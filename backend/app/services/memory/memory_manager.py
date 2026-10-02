"""记忆管理器：统一检索接口（PRD §5.12 记忆检索流程）。

记忆检索流程（Agent 处理任务时）：
1. 加载短期记忆（当前对话上下文）
2. 检索实体记忆（涉及哪些客户/商机/产品，获取它们的偏好与历史）
3. 语义检索长期记忆（相似历史交互的经验）
4. 综合以上三类记忆 + 当前任务 → 生成响应

记忆流转（交互发生时）：
- 短期记忆：当前对话上下文（最近 20 轮）
- 实体记忆：提取关键实体信息并更新
- 长期记忆：任务结束后生成交互摘要，写入向量库
"""
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import LongTermMemory
from app.services.memory.short_term import ShortTermMemory, DEFAULT_MAX_TURNS
from app.services.memory.long_term import LongTermMemoryService
from app.services.memory.entity_memory import EntityMemoryService

logger = logging.getLogger(__name__)


class MemoryManager:
    """统一记忆管理器：整合短期 + 实体 + 长期记忆。

    用法：
        mgr = MemoryManager()
        # 检索记忆
        memory = await mgr.retrieve(db, agent_id, enterprise_id, conversation_id)
        # 交互发生时更新短期记忆
        await mgr.update_short_term(agent_id, conversation_id, "user", "客户问SL-T100测温范围")
        # 任务结束后生成长期记忆
        await mgr.consolidate_to_long_term(db, agent_id, enterprise_id, conversation_id)
    """

    def __init__(
        self,
        max_turns: int = DEFAULT_MAX_TURNS,
        short_term: Optional[ShortTermMemory] = None,
        long_term: Optional[LongTermMemoryService] = None,
        entity_memory: Optional[EntityMemoryService] = None,
    ):
        self.short_term = short_term or ShortTermMemory(max_turns=max_turns)
        self.long_term = long_term or LongTermMemoryService()
        self.entity_memory = entity_memory or EntityMemoryService()

    async def retrieve(
        self,
        db: AsyncSession,
        agent_id: str,
        enterprise_id: str,
        conversation_id: str,
        current_task: Optional[str] = None,
        top_k: int = 5,
    ) -> dict:
        """统一检索三层记忆。

        Args:
            db: 数据库 session
            agent_id: Agent ID
            enterprise_id: 企业 ID
            conversation_id: 对话 ID
            current_task: 当前任务描述（用于语义检索长期记忆）
            top_k: 长期记忆检索结果数

        Returns:
            dict: {short_term: [...], entity: [...], long_term: [...]}
        """
        # 1. 加载短期记忆（当前对话上下文）
        st_turns = await self.short_term.get_context(agent_id, conversation_id)
        short_term_data = [
            {
                "role": t.role,
                "content": t.content,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
                "metadata": t.metadata,
            }
            for t in st_turns
        ]

        # 2. 检索实体记忆（Agent 关联的所有实体）
        entities = await self.entity_memory.get_all_entities(db, agent_id)
        entity_data = [
            {
                "id": e.id,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "attributes": e.attributes,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            }
            for e in entities
        ]

        # 3. 语义检索长期记忆（用当前任务作为检索查询）
        search_query = current_task or (
            st_turns[-1].content if st_turns else ""
        )
        long_term_data = []
        if search_query:
            long_term_data = await self.long_term.search(
                db, agent_id, enterprise_id, search_query, top_k=top_k
            )

        return {
            "short_term": short_term_data,
            "entity": entity_data,
            "long_term": long_term_data,
        }

    async def update_short_term(
        self,
        agent_id: str,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """更新短期记忆（添加一轮对话）。"""
        await self.short_term.add_turn(agent_id, conversation_id, role, content, metadata)

    async def update_entity_memory(
        self,
        db: AsyncSession,
        agent_id: str,
        enterprise_id: str,
        entity_type: str,
        entity_id: str,
        attributes: dict,
    ) -> None:
        """更新实体记忆。"""
        await self.entity_memory.upsert_entity(
            db, agent_id, enterprise_id, entity_type, entity_id, attributes
        )

    async def consolidate_to_long_term(
        self,
        db: AsyncSession,
        agent_id: str,
        enterprise_id: str,
        conversation_id: str,
        turns: Optional[list[dict]] = None,
    ) -> Optional[LongTermMemory]:
        """将短期记忆整合为长期记忆。

        流程：
        1. 获取短期记忆中的对话轮次
        2. LLM 生成交互摘要
        3. 写入向量库 + DB 表
        4. 清除短期记忆

        Args:
            db: 数据库 session
            agent_id: Agent ID
            enterprise_id: 企业 ID
            conversation_id: 对话 ID
            turns: 可选，外部传入的对话轮次（不传则从短期记忆读取）

        Returns:
            LongTermMemory 记录，失败返回 None
        """
        # 1. 获取对话轮次
        if turns is None:
            st_turns = await self.short_term.get_context(agent_id, conversation_id)
            turns = [
                {"role": t.role, "content": t.content}
                for t in st_turns
            ]

        if not turns:
            logger.info(f"无对话内容可整合: agent_id={agent_id}, conversation_id={conversation_id}")
            return None

        # 2. LLM 生成交互摘要
        summary = await self.long_term.generate_summary(turns)
        if not summary:
            logger.warning(f"摘要生成失败: agent_id={agent_id}, conversation_id={conversation_id}")
            return None

        # 3. 写入向量库 + DB 表
        record = await self.long_term.add_summary(
            db, agent_id, enterprise_id, summary, conversation_id
        )

        # 4. 清除短期记忆
        await self.short_term.clear(agent_id, conversation_id)

        logger.info(
            f"长期记忆已整合: agent_id={agent_id}, "
            f"conversation_id={conversation_id}, summary_len={len(summary)}"
        )
        return record

    async def clear_short_term(
        self,
        agent_id: str,
        conversation_id: str,
    ) -> None:
        """清除短期记忆（任务结束后调用）。"""
        await self.short_term.clear(agent_id, conversation_id)
