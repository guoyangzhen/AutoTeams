"""长期记忆：交互摘要写入向量库 + 语义检索（PRD §5.12）。

特性：
- 任务结束后 LLM 生成交互摘要
- 摘要向量化后写入 ChromaDB（collection 前缀 memory_lt_）
- 语义检索：search(agent_id, query, top_k=5)
- DB 表 long_term_memories 记录摘要元数据 + vector_id

设计说明：
- 向量库 collection 使用 uuid5 从 agent_id 派生确定性 UUID，
  确保与 Agent 知识库 collection 隔离且符合 vector_store 命名正则
- LLM 调用前用户输入经 prompt_security.wrap_untrusted 包裹（硬约束）
"""
import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import LongTermMemory
from app.services.llm_service import llm_service, ModelTier
from app.services.prompt_security import wrap_untrusted
from app.services.vector_store import VectorStoreService, build_collection_name
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# 用于派生记忆 collection UUID 的命名空间
_MEMORY_NAMESPACE = uuid.UUID("d7ef6d11-8a06-5be3-ad1c-32a5ee087175")


def _memory_collection_name(enterprise_id: str, agent_id: str) -> str:
    """生成长期记忆专用的向量库 collection 名称。

    使用 uuid5 从 agent_id 派生确定性 UUID，确保：
    1. 与 Agent 知识库 collection 隔离（不同 UUID）
    2. 符合 vector_store 的 collection 命名正则
    3. 同一 agent 的多次调用得到相同 collection 名
    """
    memory_agent_uuid = str(uuid.uuid5(_MEMORY_NAMESPACE, agent_id))
    return build_collection_name(enterprise_id, memory_agent_uuid)


class LongTermMemoryService:
    """长期记忆服务。

    用法：
        svc = LongTermMemoryService()
        # 写入摘要
        record = await svc.add_summary(db, agent_id, enterprise_id, "客户关注测温精度，已发报价18万")
        # 语义检索
        results = await svc.search(db, agent_id, enterprise_id, "测温精度")
    """

    async def add_summary(
        self,
        db: AsyncSession,
        agent_id: str,
        enterprise_id: str,
        summary: str,
        conversation_id: Optional[str] = None,
    ) -> LongTermMemory:
        """写入交互摘要到向量库 + DB 表。

        Args:
            db: 数据库 session
            agent_id: Agent ID
            enterprise_id: 企业 ID
            summary: 交互摘要文本
            conversation_id: 关联的对话 ID（可选）

        Returns:
            LongTermMemory ORM 记录
        """
        if not summary or not summary.strip():
            raise ValueError("摘要内容不能为空")

        # 1. 写入向量库
        vector_id = str(uuid.uuid4())
        try:
            collection_name = _memory_collection_name(enterprise_id, agent_id)
            vs = await VectorStoreService.create(collection_name)
            await vs.add_documents(
                documents=[summary],
                metadatas=[{
                    "agent_id": agent_id,
                    "enterprise_id": enterprise_id,
                    "conversation_id": conversation_id or "",
                    "type": "long_term_memory",
                    "created_at": utcnow().isoformat(),
                }],
                ids=[vector_id],
            )
        except Exception as e:
            logger.warning(f"长期记忆写入向量库失败（仍记录 DB 元数据）: {e}")
            vector_id = None  # 向量库失败不阻断 DB 记录

        # 2. 写入 DB 表
        record = LongTermMemory(
            agent_id=agent_id,
            enterprise_id=enterprise_id,
            conversation_id=conversation_id,
            summary=summary,
            vector_id=vector_id,
        )
        db.add(record)
        await db.flush()
        await db.commit()
        await db.refresh(record)

        logger.info(
            f"长期记忆已写入: agent_id={agent_id}, "
            f"conversation_id={conversation_id}, vector_id={vector_id}"
        )
        return record

    async def search(
        self,
        db: AsyncSession,
        agent_id: str,
        enterprise_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        """语义检索长期记忆。

        Args:
            db: 数据库 session
            agent_id: Agent ID
            enterprise_id: 企业 ID
            query: 检索查询文本
            top_k: 返回结果数

        Returns:
            list[dict]: 每项包含 summary, score, conversation_id, created_at
        """
        if not query or not query.strip():
            return []

        results: list[dict] = []

        # 1. 向量库语义检索
        try:
            collection_name = _memory_collection_name(enterprise_id, agent_id)
            vs = await VectorStoreService.create(collection_name)
            raw_results = await vs.search(
                query=query,
                n_results=top_k,
                where={"agent_id": agent_id},
            )
            for r in raw_results:
                meta = r.get("metadata", {})
                results.append({
                    "summary": r.get("content", ""),
                    "score": 1.0 - r.get("distance", 0.0),  # ChromaDB 返回距离，转为相似度
                    "conversation_id": meta.get("conversation_id"),
                    "created_at": meta.get("created_at"),
                })
        except Exception as e:
            logger.warning(f"长期记忆向量检索失败（降级到 DB 全文检索）: {e}")
            # 降级：从 DB 表直接检索（无语义匹配，按时间倒序）
            stmt = (
                select(LongTermMemory)
                .where(LongTermMemory.agent_id == agent_id)
                .order_by(LongTermMemory.created_at.desc())
                .limit(top_k)
            )
            db_results = (await db.execute(stmt)).scalars().all()
            for r in db_results:
                results.append({
                    "summary": r.summary,
                    "score": 0.0,
                    "conversation_id": r.conversation_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })

        return results

    async def generate_summary(
        self,
        conversation_turns: list[dict],
    ) -> str:
        """LLM 生成交互摘要。

        Args:
            conversation_turns: 对话轮次列表，每项含 role/content

        Returns:
            生成的摘要文本
        """
        if not conversation_turns:
            return ""

        # 拼接对话内容（硬约束：用户输入经 wrap_untrusted 包裹）
        dialogue_text = "\n".join(
            f"[{t.get('role', 'unknown')}]: {wrap_untrusted(t.get('content', ''), '对话内容')}"
            for t in conversation_turns
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个交互摘要生成器。请将以下对话生成为一段简洁的交互摘要，"
                    "包含：关键事实、客户需求、已执行动作、待跟进事项。"
                    "摘要应在 200 字以内，使用中文。"
                ),
            },
            {
                "role": "user",
                "content": f"请为以下对话生成交互摘要：\n\n{dialogue_text}",
            },
        ]

        try:
            summary = await llm_service.chat(
                messages=messages,
                tier=ModelTier.CHEAP,  # 摘要生成用廉价模型
                max_tokens=512,
            )
            return summary.strip()
        except Exception as e:
            logger.warning(f"LLM 生成交互摘要失败: {e}")
            # 降级：返回简单拼接的摘要
            return f"交互摘要（自动生成失败）: {conversation_turns[-1].get('content', '')[:100]}"

    async def list_by_agent(
        self,
        db: AsyncSession,
        agent_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[LongTermMemory], int]:
        """列出 Agent 的长期记忆（分页）。"""
        stmt = (
            select(LongTermMemory)
            .where(LongTermMemory.agent_id == agent_id)
            .order_by(LongTermMemory.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = (await db.execute(stmt)).scalars().all()

        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(LongTermMemory).where(
            LongTermMemory.agent_id == agent_id
        )
        total = (await db.execute(count_stmt)).scalar() or 0

        return list(items), total
