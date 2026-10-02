"""设置向导对话服务（持久化版）。

重构说明：
之前 _sessions 存在进程内存 dict 中，重启即丢。
现改为基于 SetupSession 数据库模型持久化。
LLM 对话逻辑与 Prompt 保持不变，仅状态存储层重构。
"""
import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.setup_session import SetupSession as SetupSessionModel
from app.services.llm_service import llm_service
from app.services.folder_scanner import scan_folder
from app.services.prompt_security import wrap_untrusted, safe_json_extract, SYSTEM_PROMPT_GUARDRAIL, truncate
from app.utils.metrics import errors_total
# BE-SEC-02: 统一错误码
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """你是一个企业知识管理顾问。用户想要创建一个AI知识助手来处理他们的企业文件。

当前文件夹信息：
{folder_info}

用户的最新消息：
{user_message}

之前的对话历史：
{history}

请根据以上信息：
1. 分析文件夹内容的特征和用途
2. 向用户提出澄清问题，了解他们对AI助手的期望
3. 根据对话历史，逐步完善对需求的理解

回复要求：
- 用中文回答
- 简洁明了，不超过200字
- 每次最多问2个问题
- 如果信息已经足够，可以进入方案生成阶段
"""

PLAN_PROMPT = """你是一个企业知识管理顾问。根据以下对话信息，生成一个AI知识助手的配置方案。

企业信息：
{enterprise_info}

文件夹信息：
{folder_info}

对话历史摘要：
{summary}

请生成一个JSON格式的方案，包含以下字段：
{{
    "agent_name": "建议的助手名称",
    "agent_description": "助手的功能描述",
    "knowledge_structure": {{
        "categories": ["知识分类1", "知识分类2"],
        "description": "知识库结构说明"
    }},
    "processing_config": {{
        "include_patterns": ["要处理的文件类型"],
        "exclude_patterns": ["要排除的文件类型"],
        "chunk_strategy": "分块策略"
    }},
    "estimated_time": 预估处理时间秒数,
    "suggested_questions": ["建议的用户问题1", "建议的用户问题2"]
}}

只返回JSON，不要返回其他内容。
"""


class SetupDialogueService:
    """设置向导对话服务（数据库持久化版）。"""

    async def start_session(
        self,
        folder_path: str,
        enterprise_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> dict:
        """启动设置会话，返回会话信息 dict。"""
        folder_info = ""
        try:
            files = scan_folder(folder_path)
            type_counts: dict[str, int] = {}
            total_size = 0
            for f in files:
                type_counts[f.file_type] = type_counts.get(f.file_type, 0) + 1
                total_size += f.size

            folder_info = (
                f"共发现 {len(files)} 个文件，总大小 {total_size / 1024 / 1024:.1f}MB\n"
                + "文件类型分布：" + ", ".join(f"{t}({c}个)" for t, c in type_counts.items())
            )
        except (FileNotFoundError, PermissionError, OSError, ValueError) as e:
            logger.error(f"扫描文件夹失败: {e}", exc_info=True)
            # BE-SEC-02: folder_info 会进入会话消息并返回给客户端，避免暴露原始异常
            folder_info = f"文件夹扫描失败: {ErrorCode.FOLDER_PATH_INVALID}"
        except (RuntimeError, TypeError, KeyError, AttributeError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"扫描文件夹失败（未预期错误）: {e}", exc_info=True)
            raise RuntimeError(f"扫描文件夹失败: {folder_path}") from e

        first_message = (
            f"我已扫描文件夹 {folder_path}。\n{folder_info}\n"
            "请问您希望这个AI助手主要解决什么问题？"
        )

        messages_data = [
            {"role": "assistant", "content": first_message, "timestamp": None}
        ]

        session = SetupSessionModel(
            user_id=user_id,
            enterprise_id=enterprise_id,
            folder_path=folder_path,
            messages=messages_data,
            plan=None,
            status="active",
            confirmed=False,
        )
        db.add(session)
        await db.flush()
        await db.commit()

        return {
            "session_id": session.id,
            "message": first_message,
            "folder_info": folder_info,
        }

    async def send_message(
        self, session_id: str, user_message: str, db: AsyncSession
    ) -> dict:
        """发送消息，返回回复 dict。"""
        stmt = select(SetupSessionModel).where(SetupSessionModel.id == session_id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if not session:
            raise ValueError("会话不存在或已过期")

        messages = list(session.messages or [])
        messages.append({
            "role": "user",
            "content": user_message,
            "timestamp": None,
        })

        history_str = "\n".join(
            f"{m['role']}: {m['content']}" for m in messages[-10:]
        )

        # 提取 folder_info（从首条 assistant 消息中获取）
        folder_info = "未知"
        if messages and messages[0].get("role") == "assistant":
            folder_info = messages[0]["content"]

        # P0-08: 包裹不可信内容，并截断过长输入
        safe_folder = wrap_untrusted(truncate(folder_info, 2000), "文件夹信息")
        safe_user_msg = wrap_untrusted(truncate(user_message, 8000), "用户消息")
        safe_history = wrap_untrusted(truncate(history_str, 16000), "对话历史")

        prompt = ANALYSIS_PROMPT.format(
            folder_info=safe_folder,
            user_message=safe_user_msg,
            history=safe_history,
        )

        reply = await llm_service.chat([
            {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
            {"role": "user", "content": prompt}
        ])

        messages.append({
            "role": "assistant",
            "content": reply,
            "timestamp": None,
        })
        session.messages = messages
        await db.flush()
        await db.commit()

        return {
            "session_id": session.id,
            "message": reply,
            "history_length": len(messages),
        }

    async def get_plan(self, session_id: str, db: AsyncSession) -> dict:
        """获取或生成方案。"""
        stmt = select(SetupSessionModel).where(SetupSessionModel.id == session_id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if not session:
            raise ValueError("会话不存在或已过期")

        if session.plan:
            plan = session.plan
            if isinstance(plan, str):
                plan = json.loads(plan)
            return plan

        messages = list(session.messages or [])
        history_summary = "\n".join(
            f"{m['role']}: {m['content']}" for m in messages
        )

        folder_info = "未知"
        if messages and messages[0].get("role") == "assistant":
            folder_info = messages[0]["content"]

        # P0-08: 包裹不可信内容
        safe_folder = wrap_untrusted(truncate(folder_info, 2000), "文件夹信息")
        safe_summary = wrap_untrusted(truncate(history_summary, 16000), "对话摘要")

        prompt = PLAN_PROMPT.format(
            enterprise_info=str(session.enterprise_id),
            folder_info=safe_folder,
            summary=safe_summary,
        )

        plan_text = await llm_service.chat([
            {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
            {"role": "user", "content": prompt}
        ])

        # P0-08: 使用 safe_json_extract
        plan = safe_json_extract(plan_text)
        if plan is None:
            plan = {"raw_plan": plan_text}

        # 对齐 AgentBuildState 字段（name/description/system_prompt/folder_path），
        # 便于 confirm_plan auto_apply 时直接注入 LangGraph initial_state
        plan.setdefault("folder_path", session.folder_path)
        plan.setdefault("name", plan.get("agent_name") or "知识助手")
        plan.setdefault("description", plan.get("description") or "")
        plan.setdefault("system_prompt", plan.get("system_prompt") or "")

        plan["session_id"] = session.id
        session.plan = plan
        await db.flush()
        await db.commit()
        return plan

    async def confirm_plan(
        self,
        session_id: str,
        confirmed: bool,
        modifications: Optional[dict],
        db: AsyncSession,
    ) -> dict:
        """确认或修改方案。"""
        stmt = select(SetupSessionModel).where(SetupSessionModel.id == session_id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if not session:
            raise ValueError("会话不存在或已过期")

        if not session.plan:
            session.plan = await self.get_plan(session_id, db)

        plan = session.plan
        if isinstance(plan, str):
            plan = json.loads(plan)

        if modifications:
            plan.update(modifications)
            session.plan = plan

        session.status = "confirmed" if confirmed else "rejected"
        session.confirmed = confirmed
        await db.flush()
        await db.commit()

        return {
            "session_id": session.id,
            "status": session.status,
            "plan": plan,
        }

    async def get_session(
        self, session_id: str, db: AsyncSession
    ) -> Optional[dict]:
        """获取会话详情。"""
        stmt = select(SetupSessionModel).where(SetupSessionModel.id == session_id)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if not session:
            return None
        return session.to_dict()


setup_dialogue_service = SetupDialogueService()
