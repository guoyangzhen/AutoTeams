"""AutoTeams 5.0 动态敏捷特遣队 API 路由（战役 1）。

端点契约见 docs/AutoTeams-5.0演进蓝图与全自主蜂群架构设计.md §战役 1.3：
- POST /api/v1/strike_teams                发起特遣队招募
- GET  /api/v1/strike_teams                查询活动特遣队列表与成员状态
- GET  /api/v1/strike_teams/{team_id}      查询特遣队详情
- POST /api/v1/strike_teams/{team_id}/bid        带资竞标响应
- POST /api/v1/strike_teams/{team_id}/activate   锁定资源租约
- POST /api/v1/strike_teams/{team_id}/subtasks/{subtask_id}/lock    锁入 DAG 子任务
- POST /api/v1/strike_teams/{team_id}/subtasks/{subtask_id}/deliver 提交交付物
- POST /api/v1/strike_teams/{team_id}/subtasks/{subtask_id}/accept  验收子任务
- POST /api/v1/strike_teams/{team_id}/review   申请成果验收
- POST /api/v1/strike_teams/{team_id}/dissolve 交付验收并清算解散

所有端点强制企业归属隔离：跨租户访问一律返回 404（不泄露资源是否存在）。
生命周期违规（重复竞标、越权认领、重复清算）由 StrikeTeamError 翻译为 409。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.strike_team import StrikeTeam
from app.models.user import User
from app.schemas.strike_team import (
    StrikeTeamBidRequest,
    StrikeTeamCreateRequest,
    StrikeTeamDissolveRequest,
)
from app.services.strike_team.manager import (
    _read_graph,
    StrikeTeamError,
    accept_subtask,
    activate_strike_team,
    create_strike_team,
    deliver_subtask,
    get_strike_team,
    list_strike_teams,
    lock_subtask,
    request_review,
    settle_and_dissolve,
    submit_bid,
)
from app.utils.response import success_response
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strike_teams", tags=["AutoTeams Strike Teams"])


def _serialize(team: StrikeTeam) -> Dict[str, Any]:
    """ORM → dict；datetime 统一 isoformat，与既有 4.0 路由保持一致。"""
    return {
        "id": team.id,
        "enterprise_id": team.enterprise_id,
        "name": team.name,
        "mission_statement": team.mission_statement,
        "initiator_badge": team.initiator_badge,
        "status": team.status,
        "allocated_compute_budget": team.allocated_compute_budget,
        "total_hp_stake": team.total_hp_stake,
        "shared_blackboard_id": team.shared_blackboard_id,
        "members": team.members or [],
        # 经 _read_graph 规范化：历史脏数据（如旧扁平 map）不再透传给前端导致崩溃
        "subtask_graph": _read_graph(team).model_dump(mode="json"),
        "created_at": team.created_at.isoformat() if team.created_at else None,
        "updated_at": team.updated_at.isoformat() if team.updated_at else None,
        "expires_at": team.expires_at.isoformat() if team.expires_at else None,
    }


async def _load_owned_team(db: AsyncSession, team_id: str, user: User) -> StrikeTeam:
    """加载特遣队并校验企业归属；跨租户统一 404。"""
    team = await get_strike_team(db, team_id)
    if not team or team.enterprise_id != user.enterprise_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="特遣队不存在"
        )
    return team


@router.get("", response_model=None)
async def list_teams(
    status_filter: Optional[str] = Query(
        default=None, alias="status", description="按状态过滤：forming/active/reviewing/dissolved"
    ),
    include_dissolved: bool = Query(default=False, description="是否包含已解散特遣队"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询企业特遣队列表与成员状态。"""
    teams = await list_strike_teams(
        db,
        enterprise_id=current_user.enterprise_id,
        status=status_filter,
        include_dissolved=include_dissolved,
    )
    return success_response(data=[_serialize(t) for t in teams])


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: StrikeTeamCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """发起特遣队招募。"""
    try:
        team = await create_strike_team(
            db,
            enterprise_id=current_user.enterprise_id,
            name=payload.name,
            mission_statement=payload.mission_statement,
            initiator_badge=payload.initiator_badge,
            initiator_role=payload.initiator_role,
            initiator_stake_hp=payload.initiator_stake_hp,
            allocated_compute_budget=payload.allocated_compute_budget,
            subtask_graph=payload.subtask_graph,
            expires_at=payload.expires_at,
        )
    except StrikeTeamError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return success_response(data=_serialize(team), message="特遣队招募已发布")


@router.get("/{team_id}", response_model=None)
async def get_team_detail(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询特遣队详情。"""
    team = await _load_owned_team(db, team_id, current_user)
    return success_response(data=_serialize(team))


@router.post("/{team_id}/bid", response_model=None)
async def bid_team(
    team_id: str,
    payload: StrikeTeamBidRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """带资响应竞标：抵押 HP 换取入场券（Contract Net Protocol 2.0）。"""
    await _load_owned_team(db, team_id, current_user)
    try:
        team = await submit_bid(
            db,
            team_id=team_id,
            badge=payload.badge,
            role=payload.role,
            stake_hp=payload.stake_hp,
            rationale=payload.rationale,
        )
    except StrikeTeamError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success_response(data=_serialize(team), message="竞标已受理，抵押 HP 计入对赌池")


@router.post("/{team_id}/activate", response_model=None)
async def activate_team(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """锁定资源租约：forming → active。"""
    await _load_owned_team(db, team_id, current_user)
    try:
        team = await activate_strike_team(db, team_id)
    except StrikeTeamError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success_response(data=_serialize(team), message="资源租约已锁定，特遣队进入执行态")


@router.post("/{team_id}/subtasks/{subtask_id}/lock", response_model=None)
async def lock_team_subtask(
    team_id: str,
    subtask_id: str,
    assignee_badge: str = Query(..., description="认领该子任务的成员工号"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """锁入 DAG 子任务：前置依赖未验收时拒绝认领。"""
    await _load_owned_team(db, team_id, current_user)
    try:
        team = await lock_subtask(db, team_id, subtask_id, assignee_badge)
    except StrikeTeamError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success_response(data=_serialize(team), message="子任务已锁入")


@router.post("/{team_id}/subtasks/{subtask_id}/deliver", response_model=None)
async def deliver_team_subtask(
    team_id: str,
    subtask_id: str,
    deliverable: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交子任务交付物：locked → delivered。"""
    await _load_owned_team(db, team_id, current_user)
    try:
        team = await deliver_subtask(db, team_id, subtask_id, deliverable)
    except StrikeTeamError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success_response(data=_serialize(team), message="子任务交付物已提交")


@router.post("/{team_id}/subtasks/{subtask_id}/accept", response_model=None)
async def accept_team_subtask(
    team_id: str,
    subtask_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """验收子任务：delivered → accepted，解锁下游依赖。"""
    await _load_owned_team(db, team_id, current_user)
    try:
        team = await accept_subtask(db, team_id, subtask_id)
    except StrikeTeamError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success_response(data=_serialize(team), message="子任务已验收")


@router.post("/{team_id}/review", response_model=None)
async def review_team(
    team_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """申请成果验收：active → reviewing（全部子任务验收后方可进入）。"""
    await _load_owned_team(db, team_id, current_user)
    try:
        team = await request_review(db, team_id)
    except StrikeTeamError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success_response(data=_serialize(team), message="已进入成果验收环节")


@router.post("/{team_id}/dissolve", response_model=None)
async def dissolve_team(
    team_id: str,
    payload: StrikeTeamDissolveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """交付验收并清算解散：HP 押金原路返还或罚没。"""
    await _load_owned_team(db, team_id, current_user)
    try:
        receipt = await settle_and_dissolve(
            db,
            team_id=team_id,
            accepted=payload.accepted,
            settlement_note=payload.settlement_note,
            deliverable=payload.deliverable,
        )
    except StrikeTeamError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    team = await get_strike_team(db, team_id)
    message = (
        "验收通过，HP 押金已原路返还，特遣队解散"
        if payload.accepted
        else "验收未通过，HP 押金已罚没，特遣队解散"
    )
    return success_response(
        data={"team": _serialize(team) if team else None, "settlement": receipt},
        message=message,
    )
