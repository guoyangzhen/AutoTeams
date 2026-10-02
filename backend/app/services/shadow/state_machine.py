"""影子模式状态机与任务服务。

状态机（愿景蓝图 §4.1.1）：
    shadowing  ──record-ai──▶  evaluating  ──evaluate(match, auto_qualify)──▶  qualified
                                                                                │
    qualified  ──promote──▶  autonomous ──demote──▶ evaluating（异常降级）
    qualified  ──demote──▶  shadowing（基线不足，重训）

所有转换封装为纯函数，便于测试与复用。
"""
from typing import Optional

from sqlalchemy import case, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shadow import ShadowTask


class ShadowStateError(ValueError):
    """非法状态转换错误。"""


# 谓词：判断某状态是否允许该转换
# transition: (from_status) -> tuple[allowed_from_statuses, to_status]
TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "record_ai": (("shadowing",), "evaluating"),
    "evaluate": (("evaluating",), "qualified"),
    "promote": (("qualified",), "autonomous"),
    "demote_evaluate": (("autonomous",), "evaluating"),  # 异常降级
    "demote_shadow": (("qualified",), "shadowing"),      # 基线不足重训
}


def can_transition(task: ShadowTask, action: str) -> bool:
    """校验动作在当前状态是否合法。"""
    entry = TRANSITIONS.get(action)
    if entry is None:
        return False
    allowed_from, _ = entry
    return task.status in allowed_from


def apply_transition(task: ShadowTask, action: str) -> str:
    """应用状态转换，非法则抛 ShadowStateError。"""
    entry = TRANSITIONS.get(action)
    if entry is None:
        raise ShadowStateError(f"未知状态转换动作: {action}")
    allowed_from, to_status = entry
    if not can_transition(task, action):
        raise ShadowStateError(
            f"非法状态转换: {task.status} --{action}--> {to_status}"
        )
    task.status = to_status
    return to_status


# ============================================================
# 查询服务
# ============================================================


async def list_tasks(
    db: AsyncSession,
    enterprise_id: str,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ShadowTask], int]:
    """查询影子任务（分页 + 状态过滤）。"""
    filters = [ShadowTask.enterprise_id == enterprise_id]
    if status:
        filters.append(ShadowTask.status == status)

    count_result = await db.execute(
        select(func.count(ShadowTask.id)).where(*filters)
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(ShadowTask)
        .where(*filters)
        .order_by(ShadowTask.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), total


async def list_tasks_by_agent(
    db: AsyncSession,
    enterprise_id: str,
    agent_id: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ShadowTask], int]:
    """查询某 Agent 的影子任务（影子模式按员工维度展示）。"""
    filters = [
        ShadowTask.enterprise_id == enterprise_id,
        ShadowTask.agent_id == agent_id,
    ]
    count_result = await db.execute(
        select(func.count(ShadowTask.id)).where(*filters)
    )
    total = count_result.scalar_one()
    result = await db.execute(
        select(ShadowTask)
        .where(*filters)
        .order_by(ShadowTask.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), total


async def get_task(db: AsyncSession, task_id: str) -> Optional[ShadowTask]:
    """按 ID 获取影子任务。"""
    result = await db.execute(select(ShadowTask).where(ShadowTask.id == task_id))
    return result.scalar_one_or_none()


async def summary(db: AsyncSession, enterprise_id: str) -> dict:
    """阶段汇总：count / match / mismatch / avg_confidence。"""
    stages: list[dict] = []
    statuses = ["shadowing", "evaluating", "qualified", "autonomous"]
    for status in statuses:
        result = await db.execute(
            select(
                func.count(ShadowTask.id),
                func.sum(case((ShadowTask.eval_result == "match", 1), else_=0)),
                func.sum(case((ShadowTask.eval_result == "mismatch", 1), else_=0)),
                func.avg(ShadowTask.confidence),
            ).where(
                ShadowTask.enterprise_id == enterprise_id,
                ShadowTask.status == status,
            )
        )
        count, match_count, mismatch_count, avg_conf = result.one()
        stages.append(
            {
                "status": status,
                "count": int(count or 0),
                "match_count": int(match_count or 0),
                "mismatch_count": int(mismatch_count or 0),
                "avg_confidence": float(avg_conf) if avg_conf is not None else None,
            }
        )

    total_result = await db.execute(
        select(func.count(ShadowTask.id)).where(
            ShadowTask.enterprise_id == enterprise_id
        )
    )
    autonomous_result = await db.execute(
        select(func.count(ShadowTask.id)).where(
            ShadowTask.enterprise_id == enterprise_id,
            ShadowTask.status == "autonomous",
        )
    )
    return {
        "stages": stages,
        "autonomous_count": int(autonomous_result.scalar_one() or 0),
        "total_count": int(total_result.scalar_one() or 0),
    }
