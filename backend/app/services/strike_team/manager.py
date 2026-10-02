"""AutoTeams 5.0 动态敏捷特遣队生命周期管理器（战役 1）。

Contract Net Protocol 2.0 生命周期：

    create ──▶ forming ──activate──▶ active ──request_review──▶ reviewing
                  │                     │                          │
                  └─────────── dissolve ┴──────────────────────────┘
                                     ▼
                                 dissolved（HP 清算完毕）

四条核心能力：
1. 招募发布 `create_strike_team`：发起人带资入队，发布招募令。
2. 带资竞标计算 `submit_bid`：Contract Net 2.0，员工抵押 HP 换取入场券，
   抵押总额受上限约束（防止刷人数掏空对赌池）。
3. DAG 子任务锁入 `lock_subtask`：只有依赖已全部验收的子任务可被认领锁入，
   杜绝并行改同一份上游产物。
4. 任务闭环与 HP 清算解散 `dissolve_strike_team`：验收通过则押金原路返还，
   验收失败则押金罚没（对赌机制生效）。

⚠️ JSON 列变更约定：`members` / `subtask_graph` 是普通 JSON 列，SQLAlchemy 的
脏检查依赖「对象身份变化」。所有写操作必须整体赋一个新 list/dict，
在原地 `.append()` / `[k] = v` 不会被持久化。本模块已统一走 `_replace_*` 辅助函数。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strike_team import StrikeTeam
from app.schemas.strike_team import (
    MAX_MEMBER_STAKE_HP,
    MAX_TOTAL_STAKE_HP,
    StrikeTeamStatus,
    SubtaskGraph,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# 特遣队租约默认时长（未显式传 expires_at 时）
DEFAULT_LEASE_HOURS = 72
# 激活特遣队所需的最少成员数（发起人 + 至少 1 名竞标者）
MIN_MEMBERS_TO_ACTIVATE = 2
# 激活所需的最低抵押总额（带资入场，防止零成本组队）
MIN_STAKE_TO_ACTIVATE = 1.0


class StrikeTeamError(Exception):
    """特遣队生命周期违规。由 API 层翻译为 4xx。"""


# ---------------------------------------------------------------------------
# JSON 列安全写入
# ---------------------------------------------------------------------------

def _replace_members(team: StrikeTeam, members: List[Dict[str, Any]]) -> None:
    """整体替换成员列表，触发 SQLAlchemy 脏检查。

    入参逐个 dict 复制：JSON 列的脏检查按值比较，若沿用列内同一批 dict 对象
    原地改键，SQLAlchemy 会认为「没变」而跳过 UPDATE，结算字段静默丢失。
    """
    team.members = [dict(m) for m in members]


def _replace_subtask_graph(team: StrikeTeam, graph: SubtaskGraph) -> None:
    """整体替换 DAG 图，触发 SQLAlchemy 脏检查。"""
    team.subtask_graph = graph.model_dump(mode="json")


def _read_graph(team: StrikeTeam) -> SubtaskGraph:
    """读取并校验 DAG；历史脏数据不应让整页 500。"""
    try:
        return SubtaskGraph.model_validate(team.subtask_graph or {"nodes": [], "edges": []})
    except Exception as exc:  # pragma: no cover - 仅在历史脏数据时触发
        logger.error("特遣队 %s 子任务图损坏: %s", team.id, exc)
        return SubtaskGraph()


# ---------------------------------------------------------------------------
# 1. 招募发布
# ---------------------------------------------------------------------------

async def create_strike_team(
    db: AsyncSession,
    enterprise_id: str,
    name: str,
    mission_statement: str,
    initiator_badge: str,
    initiator_role: str = "队长",
    initiator_stake_hp: float = 0.0,
    allocated_compute_budget: float = 100.0,
    subtask_graph: Optional[SubtaskGraph] = None,
    expires_at: Optional[datetime] = None,
) -> StrikeTeam:
    """发起特遣队招募：发起人带资入队，状态 forming 等待竞标。"""
    if not enterprise_id:
        raise StrikeTeamError("缺少企业归属，无法创建特遣队")
    if initiator_stake_hp > MAX_MEMBER_STAKE_HP:
        raise StrikeTeamError(f"发起人抵押 HP 不得超过 {MAX_MEMBER_STAKE_HP}")
    if initiator_stake_hp > MAX_TOTAL_STAKE_HP:
        raise StrikeTeamError(f"抵押总额不得超过 {MAX_TOTAL_STAKE_HP}")

    graph = subtask_graph or SubtaskGraph()
    lease_expiry = expires_at or (utcnow() + timedelta(hours=DEFAULT_LEASE_HOURS))

    team = StrikeTeam(
        id=str(uuid.uuid4()),
        enterprise_id=enterprise_id,
        name=name.strip(),
        mission_statement=mission_statement.strip(),
        initiator_badge=initiator_badge,
        status=StrikeTeamStatus.FORMING.value,
        allocated_compute_budget=allocated_compute_budget,
        total_hp_stake=initiator_stake_hp,
        shared_blackboard_id=f"bb-{uuid.uuid4().hex[:16]}",
        created_at=utcnow(),
        updated_at=utcnow(),
        expires_at=lease_expiry,
    )
    _replace_members(
        team,
        [
            {
                "badge": initiator_badge,
                "role": initiator_role,
                "stake_hp": initiator_stake_hp,
                "joined_at": utcnow().isoformat(),
            }
        ],
    )
    _replace_subtask_graph(team, graph)

    db.add(team)
    await db.commit()
    await db.refresh(team)
    return team


# ---------------------------------------------------------------------------
# 2. 查询
# ---------------------------------------------------------------------------

async def list_strike_teams(
    db: AsyncSession,
    enterprise_id: str,
    status: Optional[str] = None,
    include_dissolved: bool = False,
) -> List[StrikeTeam]:
    """查询企业特遣队列表；默认只返回未解散的活动队伍。"""
    stmt = select(StrikeTeam).where(StrikeTeam.enterprise_id == enterprise_id)
    if status:
        stmt = stmt.where(StrikeTeam.status == status)
    elif not include_dissolved:
        stmt = stmt.where(StrikeTeam.status != StrikeTeamStatus.DISSOLVED.value)
    stmt = stmt.order_by(StrikeTeam.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_strike_team(db: AsyncSession, team_id: str) -> Optional[StrikeTeam]:
    """按 ID 查询特遣队。"""
    result = await db.execute(select(StrikeTeam).where(StrikeTeam.id == team_id))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# 3. 带资竞标（Contract Net Protocol 2.0）
# ---------------------------------------------------------------------------

async def submit_bid(
    db: AsyncSession,
    team_id: str,
    badge: str,
    role: str,
    stake_hp: float,
    rationale: str = "",
) -> StrikeTeam:
    """带资响应竞标：抵押 HP 换取入场券，计入全队对赌池。

    规则（带资协议的硬约束）：
    - 仅 forming 状态可竞标，active 之后即为既定事实不再接单；
    - 同一工号不可重复入队；
    - 抵押必须为正，且单人不超上限、全队不超池上限。
    """
    team = await get_strike_team(db, team_id)
    if not team:
        raise StrikeTeamError("特遣队不存在")
    if team.status != StrikeTeamStatus.FORMING.value:
        raise StrikeTeamError(f"特遣队当前状态为 {team.status}，仅 forming 可竞标")
    if stake_hp <= 0:
        raise StrikeTeamError("带资竞标必须抵押正数 HP")
    if stake_hp > MAX_MEMBER_STAKE_HP:
        raise StrikeTeamError(f"单人抵押不得超过 {MAX_MEMBER_STAKE_HP} HP")

    members = list(team.members or [])
    if any(m.get("badge") == badge for m in members):
        raise StrikeTeamError(f"工号 {badge} 已在特遣队内，不可重复竞标")

    projected = float(team.total_hp_stake or 0.0) + stake_hp
    if projected > MAX_TOTAL_STAKE_HP:
        raise StrikeTeamError(
            f"抵押总额将达 {projected:.1f} HP，超过全队上限 {MAX_TOTAL_STAKE_HP}"
        )

    members.append(
        {
            "badge": badge,
            "role": role,
            "stake_hp": stake_hp,
            "joined_at": utcnow().isoformat(),
            "rationale": rationale,
        }
    )
    _replace_members(team, members)
    team.total_hp_stake = projected
    team.updated_at = utcnow()

    await db.commit()
    await db.refresh(team)
    return team


async def activate_strike_team(db: AsyncSession, team_id: str) -> StrikeTeam:
    """锁定资源租约：forming → active。

    带资协议的闸门：成员数与抵押总额双阈值同时满足才允许开工，
    避免出现「一个人零成本拉起一支队伍」的伪自治。
    """
    team = await get_strike_team(db, team_id)
    if not team:
        raise StrikeTeamError("特遣队不存在")
    if team.status == StrikeTeamStatus.ACTIVE.value:
        return team
    if team.status != StrikeTeamStatus.FORMING.value:
        raise StrikeTeamError(f"仅 forming 状态可激活，当前为 {team.status}")

    members = team.members or []
    if len(members) < MIN_MEMBERS_TO_ACTIVATE:
        raise StrikeTeamError(f"成员不足 {MIN_MEMBERS_TO_ACTIVATE} 人，无法锁定资源租约")
    if float(team.total_hp_stake or 0.0) < MIN_STAKE_TO_ACTIVATE:
        raise StrikeTeamError("抵押总额不足，拒绝零成本组队")

    team.status = StrikeTeamStatus.ACTIVE.value
    team.updated_at = utcnow()
    await db.commit()
    await db.refresh(team)
    return team


# ---------------------------------------------------------------------------
# 4. DAG 子任务锁入
# ---------------------------------------------------------------------------

async def lock_subtask(
    db: AsyncSession,
    team_id: str,
    subtask_id: str,
    assignee_badge: str,
) -> StrikeTeam:
    """锁入 DAG 子任务：认领人必须是本队成员，且全部前置依赖已验收。

    这是特遣队并行安全的核心闸门——上游产物没验收就锁下游，
    会让多名成员基于不同版本的事实同时开工。
    """
    team = await get_strike_team(db, team_id)
    if not team:
        raise StrikeTeamError("特遣队不存在")
    if team.status != StrikeTeamStatus.ACTIVE.value:
        raise StrikeTeamError(f"仅 active 状态可锁入子任务，当前为 {team.status}")

    badges = {m.get("badge") for m in (team.members or [])}
    if assignee_badge not in badges:
        raise StrikeTeamError(f"工号 {assignee_badge} 不是本特遣队成员，无法认领子任务")

    graph = _read_graph(team)
    target = next((n for n in graph.nodes if n.id == subtask_id), None)
    if target is None:
        raise StrikeTeamError(f"子任务 {subtask_id} 不存在")
    if target.status != "pending":
        raise StrikeTeamError(f"子任务 {subtask_id} 已被锁定或完成（当前 {target.status}）")

    accepted = {n.id for n in graph.nodes if n.status in ("delivered", "accepted")}
    blocking = [d for d in target.depends_on if d not in accepted]
    if blocking:
        raise StrikeTeamError(f"子任务 {subtask_id} 的前置依赖未验收: {blocking}")

    target.status = "locked"
    target.assignee_badge = assignee_badge
    _replace_subtask_graph(team, graph)
    team.updated_at = utcnow()

    await db.commit()
    await db.refresh(team)
    return team


async def deliver_subtask(
    db: AsyncSession,
    team_id: str,
    subtask_id: str,
    deliverable: Dict[str, Any],
) -> StrikeTeam:
    """提交子任务交付物：locked → delivered，等待验收。"""
    team = await get_strike_team(db, team_id)
    if not team:
        raise StrikeTeamError("特遣队不存在")

    graph = _read_graph(team)
    target = next((n for n in graph.nodes if n.id == subtask_id), None)
    if target is None:
        raise StrikeTeamError(f"子任务 {subtask_id} 不存在")
    if target.status != "locked":
        raise StrikeTeamError(f"子任务 {subtask_id} 未被锁定，无法提交交付物")

    target.status = "delivered"
    target.deliverable = deliverable
    _replace_subtask_graph(team, graph)
    team.updated_at = utcnow()

    await db.commit()
    await db.refresh(team)
    return team


async def accept_subtask(
    db: AsyncSession,
    team_id: str,
    subtask_id: str,
) -> StrikeTeam:
    """验收子任务：delivered → accepted，解锁下游依赖。"""
    team = await get_strike_team(db, team_id)
    if not team:
        raise StrikeTeamError("特遣队不存在")

    graph = _read_graph(team)
    target = next((n for n in graph.nodes if n.id == subtask_id), None)
    if target is None:
        raise StrikeTeamError(f"子任务 {subtask_id} 不存在")
    if target.status != "delivered":
        raise StrikeTeamError(f"子任务 {subtask_id} 未提交交付物")

    target.status = "accepted"
    _replace_subtask_graph(team, graph)
    team.updated_at = utcnow()

    await db.commit()
    await db.refresh(team)
    return team


# ---------------------------------------------------------------------------
# 5. 任务闭环与 HP 清算解散
# ---------------------------------------------------------------------------

async def request_review(db: AsyncSession, team_id: str) -> StrikeTeam:
    """请求成果验收：active → reviewing。全部子任务验收后方可进入。"""
    team = await get_strike_team(db, team_id)
    if not team:
        raise StrikeTeamError("特遣队不存在")
    if team.status != StrikeTeamStatus.ACTIVE.value:
        raise StrikeTeamError(f"仅 active 状态可申请验收，当前为 {team.status}")

    graph = _read_graph(team)
    outstanding = [n.id for n in graph.nodes if n.status != "accepted"]
    if outstanding:
        raise StrikeTeamError(f"仍有子任务未验收，无法进入验收环节: {outstanding}")

    team.status = StrikeTeamStatus.REVIEWING.value
    team.updated_at = utcnow()
    await db.commit()
    await db.refresh(team)
    return team


async def settle_and_dissolve(
    db: AsyncSession,
    team_id: str,
    accepted: bool,
    settlement_note: str = "",
    deliverable: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """清算并解散特遣队：HP 原路返还或罚没，特遣队进入 dissolved。

    对赌语义：
    - 验收通过（accepted=True）→ 每名成员押金 `refunded_hp` 全额返还；
    - 验收失败（accepted=False）→ 押金 `forfeited_hp` 全额罚没，计入失信记录。

    返回结算单，便于前端与审计留痕。
    """
    team = await get_strike_team(db, team_id)
    if not team:
        raise StrikeTeamError("特遣队不存在")
    if team.status == StrikeTeamStatus.DISSOLVED.value:
        raise StrikeTeamError("特遣队已解散，不可重复清算")
    if team.status not in (StrikeTeamStatus.ACTIVE.value, StrikeTeamStatus.REVIEWING.value):
        raise StrikeTeamError(f"当前状态 {team.status} 不可清算解散")

    # 必须逐个 dict 复制：`list(...)` 只复制外层容器，成员 dict 与列里的是同一对象，
    # 原地改键会让 SQLAlchemy 的 JSON 脏检查判定「值没变」而跳过 UPDATE。
    members = [dict(m) for m in (team.members or [])]
    settlement: List[Dict[str, Any]] = []
    for member in members:
        stake = float(member.get("stake_hp") or 0.0)
        refunded = stake if accepted else 0.0
        forfeited = 0.0 if accepted else stake
        settlement.append(
            {
                "badge": member.get("badge"),
                "role": member.get("role"),
                "stake_hp": stake,
                "refunded_hp": refunded,
                "forfeited_hp": forfeited,
            }
        )
        # 回写成员状态，保留历史轨迹而非直接抹除
        member["settled_hp"] = stake  # 结算后该员工账面 HP
        member["settlement"] = "refunded" if accepted else "forfeited"

    _replace_members(team, members)
    team.status = StrikeTeamStatus.DISSOLVED.value
    team.total_hp_stake = 0.0
    team.updated_at = utcnow()

    receipt = {
        "team_id": team.id,
        "accepted": accepted,
        "total_refunded_hp": sum(s["refunded_hp"] for s in settlement),
        "total_forfeited_hp": sum(s["forfeited_hp"] for s in settlement),
        "settlement_note": settlement_note,
        "deliverable": deliverable or {},
        "settlements": settlement,
        "dissolved_at": team.updated_at.isoformat(),
    }

    # 结算单不落库：它的消费者是 API 响应与审计日志，持久化主体仍是 StrikeTeam 行
    await db.commit()
    await db.refresh(team)
    return receipt
