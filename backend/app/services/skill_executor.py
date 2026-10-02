import logging
import time
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.models.skill_execution import SkillExecution
from app.models.agent import Agent
from app.models.user import User
from app.services.llm_service import llm_service
from app.services.prompt_security import wrap_untrusted, SYSTEM_PROMPT_GUARDRAIL
from app.utils.metrics import errors_total
# BE-SEC-02: 统一错误码
from app.utils.error_codes import ErrorCode

logger = logging.getLogger(__name__)


class SkillExecutor:
    def __init__(self):
        self._handlers: dict[str, Any] = {
            "text_generation": self._handle_text_generation,
            "text_summarization": self._handle_text_summarization,
            "text_classification": self._handle_text_classification,
            "data_extraction": self._handle_data_extraction,
            "translation": self._handle_translation,
            "code_generation": self._handle_code_generation,
            "custom": self._handle_custom,
            # B6: 低风险自动放行类型（与 custom 同语义，基于 prompt_template 生成）
            "search": self._handle_custom,
            "lookup": self._handle_custom,
            "summary": self._handle_text_summarization,
        }

    async def execute_skill(
        self,
        db: AsyncSession,
        skill_id: str,
        input_data: dict,
        user_id: str,
        agent_id: Optional[str] = None,
        is_review_run: bool = False,
    ) -> dict:
        """执行技能并持久化执行记录。

        P2-1: 每次执行都写入 SkillExecution 表，包括成功和失败两种情况。
        成功时 status="success"，失败时 status="failed" 并记录 error_message。
        P0-05-B: 服务层二次校验 user → skill → agent 归属（defense in depth），
                 即使 API 层校验被绕过，服务层仍能拒绝越权执行。
        P1-SKILL: 非沙箱试运行时，只有 status="approved" 的技能才可执行。
        """
        # P0-05-B: 通过 JOIN Agent 一次性获取 skill 与所属 agent，避免二次查询
        skill_result = await db.execute(
            select(Skill).join(Agent, Skill.agent_id == Agent.id).where(
                Skill.id == skill_id
            )
        )
        skill = skill_result.scalar_one_or_none()
        if not skill:
            raise ValueError(f"技能不存在: {skill_id}")

        # P1-SKILL: 运行时执行必须已审批；沙箱试运行除外
        if not is_review_run and skill.status != "approved":
            raise PermissionError(
                f"技能未通过审批，无法执行: {skill_id} (status={skill.status})"
            )

        # P0-05-B: 二次校验 — 拉取 user 的 enterprise_id，校验 agent 归属
        # 超级管理员（enterprise_id=None）跳过校验
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if user and user.enterprise_id:
            # 重新查询 skill 关联的 agent（skill_result 中已 JOIN 但 ORM 未加载关联对象）
            agent_result = await db.execute(
                select(Agent).where(Agent.id == skill.agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if not agent or agent.enterprise_id != user.enterprise_id:
                # 越权：skill 不属于当前用户的企业
                raise PermissionError(f"无权执行此技能: {skill_id}")

        handler = self._handlers.get(skill.skill_type)
        if not handler:
            raise ValueError(f"不支持的技能类型: {skill.skill_type}")

        # 使用 skill 自身的 agent_id 作为默认（如果调用方未传入）
        effective_agent_id = agent_id or skill.agent_id
        start_time = time.monotonic()
        status = "success"
        error_message = None
        output: dict = {}

        try:
            output = await handler(skill, input_data)
        except (httpx.HTTPError, ValueError, RuntimeError, PermissionError) as e:
            status = "failed"
            # BE-SEC-02: error_message 会通过 API 返回，使用统一错误码
            error_message = ErrorCode.SKILL_NOT_FOUND_OR_INVALID
            logger.error(f"技能执行失败 skill_id={skill_id}: {e}", exc_info=True)
            raise
        except (TypeError, KeyError, AttributeError, OSError) as e:
            status = "failed"
            error_message = ErrorCode.SKILL_NOT_FOUND_OR_INVALID
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"技能执行失败（未预期错误） skill_id={skill_id}: {e}", exc_info=True)
            raise
        finally:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            # P2-1: 持久化执行记录（无论成功或失败）
            try:
                execution = SkillExecution(
                    skill_id=str(skill_id),
                    user_id=user_id,
                    agent_id=effective_agent_id,
                    input_data=input_data,
                    output_data=output if status == "success" else None,
                    execution_time_ms=elapsed_ms,
                    status=status,
                    error_message=error_message,
                    is_review_run=is_review_run,
                )
                db.add(execution)
                await db.flush()
                await db.commit()
                await db.refresh(execution)
            except SQLAlchemyError as commit_err:
                errors_total.labels(module=__name__, exception_type=type(commit_err).__name__).inc()
                logger.error(f"持久化执行记录数据库失败: {commit_err}", exc_info=True)
                # 持久化失败不影响返回结果
            except (RuntimeError, OSError, TypeError, ValueError, KeyError, AttributeError) as commit_err:
                errors_total.labels(module=__name__, exception_type=type(commit_err).__name__).inc()
                logger.error(f"持久化执行记录失败（未预期错误）: {commit_err}", exc_info=True)
                # 持久化失败不影响返回结果

        return {
            "skill_id": str(skill_id),
            "output_data": output,
            "execution_time_ms": elapsed_ms,
            "status": status,
        }

    async def _handle_text_generation(self, skill: Skill, input_data: dict) -> dict:
        prompt = skill.config.get("prompt_template", "请根据以下内容生成文本：\n{input}")
        user_input = input_data.get("text", "")
        # P0-08: 用 wrap_untrusted 包裹用户输入，防止 prompt injection
        safe_input = wrap_untrusted(user_input, "用户输入")
        formatted_prompt = prompt.replace("{input}", safe_input)

        messages = [
            {"role": "system", "content": (skill.description or "") + "\n\n" + SYSTEM_PROMPT_GUARDRAIL},
            {"role": "user", "content": formatted_prompt},
        ]
        result = await llm_service.chat(messages)
        return {"generated_text": result}

    async def _handle_text_summarization(self, skill: Skill, input_data: dict) -> dict:
        text = input_data.get("text", "")
        max_length = input_data.get("max_length", 500)
        # P0-08: 包裹用户输入
        safe_text = wrap_untrusted(text, "待总结文本")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
            {
                "role": "user",
                "content": f"请将以下文本总结为不超过{max_length}字的摘要：\n\n{safe_text}",
            }
        ]
        summary = await llm_service.chat(messages)
        return {"summary": summary}

    async def _handle_text_classification(self, skill: Skill, input_data: dict) -> dict:
        text = input_data.get("text", "")
        categories = skill.config.get("categories", [])

        cat_str = "、".join(categories) if categories else "自动分类"
        # P0-08: 包裹用户输入
        safe_text = wrap_untrusted(text, "待分类文本")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
            {
                "role": "user",
                "content": f"请将以下文本分类到以下类别之一：{cat_str}\n\n文本：{safe_text}\n\n请只返回类别名称。",
            }
        ]
        classification = await llm_service.chat(messages)
        return {"classification": classification.strip()}

    async def _handle_data_extraction(self, skill: Skill, input_data: dict) -> dict:
        text = input_data.get("text", "")
        fields = skill.config.get("fields", [])

        fields_str = "、".join(fields) if fields else "关键信息"
        # P0-08: 包裹用户输入
        safe_text = wrap_untrusted(text, "待提取文本")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
            {
                "role": "user",
                "content": f"请从以下文本中提取以下信息：{fields_str}\n\n以JSON格式返回。\n\n文本：{safe_text}",
            }
        ]
        result = await llm_service.chat(messages)
        return {"extracted_data": result}

    async def _handle_translation(self, skill: Skill, input_data: dict) -> dict:
        text = input_data.get("text", "")
        target_lang = input_data.get("target_language", skill.config.get("target_language", "英文"))
        # P0-08: 包裹用户输入
        safe_text = wrap_untrusted(text, "待翻译文本")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
            {
                "role": "user",
                "content": f"请将以下文本翻译为{target_lang}：\n\n{safe_text}",
            }
        ]
        translation = await llm_service.chat(messages)
        return {"translation": translation}

    async def _handle_code_generation(self, skill: Skill, input_data: dict) -> dict:
        description = input_data.get("description", "")
        language = input_data.get("language", skill.config.get("language", "Python"))
        # P0-08: 包裹用户输入
        safe_desc = wrap_untrusted(description, "功能描述")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
            {
                "role": "user",
                "content": f"请用{language}编写以下功能的代码：\n\n{safe_desc}\n\n只返回代码，不要解释。",
            }
        ]
        code = await llm_service.chat(messages)
        return {"code": code, "language": language}

    async def _handle_custom(self, skill: Skill, input_data: dict) -> dict:
        prompt_template = skill.config.get("prompt_template", "{input}")
        user_input = input_data.get("text", str(input_data))
        # P0-08: 包裹用户输入
        safe_input = wrap_untrusted(user_input, "用户输入")
        prompt = prompt_template.replace("{input}", safe_input)

        messages = [
            {"role": "system", "content": (skill.description or "") + "\n\n" + SYSTEM_PROMPT_GUARDRAIL},
            {"role": "user", "content": prompt},
        ]
        result = await llm_service.chat(messages)
        return {"result": result}

    # ============================================================
    # B6: Skill 链式编排执行
    # ============================================================

    # 链式编排安全阈值：避免循环引用导致无限执行
    _MAX_CHAIN_LENGTH = 10

    async def execute_chain(
        self,
        db: AsyncSession,
        skill_id: str,
        input_data: dict,
        user_id: str,
        agent_id: Optional[str] = None,
        is_review_run: bool = False,
    ) -> dict:
        """B6: 链式执行 Skill。

        从起始 skill 开始，依次读取 next_skill_id 串联下游 skill：
        - 上一步 output_data 作为下一步 input_data（自动提取首字符串字段作 text）
        - 聚合每步输出为最终结构化响应
        - 遇到 None / 已访问节点 / 超过 _MAX_CHAIN_LENGTH 时终止

        与 execute_skill 的关系：
        - execute_chain 内部对每个节点调用 execute_skill，保留每步执行记录写入 SkillExecution 表
        - 单步失败时整体链终止，已执行步骤的记录保留

        Args:
            db: AsyncSession
            skill_id: 链起始 Skill ID
            input_data: 起始输入
            user_id: 调用方用户 ID
            agent_id: 可选，覆盖默认 agent_id
            is_review_run: 是否沙箱试运行（不强制 status=approved）

        Returns:
            {
                "chain_started": str,        # 链起始 skill_id
                "steps": list[dict],         # 每步的 {skill_id, skill_name, output_data, status, execution_time_ms}
                "aggregated_output": dict,   # 聚合输出（最后一步的 output 作为主结果 + chain 字段记录全链）
                "status": str,               # overall: success / partial / failed
                "total_steps": int,
                "total_time_ms": int,
            }
        """
        from app.models.skill import Skill as SkillModel

        visited: set[str] = set()
        current_skill_id: Optional[str] = skill_id
        current_input: dict = input_data
        steps: list[dict] = []
        chain_failed = False
        overall_status = "success"
        total_time_ms = 0

        while current_skill_id and not chain_failed:
            # 环检测
            if current_skill_id in visited:
                logger.warning(
                    f"B6: 链式执行检测到循环引用 {current_skill_id}，提前终止"
                )
                overall_status = "partial"
                break
            if len(visited) >= self._MAX_CHAIN_LENGTH:
                logger.warning(
                    f"B6: 链式执行超过最大长度 {self._MAX_CHAIN_LENGTH}，提前终止"
                )
                overall_status = "partial"
                break
            visited.add(current_skill_id)

            # 执行当前 skill
            try:
                step_result = await self.execute_skill(
                    db=db,
                    skill_id=current_skill_id,
                    input_data=current_input,
                    user_id=user_id,
                    agent_id=agent_id,
                    is_review_run=is_review_run,
                )
            except (httpx.HTTPError, ValueError, RuntimeError, PermissionError) as e:
                logger.error(
                    f"B6: 链式执行第 {len(steps) + 1} 步失败 skill_id={current_skill_id}: {e}",
                    exc_info=True,
                )
                steps.append({
                    "skill_id": current_skill_id,
                    "skill_name": None,
                    "output_data": {},
                    "status": "failed",
                    "error": ErrorCode.SKILL_NOT_FOUND_OR_INVALID,
                    "execution_time_ms": 0,
                })
                chain_failed = True
                overall_status = "failed"
                break

            # 拉取 skill 元信息（用于链尾 next_skill_id 与 name 展示）
            skill_meta_result = await db.execute(
                select(SkillModel).where(SkillModel.id == current_skill_id)
            )
            skill_meta = skill_meta_result.scalar_one_or_none()
            step_name = skill_meta.name if skill_meta else None
            next_skill_id = skill_meta.next_skill_id if skill_meta else None

            steps.append({
                "skill_id": current_skill_id,
                "skill_name": step_name,
                "output_data": step_result.get("output_data", {}),
                "status": step_result.get("status", "success"),
                "execution_time_ms": step_result.get("execution_time_ms", 0),
            })
            total_time_ms += step_result.get("execution_time_ms", 0)

            # 准备下一步输入：从当前 output 中提取首字符串字段作 text
            current_input = self._derive_next_input(step_result.get("output_data", {}))
            current_skill_id = next_skill_id

        # 聚合输出：最后一步的 output 作为主结果 + chain 元信息
        last_step = steps[-1] if steps else None
        aggregated_output: dict = {
            "final_output": last_step["output_data"] if last_step else {},
            "chain": [
                {"skill_id": s["skill_id"], "skill_name": s["skill_name"], "status": s["status"]}
                for s in steps
            ],
            "total_steps": len(steps),
        }

        return {
            "chain_started": skill_id,
            "steps": steps,
            "aggregated_output": aggregated_output,
            "status": overall_status,
            "total_steps": len(steps),
            "total_time_ms": total_time_ms,
        }

    @staticmethod
    def _derive_next_input(prev_output: dict) -> dict:
        """从上一步 output_data 提取首字符串字段，作为下一步 input_data.text。

        约定：
        - 各 handler 输出均为 dict，含单个字符串值（如 {"generated_text": "..."} / {"summary": "..."}）
        - 链式下游 skill 通常接受 {"text": "..."} 输入
        - 找不到字符串字段时退化为 str(prev_output)
        """
        if not isinstance(prev_output, dict) or not prev_output:
            return {"text": str(prev_output)}
        for v in prev_output.values():
            if isinstance(v, str) and v:
                return {"text": v}
        return {"text": str(prev_output)}


skill_executor = SkillExecutor()
