"""AutoTeams 5.0 动态敏捷特遣队单元测试（战役 1）。

覆盖三条主链路：
1. 生命周期：forming → active → reviewing → dissolved 状态机与非法跃迁拒绝；
2. 带资竞标扣血抵押：抵押池累加、重复竞标拒绝、超额抵押拒绝、押金返还/罚没；
3. 解散结算：验收通过押金原路返还，验收失败全额罚没，重复清算被拒。

DAG 子任务锁入的依赖闸门（前置未验收不得认领）亦在此覆盖，
因为它是特遣队并行正确性的核心不变量。
"""
import pytest

from app.schemas.strike_team import (
    MAX_MEMBER_STAKE_HP,
    MAX_TOTAL_STAKE_HP,
    SubtaskGraph,
    SubtaskNode,
)
from app.services.strike_team.manager import (
    MIN_MEMBERS_TO_ACTIVATE,
    MIN_STAKE_TO_ACTIVATE,
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

ENT = "ent-strike-test"


def _linear_graph() -> SubtaskGraph:
    """A → B → C 线性依赖图，用于验证前置闸门。"""
    return SubtaskGraph(
        nodes=[
            SubtaskNode(id="A", title="情报收集"),
            SubtaskNode(id="B", title="方案拆解", depends_on=["A"]),
            SubtaskNode(id="C", title="成果交付", depends_on=["B"]),
        ],
        edges=[{"from": "A", "to": "B"}, {"from": "B", "to": "C"}],
    )


async def _forming_team(db, **overrides):
    """建一支处于 forming 的特遣队，发起人抵押 20 HP。"""
    params = {
        "name": "Alpha-01-海关报关突击队",
        "mission_statement": "72 小时内完成 AEO 认证资料预审与报关链路演练",
        "initiator_badge": "ATE-2026-SALES-001",
        "initiator_role": "队长",
        "initiator_stake_hp": 20.0,
        "allocated_compute_budget": 100.0,
    }
    params.update(overrides)
    return await create_strike_team(db, enterprise_id=ENT, **params)


# ---------------------------------------------------------------------------
# 1. 生命周期
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_strike_team_starts_in_forming_with_shared_blackboard(db_session):
    """招募发布：特遣队落地为 forming，发起人带资在列，自动开黑板。"""
    team = await _forming_team(db_session)

    assert team.status == "forming"
    assert team.initiator_badge == "ATE-2026-SALES-001"
    assert team.shared_blackboard_id.startswith("bb-")
    assert team.expires_at is not None
    assert len(team.members) == 1
    assert team.members[0]["badge"] == "ATE-2026-SALES-001"
    assert team.total_hp_stake == 20.0


@pytest.mark.asyncio
async def test_create_strike_team_rejects_zero_or_oversized_stake(db_session):
    """带资协议：单人抵押不得超过单笔上限。"""
    with pytest.raises(StrikeTeamError):
        await _forming_team(db_session, initiator_stake_hp=101.0)


@pytest.mark.asyncio
async def test_activate_requires_minimum_members(db_session):
    """成员不足时拒绝锁定租约：单人拉队不算自治编队。"""
    solo_team = await _forming_team(db_session, initiator_stake_hp=10.0, name="Solo-00")
    assert len(solo_team.members) < MIN_MEMBERS_TO_ACTIVATE
    with pytest.raises(StrikeTeamError, match="成员不足"):
        await activate_strike_team(db_session, solo_team.id)


@pytest.mark.asyncio
async def test_activate_requires_positive_stake_pool(db_session):
    """抵押总额须为正：带资入场是硬门槛，而非形式。

    由于竞标入口本身强制 stake>0，此处直接构造零抵押队伍，
    验证激活路径的第二道闸门独立生效（纵深防御）。
    """
    zero_team = await _forming_team(db_session, initiator_stake_hp=0.0, name="Zero-01")
    # 补足第二名成员（抵押仍为 0，绕过 API 直写以触达第二道闸门）
    zero_team.members = list(zero_team.members) + [
        {"badge": "ATE-2026-TECH-014", "role": "工程师", "stake_hp": 0.0, "joined_at": None}
    ]
    zero_team.total_hp_stake = 0.0
    await db_session.commit()

    assert zero_team.total_hp_stake < MIN_STAKE_TO_ACTIVATE
    with pytest.raises(StrikeTeamError, match="抵押总额不足"):
        await activate_strike_team(db_session, zero_team.id)


@pytest.mark.asyncio
async def test_activate_transitions_forming_to_active(db_session):
    """凑齐成员与抵押后锁定资源租约，进入 active。"""
    team = await _forming_team(db_session)
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)

    assert len(team.members) == MIN_MEMBERS_TO_ACTIVATE
    assert team.total_hp_stake == 50.0

    activated = await activate_strike_team(db_session, team.id)
    assert activated.status == "active"


@pytest.mark.asyncio
async def test_full_lifecycle_forming_to_dissolved(db_session):
    """完整闭环：forming → active → reviewing → dissolved。"""
    team = await _forming_team(db_session, subtask_graph=_linear_graph())
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, team.id)

    # 顺序推进 A → B → C
    for subtask_id, badge in (("A", "ATE-2026-SALES-001"), ("B", "ATE-2026-TECH-014"), ("C", "ATE-2026-SALES-001")):
        await lock_subtask(db_session, team.id, subtask_id, badge)
        await deliver_subtask(db_session, team.id, subtask_id, {"artifact": f"{subtask_id}-report"})
        await accept_subtask(db_session, team.id, subtask_id)

    reviewing = await request_review(db_session, team.id)
    assert reviewing.status == "reviewing"

    receipt = await settle_and_dissolve(db_session, team.id, accepted=True, settlement_note="AEO 预审通过")
    assert receipt["accepted"] is True
    assert receipt["total_refunded_hp"] == 50.0
    assert receipt["total_forfeited_hp"] == 0.0

    dissolved = await get_strike_team(db_session, team.id)
    assert dissolved.status == "dissolved"
    assert dissolved.total_hp_stake == 0.0


@pytest.mark.asyncio
async def test_dissolve_from_forming_is_rejected(db_session):
    """未锁定租约的队伍不可直接清算，杜绝绕过带资门槛。"""
    team = await _forming_team(db_session)
    with pytest.raises(StrikeTeamError, match="不可清算解散"):
        await settle_and_dissolve(db_session, team.id, accepted=True)


# ---------------------------------------------------------------------------
# 2. 带资竞标扣血抵押
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bid_accumulates_hp_stake_pool(db_session):
    """带资竞标：抵押额累加进全队对赌池。"""
    team = await _forming_team(db_session, initiator_stake_hp=0.0)
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=25.0)
    await submit_bid(db_session, team.id, badge="ATE-2026-FIN-003", role="风控", stake_hp=15.0)

    assert team.total_hp_stake == 40.0
    assert len(team.members) == 3
    assert {m["badge"] for m in team.members} == {
        "ATE-2026-SALES-001",
        "ATE-2026-TECH-014",
        "ATE-2026-FIN-003",
    }


@pytest.mark.asyncio
async def test_bid_rejects_non_positive_stake(db_session):
    """带资竞标必须抵押正数 HP，零成本入场被拒。"""
    team = await _forming_team(db_session)
    with pytest.raises(StrikeTeamError, match="正数 HP"):
        await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=0.0)
    with pytest.raises(StrikeTeamError, match="正数 HP"):
        await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=-5.0)


@pytest.mark.asyncio
async def test_bid_rejects_duplicate_badge(db_session):
    """同一工号不可重复入队刷抵押池。"""
    team = await _forming_team(db_session)
    with pytest.raises(StrikeTeamError, match="不可重复竞标"):
        await submit_bid(db_session, team.id, badge="ATE-2026-SALES-001", role="队长兼任", stake_hp=10.0)


@pytest.mark.asyncio
async def test_bid_rejects_pool_overflow(db_session):
    """全队抵押总额受上限约束，防止无限对赌刷池。

    上限 400 HP 而单人上限 100 HP，故须多名成员逐笔累加至接近上限，
    再发最后一笔触发全队超限拒绝。
    """
    team = await _forming_team(db_session, initiator_stake_hp=MAX_MEMBER_STAKE_HP)
    # 发起人已押满单人上限，再补 3 名成员各押满，累计达 400 HP = 全队上限
    for i, badge in enumerate(("ATE-2026-TECH-014", "ATE-2026-FIN-003", "ATE-2026-OPS-005")):
        await submit_bid(db_session, team.id, badge=badge, role=f"成员{i}", stake_hp=MAX_MEMBER_STAKE_HP)
    assert team.total_hp_stake == MAX_TOTAL_STAKE_HP

    with pytest.raises(StrikeTeamError, match="超过全队上限"):
        await submit_bid(db_session, team.id, badge="ATE-2026-EXTRA-006", role="候补", stake_hp=10.0)


@pytest.mark.asyncio
async def test_bid_rejected_after_activation(db_session):
    """active 之后即为既定事实，不再接受新竞标。"""
    team = await _forming_team(db_session)
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, team.id)

    with pytest.raises(StrikeTeamError, match="仅 forming 可竞标"):
        await submit_bid(db_session, team.id, badge="ATE-2026-FIN-003", role="风控", stake_hp=10.0)


@pytest.mark.asyncio
async def test_stake_persists_across_sessions(db_session):
    """JSON 列必须真正落库：重开会话后成员与抵押额仍在。

    守护的是 in-place mutation 陷阱——原地 append 不会被 SQLAlchemy 脏检查捕获。
    """
    team = await _forming_team(db_session, initiator_stake_hp=0.0)
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=42.0)

    reloaded = await get_strike_team(db_session, team.id)
    assert reloaded.total_hp_stake == 42.0
    assert [m["badge"] for m in reloaded.members] == [
        "ATE-2026-SALES-001",
        "ATE-2026-TECH-014",
    ]
    assert reloaded.members[1]["stake_hp"] == 42.0
    assert reloaded.members[1]["role"] == "工程师"


# ---------------------------------------------------------------------------
# 3. 解散结算
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dissolve_refunds_stakes_on_acceptance(db_session):
    """验收通过：押金原路返还，结算单逐人可追溯。"""
    team = await _forming_team(db_session, initiator_stake_hp=20.0)
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, team.id)

    receipt = await settle_and_dissolve(db_session, team.id, accepted=True, settlement_note="验收通过")

    assert receipt["total_refunded_hp"] == 50.0
    assert receipt["total_forfeited_hp"] == 0.0
    by_badge = {s["badge"]: s for s in receipt["settlements"]}
    assert by_badge["ATE-2026-SALES-001"]["refunded_hp"] == 20.0
    assert by_badge["ATE-2026-TECH-014"]["refunded_hp"] == 30.0
    assert all(s["forfeited_hp"] == 0.0 for s in receipt["settlements"])


@pytest.mark.asyncio
async def test_dissolve_forfeits_stakes_on_rejection(db_session):
    """验收失败：押金全额罚没，对赌机制生效。"""
    team = await _forming_team(db_session, initiator_stake_hp=20.0)
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, team.id)

    receipt = await settle_and_dissolve(
        db_session, team.id, accepted=False, settlement_note="报关链路演练未达标"
    )

    assert receipt["total_refunded_hp"] == 0.0
    assert receipt["total_forfeited_hp"] == 50.0
    assert all(s["refunded_hp"] == 0.0 for s in receipt["settlements"])
    assert all(s["forfeited_hp"] > 0.0 for s in receipt["settlements"])

    settled = await get_strike_team(db_session, team.id)
    assert settled.status == "dissolved"
    assert settled.total_hp_stake == 0.0
    assert all(m["settlement"] == "forfeited" for m in settled.members)


@pytest.mark.asyncio
async def test_double_dissolve_is_rejected(db_session):
    """重复清算必须被拒，防止押金被重复返还/罚没。"""
    team = await _forming_team(db_session)
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, team.id)
    await settle_and_dissolve(db_session, team.id, accepted=True)

    with pytest.raises(StrikeTeamError, match="已解散"):
        await settle_and_dissolve(db_session, team.id, accepted=True)


# ---------------------------------------------------------------------------
# 4. DAG 子任务锁入闸门
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subtask_lock_respects_dag_dependencies(db_session):
    """前置未验收不得锁入下游，这是并行正确性的核心不变量。"""
    team = await _forming_team(db_session, subtask_graph=_linear_graph())
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, team.id)

    # B 依赖 A，A 未验收 → 拒绝
    with pytest.raises(StrikeTeamError, match="前置依赖未验收"):
        await lock_subtask(db_session, team.id, "B", "ATE-2026-TECH-014")

    # A 可锁入 → 交付 → 验收
    await lock_subtask(db_session, team.id, "A", "ATE-2026-SALES-001")
    await deliver_subtask(db_session, team.id, "A", {"artifact": "intel"})
    await accept_subtask(db_session, team.id, "A")

    # A 验收后 B 解锁
    await lock_subtask(db_session, team.id, "B", "ATE-2026-TECH-014")
    locked = await get_strike_team(db_session, team.id)
    statuses = {n["id"]: n["status"] for n in locked.subtask_graph["nodes"]}
    assert statuses["A"] == "accepted"
    assert statuses["B"] == "locked"
    assert statuses["C"] == "pending"


@pytest.mark.asyncio
async def test_subtask_lock_rejects_non_member(db_session):
    """非本队成员不得认领子任务。"""
    team = await _forming_team(db_session, subtask_graph=_linear_graph())
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, team.id)

    with pytest.raises(StrikeTeamError, match="不是本特遣队成员"):
        await lock_subtask(db_session, team.id, "A", "ATE-2026-OUTSIDER-999")


@pytest.mark.asyncio
async def test_subtask_cannot_be_locked_twice(db_session):
    """同一子任务不可被二次锁入。"""
    team = await _forming_team(db_session, subtask_graph=_linear_graph())
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, team.id)

    await lock_subtask(db_session, team.id, "A", "ATE-2026-SALES-001")
    with pytest.raises(StrikeTeamError, match="已被锁定"):
        await lock_subtask(db_session, team.id, "A", "ATE-2026-TECH-014")


@pytest.mark.asyncio
async def test_review_blocked_while_subtasks_outstanding(db_session):
    """子任务未全部验收时不可进入验收环节。"""
    team = await _forming_team(db_session, subtask_graph=_linear_graph())
    await submit_bid(db_session, team.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, team.id)

    with pytest.raises(StrikeTeamError, match="未验收"):
        await request_review(db_session, team.id)


# ---------------------------------------------------------------------------
# 5. 查询与租户隔离
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_excludes_dissolved_by_default(db_session):
    """列表默认只返回活动队伍；显式开关才带上已解散记录。"""
    forming = await _forming_team(db_session, name="Active-01")
    await submit_bid(db_session, forming.id, badge="ATE-2026-TECH-014", role="工程师", stake_hp=30.0)
    await activate_strike_team(db_session, forming.id)
    await settle_and_dissolve(db_session, forming.id, accepted=True)

    keep = await _forming_team(db_session, name="Active-02", initiator_badge="ATE-2026-FIN-003")

    active_only = await list_strike_teams(db_session, enterprise_id=ENT)
    assert [t.id for t in active_only] == [keep.id]

    with_dissolved = await list_strike_teams(
        db_session, enterprise_id=ENT, include_dissolved=True
    )
    assert len(with_dissolved) == 2


@pytest.mark.asyncio
async def test_list_is_scoped_to_enterprise(db_session):
    """跨企业不可见。"""
    await _forming_team(db_session)
    other = await create_strike_team(
        db_session,
        enterprise_id="ent-other",
        name="Other-01",
        mission_statement="别的企业",
        initiator_badge="ATE-9999-OTHER-001",
    )

    mine = await list_strike_teams(db_session, enterprise_id=ENT)
    assert all(t.enterprise_id == ENT for t in mine)
    assert other.id not in [t.id for t in mine]
