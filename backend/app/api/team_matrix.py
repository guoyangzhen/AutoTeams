"""AutoTeams 4.0 矩阵团队协同、竞标与黑板 API 路由。

租户边界（AUD-03）
------------------
本模块历史上只对 `GET /teams/{team_id}` 做了归属校验，任务、黑板等子路由
直接按 URL 里的 ID 查询，导致：

* 企业 A 的普通成员可以 `GET /teams/<B 的团队>/tasks` 读到 B 的任务；
* 更严重的是 `POST /teams/<任意团队>/tasks/<B 的任务>/review` 能把 B 的任务
  改成 `done`，且 URL 中的团队甚至无需与任务所属团队一致。

现在所有子路由都通过 `_load_team` / `_load_task` 两个依赖进入：
先按 `id + enterprise_id` 加载工作组，再按 `task_id + team_id` 加载任务。
跨企业与不存在都返回 404，调用方无法区分两者。

角色门禁：创建工作组、下发任务、授标、验收属于 Leader/管理员决策，
统一要求企业管理员；读取看板与黑板、提交交付物对本企业成员开放。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.team_matrix import MatrixTask, WorkgroupTeam
from app.models.user import User
from app.services.matrix.team_service import (
    create_workgroup_team,
    list_workgroup_teams,
    get_workgroup_team,
    get_scoped_matrix_task,
    create_matrix_task,
    list_matrix_tasks,
    submit_candidate_bid,
    award_and_start_task,
    submit_deliverable,
    review_deliverable,
)
from app.services.matrix.blackboard_service import (
    post_blackboard_entry,
    list_blackboard_entries,
)
from app.utils.security import get_current_user
from app.utils.tenant_scope import (
    ROLE_ADMIN,
    assert_role,
    not_found,
    require_enterprise_bound,
)
from app.utils.response import success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["AutoTeams Team-Matrix"])

TEAM_NOT_FOUND = "工作组不存在"
TASK_NOT_FOUND = "任务不存在"


async def _load_team(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WorkgroupTeam:
    """加载并授权当前用户所属企业的工作组。"""
    require_enterprise_bound(current_user)
    team = await get_workgroup_team(
        db,
        team_id=team_id,
        enterprise_id=current_user.enterprise_id,
    )
    if team is None:
        raise not_found(TEAM_NOT_FOUND)
    return team


async def _load_task(
    team_id: str,
    task_id: str,
    team: WorkgroupTeam = Depends(_load_team),
    db: AsyncSession = Depends(get_db),
) -> MatrixTask:
    """加载并授权属于 `team_id` 的任务（企业 → 工作组 → 任务 三层绑定）。"""
    task = await get_scoped_matrix_task(db, task_id=task_id, team_id=team.id)
    if task is None:
        raise not_found(TASK_NOT_FOUND)
    return task


async def _require_team_leader(
    current_user: User = Depends(get_current_user),
) -> User:
    """创建工作组 / 下发任务 / 授标 / 验收需要企业管理员权限。"""
    require_enterprise_bound(current_user)
    return assert_role(current_user, ROLE_ADMIN)


class CreateTeamRequest(BaseModel):
    name: str
    leader_profile_id: str
    member_profile_ids: List[str]
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class CreateTaskRequest(BaseModel):
    title: str
    description: str
    parent_task_id: Optional[str] = None
    suggested_profile_id: Optional[str] = None
    priority: int = 1


class SubmitBidRequest(BaseModel):
    candidate_profile_id: str
    bid_round: int
    statement: str
    score: float
    score_rationale: Optional[str] = None


class AwardTaskRequest(BaseModel):
    winner_profile_id: str


class DeliverTaskRequest(BaseModel):
    report_data: Dict[str, Any]


class ReviewTaskRequest(BaseModel):
    approved: bool
    feedback: Optional[Dict[str, Any]] = None


class PostBlackboardRequest(BaseModel):
    topic: str
    content: str
    source_profile_id: str
    source_task_id: Optional[str] = None
    citations: Optional[List[Dict[str, Any]]] = None
    is_pinned: bool = False


@router.get("", response_model=None)
async def list_teams(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取企业当前工作组列表。"""
    require_enterprise_bound(current_user)
    teams = await list_workgroup_teams(db, enterprise_id=current_user.enterprise_id)
    data = [{
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "leader_profile_id": t.leader_profile_id,
        "member_profile_ids": t.member_profile_ids,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in teams]
    return success_response(data=data)


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: CreateTeamRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建新的多员工项目协同工作组（企业管理员）。"""
    require_enterprise_bound(current_user)
    assert_role(current_user, ROLE_ADMIN)
    team = await create_workgroup_team(
        db,
        enterprise_id=current_user.enterprise_id,
        name=payload.name,
        leader_profile_id=payload.leader_profile_id,
        member_profile_ids=payload.member_profile_ids,
        description=payload.description,
        config=payload.config,
    )
    return success_response(data={"id": team.id, "name": team.name}, message="工作组创建成功")


@router.get("/{team_id}", response_model=None)
async def get_team_detail(
    team: WorkgroupTeam = Depends(_load_team),
):
    """查询工作组详情（依赖已完成企业归属校验）。"""
    return success_response(data={
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "leader_profile_id": team.leader_profile_id,
        "member_profile_ids": team.member_profile_ids,
        "config": team.config,
        "status": team.status,
    })


@router.get("/{team_id}/tasks", response_model=None)
async def list_tasks(
    team_id: str,
    status: Optional[str] = Query(None),
    team: WorkgroupTeam = Depends(_load_team),
    db: AsyncSession = Depends(get_db),
):
    """获取工作组任务看板列表。"""
    tasks = await list_matrix_tasks(db, team_id=team.id, status=status)
    data = [{
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "assignee_profile_id": t.assignee_profile_id,
        "priority": t.priority,
        "deliverable_report": t.deliverable_report,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in tasks]
    return success_response(data=data)


@router.post("/{team_id}/tasks", response_model=None, status_code=status.HTTP_201_CREATED)
async def create_task(
    team_id: str,
    payload: CreateTaskRequest,
    team: WorkgroupTeam = Depends(_load_team),
    _leader: User = Depends(_require_team_leader),
    db: AsyncSession = Depends(get_db),
):
    """向工作组下发新任务（Leader/管理员决策）。"""
    task = await create_matrix_task(
        db,
        team_id=team.id,
        title=payload.title,
        description=payload.description,
        parent_task_id=payload.parent_task_id,
        suggested_profile_id=payload.suggested_profile_id,
        priority=payload.priority,
    )
    return success_response(data={"id": task.id, "title": task.title, "status": task.status}, message="任务下发成功")


@router.post("/{team_id}/tasks/{task_id}/bid", response_model=None)
async def submit_bid(
    team_id: str,
    task_id: str,
    payload: SubmitBidRequest,
    task: MatrixTask = Depends(_load_task),
    db: AsyncSession = Depends(get_db),
):
    """候选数字员工提交竞聘轮次陈述并计分。"""
    try:
        bid = await submit_candidate_bid(
            db,
            task_id=task.id,
            team_id=task.team_id,
            candidate_profile_id=payload.candidate_profile_id,
            round_num=payload.bid_round,
            statement=payload.statement,
            score=payload.score,
            score_rationale=payload.score_rationale,
        )
    except ValueError as exc:
        raise not_found(TASK_NOT_FOUND) from exc
    return success_response(data={
        "id": bid.id,
        "round": bid.bid_round,
        "current_hp": bid.current_hp,
        "statement": bid.statement,
    }, message="竞聘轮次记录已提交")


@router.post("/{team_id}/tasks/{task_id}/award", response_model=None)
async def award_task(
    team_id: str,
    task_id: str,
    payload: AwardTaskRequest,
    task: MatrixTask = Depends(_load_task),
    _leader: User = Depends(_require_team_leader),
    db: AsyncSession = Depends(get_db),
):
    """中标签约定标并启动任务（Leader/管理员决策）。"""
    try:
        awarded = await award_and_start_task(
            db,
            task_id=task.id,
            team_id=task.team_id,
            winner_profile_id=payload.winner_profile_id,
        )
    except ValueError as exc:
        raise not_found(TASK_NOT_FOUND) from exc
    return success_response(
        data={"id": awarded.id, "assignee": awarded.assignee_profile_id, "status": awarded.status},
        message="任务定标启动成功",
    )


@router.post("/{team_id}/tasks/{task_id}/deliver", response_model=None)
async def deliver_task(
    team_id: str,
    task_id: str,
    payload: DeliverTaskRequest,
    task: MatrixTask = Depends(_load_task),
    db: AsyncSession = Depends(get_db),
):
    """提交任务交付报告。"""
    try:
        delivered = await submit_deliverable(
            db,
            task_id=task.id,
            team_id=task.team_id,
            report_data=payload.report_data,
        )
    except ValueError as exc:
        raise not_found(TASK_NOT_FOUND) from exc
    return success_response(data={"id": delivered.id, "status": delivered.status}, message="交付成果已提交验收")


@router.post("/{team_id}/tasks/{task_id}/review", response_model=None)
async def review_task(
    team_id: str,
    task_id: str,
    payload: ReviewTaskRequest,
    task: MatrixTask = Depends(_load_task),
    _leader: User = Depends(_require_team_leader),
    db: AsyncSession = Depends(get_db),
):
    """Leader 验收结论（Leader/管理员决策）。"""
    try:
        reviewed = await review_deliverable(
            db,
            task_id=task.id,
            team_id=task.team_id,
            approved=payload.approved,
            feedback=payload.feedback,
        )
    except ValueError as exc:
        raise not_found(TASK_NOT_FOUND) from exc
    return success_response(data={"id": reviewed.id, "status": reviewed.status}, message="验收结论判定完成")


@router.get("/{team_id}/blackboard", response_model=None)
async def get_blackboard(
    team_id: str,
    topic: Optional[str] = Query(None),
    pinned_only: bool = Query(False),
    team: WorkgroupTeam = Depends(_load_team),
    db: AsyncSession = Depends(get_db),
):
    """查询团队共享黑板活文档。"""
    entries = await list_blackboard_entries(db, team_id=team.id, topic=topic, pinned_only=pinned_only)
    data = [{
        "id": e.id,
        "topic": e.topic,
        "content": e.content,
        "source_profile_id": e.source_profile_id,
        "citations": e.citations,
        "is_pinned": e.is_pinned,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    } for e in entries]
    return success_response(data=data)


@router.post("/{team_id}/blackboard", response_model=None)
async def post_blackboard(
    team_id: str,
    payload: PostBlackboardRequest,
    team: WorkgroupTeam = Depends(_load_team),
    db: AsyncSession = Depends(get_db),
):
    """写入或更新团队共享黑板。"""
    entry = await post_blackboard_entry(
        db,
        team_id=team.id,
        topic=payload.topic,
        content=payload.content,
        source_profile_id=payload.source_profile_id,
        source_task_id=payload.source_task_id,
        citations=payload.citations,
        is_pinned=payload.is_pinned,
    )
    return success_response(data={"id": entry.id, "topic": entry.topic}, message="共享黑板条目已更新")
