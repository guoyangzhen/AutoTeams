"""任务规划引擎（持久化版）。

重构说明：
之前 _plans 存在进程内存 dict 中，重启即丢、无法多 worker。
现改为基于 TaskPlan 数据库模型持久化，支持多 worker、水平扩展、Docker 重启不丢数据。
LLM 规划逻辑与 Prompt 保持不变，仅状态存储层重构。
"""
import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Optional
from enum import Enum

import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_plan import TaskPlan as TaskPlanModel
from app.services.llm_service import llm_service
from app.services.prompt_security import wrap_untrusted, safe_json_extract, SYSTEM_PROMPT_GUARDRAIL
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


PLANNING_PROMPT = """你是一个任务规划专家。用户想要完成一个目标，你需要将其分解为清晰的可执行步骤。

用户目标：{goal}

可用工具：
- folder_scanner: 扫描本地文件夹
- document_processor: 处理文档文件
- image_processor: 分析图片内容
- video_processor: 处理视频文件
- vector_store: 向量存储和检索
- llm_service: AI对话和分析
- agent_builder: 构建智能体
- skill_executor: 执行技能任务

请将目标分解为3-8个清晰的步骤，每个步骤包含：
1. 步骤标题（简洁明了）
2. 步骤描述（具体要做什么）
3. 使用的工具（从上面选择）

以JSON格式返回：
{{
    "steps": [
        {{"title": "步骤标题", "description": "详细描述", "tool": "工具名称"}},
        ...
    ],
    "estimated_time": "预计总时间",
    "suggestions": ["建议1", "建议2"]
}}

只返回JSON，不要返回其他内容。
"""


def _generate_default_steps(goal: str) -> list[dict]:
    return [
        {"title": "分析需求", "description": f"理解用户目标: {goal}", "tool": "llm_service"},
        {"title": "扫描文件", "description": "扫描指定文件夹中的文件", "tool": "folder_scanner"},
        {"title": "处理文档", "description": "提取和处理文档内容", "tool": "document_processor"},
        {"title": "构建知识库", "description": "将内容向量化并存储", "tool": "vector_store"},
        {"title": "创建智能体", "description": "基于知识库创建AI助手", "tool": "agent_builder"},
    ]


def _calculate_progress(steps: list[dict]) -> int:
    """计算完成进度百分比。"""
    if not steps:
        return 0
    completed = sum(1 for s in steps if s.get("status") == TaskStatus.COMPLETED.value)
    return int(round(completed / len(steps), 2) * 100)


class TaskPlanner:
    """任务规划器（数据库持久化版）。"""

    async def create_plan(self, goal: str, user_id: str, db: AsyncSession) -> dict:
        """创建任务计划，返回 plan dict。"""
        # 调用 LLM 分解目标
        steps_data: list[dict] = []
        suggestions: list[str] = []

        # P0-08: 包裹用户输入
        safe_goal = wrap_untrusted(goal, "用户目标")
        prompt = PLANNING_PROMPT.format(goal=safe_goal)
        try:
            response = await llm_service.chat([
                {"role": "system", "content": SYSTEM_PROMPT_GUARDRAIL},
                {"role": "user", "content": prompt}
            ])
            # P0-08: 使用 safe_json_extract
            data = safe_json_extract(response)
            if data is None:
                data = {"steps": _generate_default_steps(goal)}

            for i, step_data in enumerate(data.get("steps", [])):
                steps_data.append({
                    "step_id": f"step-{i+1}",
                    "title": step_data.get("title", f"步骤 {i+1}"),
                    "description": step_data.get("description", ""),
                    "tool": step_data.get("tool", ""),
                    "status": TaskStatus.PENDING.value,
                    "started_at": None,
                    "completed_at": None,
                    "output": None,
                    "error": None,
                })
            suggestions = data.get("suggestions", [])
        except (httpx.HTTPError, ValueError, RuntimeError, json.JSONDecodeError) as e:
            logger.error(f"LLM 规划失败，使用默认步骤: {e}", exc_info=True)
            for i, step_data in enumerate(_generate_default_steps(goal)):
                steps_data.append({
                    "step_id": f"step-{i+1}",
                    "title": step_data["title"],
                    "description": step_data["description"],
                    "tool": step_data.get("tool", ""),
                    "status": TaskStatus.PENDING.value,
                    "started_at": None,
                    "completed_at": None,
                    "output": None,
                    "error": None,
                })
        except (TypeError, KeyError, AttributeError, OSError) as e:
            errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
            logger.error(f"LLM 规划失败（未预期错误）: {e}", exc_info=True)
            raise RuntimeError(f"LLM 规划失败: {e}") from e

        # 持久化到数据库
        plan = TaskPlanModel(
            user_id=user_id,
            goal=goal,
            steps=steps_data,
            status=TaskStatus.PENDING.value,
            progress=0,
        )
        db.add(plan)
        await db.flush()
        await db.commit()

        result = plan.to_dict()
        result["suggestions"] = suggestions
        return result

    async def get_plan(self, plan_id: str, db: AsyncSession) -> Optional[dict]:
        stmt = select(TaskPlanModel).where(TaskPlanModel.id == plan_id)
        result = await db.execute(stmt)
        plan = result.scalar_one_or_none()
        if not plan:
            return None
        return plan.to_dict()

    async def list_plans(
        self, user_id: str, db: AsyncSession, limit: int = 50
    ) -> list[dict]:
        stmt = (
            select(TaskPlanModel)
            .where(TaskPlanModel.user_id == user_id)
            .order_by(TaskPlanModel.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return [p.to_dict() for p in result.scalars().all()]

    async def start_step(
        self, plan_id: str, step_id: str, db: AsyncSession
    ) -> Optional[dict]:
        stmt = select(TaskPlanModel).where(TaskPlanModel.id == plan_id)
        result = await db.execute(stmt)
        plan = result.scalar_one_or_none()
        if not plan:
            return None

        steps = deepcopy(plan.steps or [])
        updated = False
        for step in steps:
            if step.get("step_id") == step_id:
                step["status"] = TaskStatus.RUNNING.value
                step["started_at"] = datetime.now(timezone.utc).isoformat()
                updated = True
                break

        if updated:
            plan.status = TaskStatus.RUNNING.value
            plan.steps = steps
            await db.flush()
            await db.commit()
            return next((s for s in steps if s.get("step_id") == step_id), None)
        return None

    async def complete_step(
        self, plan_id: str, step_id: str, output: str, db: AsyncSession
    ) -> Optional[dict]:
        stmt = select(TaskPlanModel).where(TaskPlanModel.id == plan_id)
        result = await db.execute(stmt)
        plan = result.scalar_one_or_none()
        if not plan:
            return None

        steps = deepcopy(plan.steps or [])
        updated_step = None
        for step in steps:
            if step.get("step_id") == step_id:
                step["status"] = TaskStatus.COMPLETED.value
                step["completed_at"] = datetime.now(timezone.utc).isoformat()
                step["output"] = output
                updated_step = step
                break

        if updated_step:
            # 检查是否全部完成
            if all(s.get("status") == TaskStatus.COMPLETED.value for s in steps):
                plan.status = TaskStatus.COMPLETED.value
            plan.progress = _calculate_progress(steps)
            plan.steps = steps
            await db.flush()
            await db.commit()
            return updated_step
        return None

    async def fail_step(
        self, plan_id: str, step_id: str, error: str, db: AsyncSession
    ) -> Optional[dict]:
        stmt = select(TaskPlanModel).where(TaskPlanModel.id == plan_id)
        result = await db.execute(stmt)
        plan = result.scalar_one_or_none()
        if not plan:
            return None

        steps = deepcopy(plan.steps or [])
        updated_step = None
        for step in steps:
            if step.get("step_id") == step_id:
                step["status"] = TaskStatus.FAILED.value
                step["error"] = error
                updated_step = step
                break

        if updated_step:
            plan.steps = steps
            await db.flush()
            await db.commit()
            return updated_step
        return None


task_planner = TaskPlanner()
