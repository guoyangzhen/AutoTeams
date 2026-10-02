"""AutoTeams 4.0 工作组与矩阵任务流转服务。

租户边界（AUD-03）
------------------
所有以任务为入口的服务函数都同时接收 `task_id` 与 `team_id`，并把两者一起写进
查询条件。上层 API 需先用企业边界加载 `WorkgroupTeam`，因此
"企业 -> 工作组 -> 任务" 三层都被绑定：既不能跨企业，也不能用别的团队的 URL
去操作本团队的任务。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team_matrix import WorkgroupTeam, MatrixTask, TaskSelectionBid
from app.services.matrix.bidding_engine import BiddingEngine

logger = logging.getLogger(__name__)


async def create_workgroup_team(
    db: AsyncSession,
    enterprise_id: str,
    name: str,
    leader_profile_id: str,
    member_profile_ids: List[str],
    description: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> WorkgroupTeam:
    """创建协同工作组。"""
    team = WorkgroupTeam(
        id=str(uuid.uuid4()),
        enterprise_id=enterprise_id,
        name=name,
        description=description,
        leader_profile_id=leader_profile_id,
        member_profile_ids=member_profile_ids,
        config=config or {"concurrency_limit": 3, "bid_rounds": 3, "timeout_seconds": 1800},
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return team


async def list_workgroup_teams(
    db: AsyncSession,
    enterprise_id: str,
) -> List[WorkgroupTeam]:
    """查询企业当前所有工作组。"""
    stmt = select(WorkgroupTeam).where(
        WorkgroupTeam.enterprise_id == enterprise_id,
        WorkgroupTeam.status == "active",
    ).order_by(WorkgroupTeam.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_workgroup_team(
    db: AsyncSession,
    team_id: str,
    enterprise_id: Optional[str] = None,
) -> Optional[WorkgroupTeam]:
    """查询单个工作组详情。

    传入 `enterprise_id` 时按企业过滤：跨企业访问与不存在对调用方返回同一结果，
    避免 403 确认资源真实存在（跨租户资源枚举信道）。
    """
    stmt = select(WorkgroupTeam).where(WorkgroupTeam.id == team_id)
    if enterprise_id is not None:
        stmt = stmt.where(WorkgroupTeam.enterprise_id == enterprise_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_scoped_matrix_task(
    db: AsyncSession,
    task_id: str,
    team_id: str,
) -> Optional[MatrixTask]:
    """按 `task_id + team_id` 加载任务。

    AUD-03：URL 中的 `team_id` 此前完全不参与查询，导致
    `/teams/<任意团队>/tasks/<他人任务>/review` 能改写别的企业任务。
    """
    stmt = select(MatrixTask).where(
        MatrixTask.id == task_id,
        MatrixTask.team_id == team_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_matrix_task(
    db: AsyncSession,
    team_id: str,
    title: str,
    description: str,
    parent_task_id: Optional[str] = None,
    suggested_profile_id: Optional[str] = None,
    priority: int = 1,
) -> MatrixTask:
    """向团队下发拆解任务。"""
    task = MatrixTask(
        id=str(uuid.uuid4()),
        team_id=team_id,
        parent_task_id=parent_task_id,
        title=title,
        description=description,
        suggested_profile_id=suggested_profile_id,
        priority=priority,
        status="pending",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def list_matrix_tasks(
    db: AsyncSession,
    team_id: str,
    status: Optional[str] = None,
) -> List[MatrixTask]:
    """查询团队任务清单（团队归属已由调用方按企业校验）。"""
    stmt = select(MatrixTask).where(MatrixTask.team_id == team_id)
    if status:
        stmt = stmt.where(MatrixTask.status == status)
    stmt = stmt.order_by(MatrixTask.priority.desc(), MatrixTask.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def submit_candidate_bid(
    db: AsyncSession,
    task_id: str,
    team_id: str,
    candidate_profile_id: str,
    round_num: int,
    statement: str,
    score: float,
    score_rationale: Optional[str] = None,
) -> TaskSelectionBid:
    """提交并记录一轮竞聘出价与得分。"""
    # 竞标必须作用在本工作组的任务上（AUD-03）。
    task_stmt = select(MatrixTask).where(
        MatrixTask.id == task_id,
        MatrixTask.team_id == team_id,
    )
    task_res = await db.execute(task_stmt)
    task = task_res.scalar_one_or_none()
    if task is None:
        raise ValueError(f"任务不存在或不属于该工作组: {task_id}")

    # 查找上一轮剩余 HP
    stmt = select(TaskSelectionBid).where(
        TaskSelectionBid.task_id == task_id,
        TaskSelectionBid.candidate_profile_id == candidate_profile_id,
    ).order_by(TaskSelectionBid.bid_round.desc())
    res = await db.execute(stmt)
    last_bid = res.scalars().first()

    current_hp = last_bid.current_hp if last_bid else 100.0
    eval_res = BiddingEngine.evaluate_bid_round(
        candidate_profile_id=candidate_profile_id,
        round_num=round_num,
        statement=statement,
        current_hp=current_hp,
        score=score,
        score_rationale=score_rationale,
    )

    bid = TaskSelectionBid(
        id=str(uuid.uuid4()),
        task_id=task_id,
        candidate_profile_id=candidate_profile_id,
        bid_round=round_num,
        statement=statement,
        score=score,
        score_rationale=eval_res.score_rationale,
        current_hp=eval_res.remaining_hp,
    )
    db.add(bid)

    # 标记任务进入 bidding 状态
    if task.status == "pending":
        task.status = "bidding"

    await db.commit()
    await db.refresh(bid)
    return bid


async def award_and_start_task(
    db: AsyncSession,
    task_id: str,
    team_id: str,
    winner_profile_id: str,
) -> MatrixTask:
    """中标签约定标并启动任务执行。"""
    task = await _load_scoped_task(db, task_id=task_id, team_id=team_id)

    task.assignee_profile_id = winner_profile_id
    task.status = "in_progress"
    task.version += 1
    await db.commit()
    await db.refresh(task)
    return task


async def submit_deliverable(
    db: AsyncSession,
    task_id: str,
    team_id: str,
    report_data: Dict[str, Any],
) -> MatrixTask:
    """提交任务交付报告并进入 review 状态。"""
    task = await _load_scoped_task(db, task_id=task_id, team_id=team_id)

    task.deliverable_report = report_data
    task.status = "review"
    task.version += 1
    await db.commit()
    await db.refresh(task)
    return task


async def review_deliverable(
    db: AsyncSession,
    task_id: str,
    team_id: str,
    approved: bool,
    feedback: Optional[Dict[str, Any]] = None,
) -> MatrixTask:
    """Leader 验收判定：通过 -> done；未通过 -> rework。"""
    task = await _load_scoped_task(db, task_id=task_id, team_id=team_id)

    task.review_feedback = feedback or {}
    if approved:
        task.status = "done"
    else:
        task.status = "rework"

    task.version += 1
    await db.commit()
    await db.refresh(task)
    return task


async def _load_scoped_task(
    db: AsyncSession,
    task_id: str,
    team_id: str,
) -> MatrixTask:
    """加载任务并要求其属于指定工作组，否则抛出 404 语义的 ValueError。"""
    task = await get_scoped_matrix_task(db, task_id=task_id, team_id=team_id)
    if task is None:
        raise ValueError(f"任务不存在或不属于该工作组: {task_id}")
    return task
