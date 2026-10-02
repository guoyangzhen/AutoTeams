"""双盲反事实影子评估测试（AutoTeams 5.0 战役 4）。

覆盖三条主线：
1. 差分推演：双盲脱敏、语义对齐对称性、四维差分瀑布；
2. 反事实净收益计算：时间/成本得失折现与不达标归因；
3. 连续达标免干预转正裁决：累计、中断归零、幂等与端到端 API 链路。
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.counterfactual_shadow import (
    CounterfactualDiff,
    ShadowEvaluationSession,
)
from app.models.enterprise import Enterprise
from app.models.user import User
from app.services.shadow import counterfactual_evaluator as evaluator

BASE_URL = "/api/v1/shadow/counterfactual"

# 两侧语义完全一致的处置描述（字符二元组完全重合 → 对齐度 1.0）
HUMAN_ACTION = "先核库存再回复客户"
AGENT_PROPOSAL = "先核库存再回复客户"
# 语义完全不重合的提案（对齐度 0.0）
DIVERGENT_PROPOSAL = "核对供应商交期承诺书"


# ============================================================
# Fixtures
# ============================================================


@pytest_asyncio.fixture
async def cf_admin_client(client, test_engine):
    """已登录、绑定企业且为 admin 的测试客户端。"""
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as s:
        s.add(Enterprise(id="ent-cf", name="反事实测试企业"))
        await s.commit()

    await _register_and_login(client, "cf@test.com", "反事实管理员")
    async with factory() as s:
        result = await s.execute(select(User).where(User.email == "cf@test.com"))
        user = result.scalar_one_or_none()
        assert user is not None
        user.enterprise_id = "ent-cf"
        user.role = "admin"
        await s.commit()

    yield client, "ent-cf"


async def _register_and_login(client, email: str, name: str):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": name, "password": "pass1234"},
    )
    assert resp.status_code == 201, f"注册失败: {resp.text}"
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pass1234"},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"


def _payload(
    agent_proposal: str = AGENT_PROPOSAL,
    guardrail_breach_count: int = 0,
    required_streak: int | None = None,
    enterprise_id: str = "ent-cf",
) -> dict:
    body = {
        "enterprise_id": enterprise_id,
        "employee_badge": "生产计划员·A",
        "scenario": "客户催单，库存与交期需在 5 分钟内核清",
        "human_action_snapshot": HUMAN_ACTION,
        "agent_proposal_snapshot": agent_proposal,
        "human_duration_seconds": 300.0,
        "agent_duration_seconds": 20.0,
        "human_cost_yuan": 30.0,
        "agent_cost_yuan": 2.0,
        "guardrail_breach_count": guardrail_breach_count,
    }
    if required_streak is not None:
        body["required_streak"] = required_streak
    return body


# ============================================================
# 1. 差分推演：双盲脱敏与语义对齐
# ============================================================


def test_blind_text_strips_identity_labels():
    """双盲脱敏：身份标记与多余空白不进入评分输入。"""
    assert (
        evaluator.blind_text("【真人】先核库存再回复客户")
        == "先核库存再回复客户"
    )
    assert (
        evaluator.blind_text("[数字员工] 先核库存再回复客户")
        == "先核库存再回复客户"
    )
    # 堆叠标记被反复剥离
    assert (
        evaluator.blind_text("【真人】【数字员工】：先核库存") == "先核库存"
    )
    assert evaluator.blind_text("  先核\n库存  ") == "先核 库存"


def test_semantic_alignment_is_symmetric_and_label_blind():
    """双盲：身份标记不改变对齐度，且交换两侧结果不变。"""
    baseline = evaluator.semantic_alignment(HUMAN_ACTION, AGENT_PROPOSAL)
    assert baseline == 1.0

    labelled = evaluator.semantic_alignment(
        "【真人】" + HUMAN_ACTION, "【数字员工】" + AGENT_PROPOSAL
    )
    assert labelled == baseline

    assert evaluator.semantic_alignment(AGENT_PROPOSAL, HUMAN_ACTION) == baseline


def test_semantic_alignment_bounds():
    """完全无交集 → 0.0；两侧皆空 → 0.0（不白送分）。"""
    assert evaluator.semantic_alignment(HUMAN_ACTION, DIVERGENT_PROPOSAL) == 0.0
    assert evaluator.semantic_alignment("", "") == 0.0
    # 单字符文本退化为自身，比较仍保持对称
    assert evaluator.semantic_alignment("甲", "甲") == 1.0
    assert evaluator.semantic_alignment("甲", "乙") == 0.0


def test_build_diff_rows_covers_four_dimensions():
    """四维差分瀑布齐备，且 delta_score 统一为「正数 = 数字员工占优」。"""
    verdict = evaluator.evaluate_session(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=AGENT_PROPOSAL,
        human_duration_seconds=300.0,
        agent_duration_seconds=20.0,
        human_cost_yuan=30.0,
        agent_cost_yuan=2.0,
        guardrail_breach_count=0,
    )
    rows = evaluator.build_diff_rows(verdict, HUMAN_ACTION, AGENT_PROPOSAL)

    assert [r["dimension"] for r in rows] == list(evaluator.DIFF_DIMENSIONS)
    assert {r["assessment"] for r in rows} == {"better"}

    by_dim = {r["dimension"]: r for r in rows}
    # 语义：对齐度直接作为正向读数
    assert by_dim["semantics"]["delta_score"] == 1.0
    # 时效：节省 280s / 3600 标尺
    assert by_dim["latency"]["delta_score"] == pytest.approx(280 / 3600, abs=1e-6)
    # 成本：更省 → 正分
    assert by_dim["cost"]["delta_score"] == pytest.approx(28 / 100, abs=1e-6)
    # 风险：0 次突破 → 满分
    assert by_dim["risk"]["delta_score"] == 1.0


def test_build_diff_rows_marks_regressions_as_worse():
    """数字员工更慢更贵且突破护栏时，时效/成本/风险维度判为 worse。"""
    verdict = evaluator.evaluate_session(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=DIVERGENT_PROPOSAL,
        human_duration_seconds=20.0,
        agent_duration_seconds=300.0,
        human_cost_yuan=2.0,
        agent_cost_yuan=30.0,
        guardrail_breach_count=2,
    )
    rows = {
        r["dimension"]: r
        for r in evaluator.build_diff_rows(verdict, HUMAN_ACTION, DIVERGENT_PROPOSAL)
    }
    # 语义读数为 0.0，落在 parity 边界（不虚报语义增益）
    assert rows["semantics"]["assessment"] == "parity"
    assert rows["semantics"]["delta_score"] == 0.0
    assert rows["latency"]["assessment"] == "worse"
    assert rows["cost"]["assessment"] == "worse"
    assert rows["risk"]["assessment"] == "worse"
    # 2 次突破 → (1-2)/(1+2)
    assert rows["risk"]["delta_score"] == pytest.approx(-1 / 3, abs=1e-6)


def test_build_diff_rows_parity_on_identical_cost_and_time():
    """时间与成本完全打平 → parity（不虚报增益）。"""
    verdict = evaluator.evaluate_session(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=HUMAN_ACTION,
        human_duration_seconds=100.0,
        agent_duration_seconds=100.0,
        human_cost_yuan=5.0,
        agent_cost_yuan=5.0,
        guardrail_breach_count=0,
    )
    rows = {
        r["dimension"]: r
        for r in evaluator.build_diff_rows(verdict, HUMAN_ACTION, HUMAN_ACTION)
    }
    assert rows["latency"]["assessment"] == "parity"
    assert rows["latency"]["delta_score"] == 0.0
    assert rows["cost"]["assessment"] == "parity"
    assert rows["cost"]["delta_score"] == 0.0


# ============================================================
# 2. 反事实净收益计算
# ============================================================


def test_time_and_cost_deltas():
    """时间得失 = 真人 − 数字员工；成本得失 = 数字员工 − 真人。"""
    assert evaluator.compute_time_saving(300.0, 20.0) == 280.0
    assert evaluator.compute_time_saving(20.0, 300.0) == -280.0
    assert evaluator.compute_cost_delta(30.0, 2.0) == -28.0
    assert evaluator.compute_cost_delta(2.0, 30.0) == 28.0


def test_net_benefit_monetizes_time_and_penalizes_guardrails():
    """净收益 = 节省工时 × 时薪 − 成本增量 − 护栏罚金。"""
    # 280s × 180元/h = 14 元；减去成本增量 -28 元（等价 +28）→ 42 元
    assert evaluator.compute_net_benefit(280.0, -28.0, 0) == pytest.approx(42.0)
    # 护栏突破 2 次 → 再扣 1000 元
    assert evaluator.compute_net_benefit(280.0, -28.0, 2) == pytest.approx(-958.0)
    # 时薪可配置
    assert evaluator.compute_net_benefit(
        3600.0, 0.0, 0, hourly_labor_rate_yuan=300.0
    ) == pytest.approx(300.0)


def test_evaluate_session_qualified_case():
    """四项条件全部满足 → 达标。"""
    verdict = evaluator.evaluate_session(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=AGENT_PROPOSAL,
        human_duration_seconds=300.0,
        agent_duration_seconds=20.0,
        human_cost_yuan=30.0,
        agent_cost_yuan=2.0,
        guardrail_breach_count=0,
    )
    assert verdict.is_qualified is True
    assert verdict.reasons == ()
    assert verdict.semantic_alignment_score == 1.0
    assert verdict.time_saving_seconds == 280.0
    assert verdict.cost_delta_yuan == -28.0
    assert verdict.expected_net_benefit_yuan == pytest.approx(42.0)
    assert verdict.to_dict()["is_qualified"] is True


def test_evaluate_session_rejects_low_alignment():
    """语义未对齐 → 不达标并归因。"""
    verdict = evaluator.evaluate_session(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=DIVERGENT_PROPOSAL,
        human_duration_seconds=300.0,
        agent_duration_seconds=20.0,
        human_cost_yuan=30.0,
        agent_cost_yuan=2.0,
        guardrail_breach_count=0,
    )
    assert verdict.is_qualified is False
    assert any("语义对齐度" in r for r in verdict.reasons)


def test_evaluate_session_rejects_slower_agent():
    """没有时效增益 → 不达标（对齐度高也白搭）。"""
    verdict = evaluator.evaluate_session(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=HUMAN_ACTION,
        human_duration_seconds=20.0,
        agent_duration_seconds=300.0,
        human_cost_yuan=30.0,
        agent_cost_yuan=2.0,
        guardrail_breach_count=0,
    )
    assert verdict.is_qualified is False
    assert any("时效增益" in r for r in verdict.reasons)


def test_evaluate_session_rejects_equal_time():
    """时间打平（节省 0s）不算增益。"""
    verdict = evaluator.evaluate_session(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=HUMAN_ACTION,
        human_duration_seconds=100.0,
        agent_duration_seconds=100.0,
        human_cost_yuan=30.0,
        agent_cost_yuan=2.0,
        guardrail_breach_count=0,
    )
    assert verdict.is_qualified is False
    assert any("时效增益" in r for r in verdict.reasons)


def test_evaluate_session_rejects_cost_increase():
    """成本增量超出上限 → 不达标。"""
    verdict = evaluator.evaluate_session(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=HUMAN_ACTION,
        human_duration_seconds=300.0,
        agent_duration_seconds=20.0,
        human_cost_yuan=2.0,
        agent_cost_yuan=30.0,
        guardrail_breach_count=0,
    )
    assert verdict.is_qualified is False
    assert any("成本增量" in r for r in verdict.reasons)


def test_guardrail_breach_is_hard_veto():
    """护栏突破是硬否决：其余维度全优也不达标。"""
    verdict = evaluator.evaluate_session(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=HUMAN_ACTION,
        human_duration_seconds=3600.0,
        agent_duration_seconds=0.0,
        human_cost_yuan=100.0,
        agent_cost_yuan=0.0,
        guardrail_breach_count=1,
    )
    assert verdict.semantic_alignment_score == 1.0
    assert verdict.is_qualified is False
    assert any("硬否决" in r for r in verdict.reasons)
    # 罚金吞掉全部时间收益
    assert verdict.expected_net_benefit_yuan < 0


def test_custom_hourly_rate_changes_net_benefit():
    """时薪是净收益的显式参数，可配置。"""
    kwargs = dict(
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=AGENT_PROPOSAL,
        human_duration_seconds=3600.0,
        agent_duration_seconds=0.0,
        human_cost_yuan=0.0,
        agent_cost_yuan=0.0,
        guardrail_breach_count=0,
    )
    low = evaluator.evaluate_session(hourly_labor_rate_yuan=50.0, **kwargs)
    high = evaluator.evaluate_session(hourly_labor_rate_yuan=500.0, **kwargs)
    assert high.expected_net_benefit_yuan == 500.0
    assert low.expected_net_benefit_yuan == 50.0
    # 达标判定不随时薪变化
    assert low.is_qualified is high.is_qualified is True


# ============================================================
# 3. 免干预转正裁决
# ============================================================


def test_resolve_promotion_accumulates_until_threshold():
    """连续达标逐笔累加，达到阈值那一笔自动转正。"""
    first = evaluator.resolve_promotion(0, True, required_streak=3)
    assert first.consecutive_pass_streak == 1
    assert first.is_auto_promoted is False
    assert first.remaining_to_promotion == 2

    second = evaluator.resolve_promotion(1, True, required_streak=3)
    assert second.consecutive_pass_streak == 2
    assert second.remaining_to_promotion == 1
    assert second.hit_threshold is False

    third = evaluator.resolve_promotion(2, True, required_streak=3)
    assert third.consecutive_pass_streak == 3
    assert third.hit_threshold is True
    assert third.is_auto_promoted is True
    assert third.remaining_to_promotion == 0


def test_resolve_promotion_resets_streak_on_failure():
    """任一笔不达标 → 连续记录归零。"""
    broken = evaluator.resolve_promotion(49, False, required_streak=50)
    assert broken.consecutive_pass_streak == 0
    assert broken.remaining_to_promotion == 50
    assert broken.is_auto_promoted is False
    assert broken.hit_threshold is False


def test_resolve_promotion_is_idempotent_after_promotion():
    """已转正后继续提交 → 保持转正态且不重复计数。"""
    decision = evaluator.resolve_promotion(
        50, True, already_promoted=True, required_streak=50
    )
    assert decision.is_auto_promoted is True
    assert decision.hit_threshold is True

    # 转正后若出现不达标样本：标记保留，连续记录归零以便重新累计
    regressed = evaluator.resolve_promotion(
        50, False, already_promoted=True, required_streak=50
    )
    assert regressed.consecutive_pass_streak == 0
    assert regressed.is_auto_promoted is True


def test_resolve_promotion_default_threshold_is_fifty():
    """战役 4 规格：默认 50 笔连续达标才免干预转正。"""
    assert evaluator.PROMOTION_STREAK_THRESHOLD == 50
    decision = evaluator.resolve_promotion(49, True)
    assert decision.required_streak == 50
    assert decision.is_auto_promoted is True
    assert decision.remaining_to_promotion == 0


# ============================================================
# 4. 端到端 API
# ============================================================


async def test_create_session_persists_diffs_and_verdict(cf_admin_client):
    """提交推演 → 四维差分 + 裁决 + 转正进度全部落库。"""
    client, ent_id = cf_admin_client
    resp = await client.post(
        f"{BASE_URL}/sessions", json=_payload(required_streak=50)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["enterprise_id"] == ent_id
    assert data["employee_badge"] == "生产计划员·A"
    assert data["is_qualified"] is True
    assert data["is_auto_promoted"] is False
    assert data["consecutive_pass_streak"] == 1
    assert data["semantic_alignment_score"] == 1.0
    assert data["time_saving_seconds"] == 280.0
    assert data["cost_delta_yuan"] == -28.0
    assert data["expected_net_benefit_yuan"] == pytest.approx(42.0)

    assert [d["dimension"] for d in data["diffs"]] == [
        "semantics",
        "latency",
        "cost",
        "risk",
    ]
    # 双盲脱敏后的对照文本不含身份标记
    assert data["diffs"][0]["human_value"] == HUMAN_ACTION
    assert data["diffs"][0]["agent_value"] == AGENT_PROPOSAL

    assert data["verdict"]["is_qualified"] is True
    assert data["verdict"]["reasons"] == []
    assert data["promotion"]["remaining_to_promotion"] == 49


async def test_list_and_detail_sessions(cf_admin_client):
    """列表分页 + 详情（含差分瀑布）。"""
    client, ent_id = cf_admin_client
    for _ in range(3):
        resp = await client.post(f"{BASE_URL}/sessions", json=_payload())
        assert resp.status_code == 200, resp.text
    session_id = resp.json()["data"]["session_id"]

    resp = await client.get(
        f"{BASE_URL}/sessions", params={"enterprise_id": ent_id, "limit": 2}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["total"] == 3
    assert len(body["items"]) == 2

    resp = await client.get(
        f"{BASE_URL}/sessions", params={"enterprise_id": ent_id, "offset": 2}
    )
    assert len(resp.json()["data"]["items"]) == 1

    resp = await client.get(
        f"{BASE_URL}/sessions",
        params={"enterprise_id": ent_id, "employee_badge": "不存在的员工"},
    )
    assert resp.json()["data"]["total"] == 0

    resp = await client.get(f"{BASE_URL}/sessions/{session_id}")
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]
    assert detail["session_id"] == session_id
    assert len(detail["diffs"]) == 4
    assert detail["verdict"]["is_qualified"] is True


async def test_zero_touch_promotion_after_streak(cf_admin_client):
    """免干预转正：达标→中断→连续两笔达标即自动转正。"""
    client, ent_id = cf_admin_client

    # 1) 达标第 1 笔
    r1 = await client.post(
        f"{BASE_URL}/sessions", json=_payload(required_streak=2)
    )
    d1 = r1.json()["data"]
    assert d1["consecutive_pass_streak"] == 1
    assert d1["is_auto_promoted"] is False
    assert d1["promoted_at"] is None

    # 2) 护栏突破 → 连续记录归零
    r2 = await client.post(
        f"{BASE_URL}/sessions",
        json=_payload(guardrail_breach_count=1, required_streak=2),
    )
    d2 = r2.json()["data"]
    assert d2["is_qualified"] is False
    assert d2["consecutive_pass_streak"] == 0
    assert d2["is_auto_promoted"] is False
    assert any("硬否决" in reason for reason in d2["verdict"]["reasons"])

    # 3) 达标第 1 笔（重新累计）
    r3 = await client.post(
        f"{BASE_URL}/sessions", json=_payload(required_streak=2)
    )
    assert r3.json()["data"]["consecutive_pass_streak"] == 1

    # 4) 达标第 2 笔 → 免干预自动转正
    r4 = await client.post(
        f"{BASE_URL}/sessions", json=_payload(required_streak=2)
    )
    d4 = r4.json()["data"]
    assert d4["consecutive_pass_streak"] == 2
    assert d4["is_auto_promoted"] is True
    assert d4["promoted_at"] is not None
    assert d4["promotion"]["hit_threshold"] is True


async def test_auto_promotion_keeps_streak_advancing(cf_admin_client):
    """转正后继续提交：连续记录继续累加，转正态保持。"""
    client, _ = cf_admin_client
    await client.post(f"{BASE_URL}/sessions", json=_payload(required_streak=1))
    resp = await client.post(
        f"{BASE_URL}/sessions", json=_payload(required_streak=1)
    )
    data = resp.json()["data"]
    assert data["is_auto_promoted"] is True
    assert data["consecutive_pass_streak"] == 2


async def test_failed_session_does_not_promote(cf_admin_client):
    """语义完全跑偏 → 不达标，绝不转正。"""
    client, _ = cf_admin_client
    resp = await client.post(
        f"{BASE_URL}/sessions",
        json=_payload(agent_proposal=DIVERGENT_PROPOSAL, required_streak=1),
    )
    data = resp.json()["data"]
    assert data["is_qualified"] is False
    assert data["is_auto_promoted"] is False
    assert data["consecutive_pass_streak"] == 0
    assert data["promoted_at"] is None


async def test_detail_exposes_failure_reasons(cf_admin_client):
    """读取路径还原不达标归因：列表/详情/重放口径一致。"""
    client, _ = cf_admin_client
    resp = await client.post(
        f"{BASE_URL}/sessions",
        json=_payload(
            agent_proposal=DIVERGENT_PROPOSAL,
            guardrail_breach_count=1,
            required_streak=50,
        ),
    )
    session_id = resp.json()["data"]["session_id"]
    created_reasons = resp.json()["data"]["verdict"]["reasons"]
    assert len(created_reasons) == 2  # 语义未对齐 + 护栏硬否决

    for url in (f"{BASE_URL}/sessions/{session_id}",):
        detail = (await client.get(url)).json()["data"]
        assert detail["verdict"]["reasons"] == created_reasons

    listing = (
        await client.get(
            f"{BASE_URL}/sessions",
            params={"enterprise_id": "ent-cf", "employee_badge": "生产计划员·A"},
        )
    ).json()["data"]
    assert listing["items"][0]["verdict"]["reasons"] == created_reasons

    replay = (
        await client.post(f"{BASE_URL}/sessions/{session_id}/replay")
    ).json()["data"]
    assert replay["verdict"]["reasons"] == created_reasons


def test_explain_stored_matches_evaluate_session():
    """explain_stored 与 evaluate_session 的归因完全一致。"""
    session = ShadowEvaluationSession(
        enterprise_id="ent-cf",
        employee_badge="生产计划员·A",
        scenario="s",
        human_action_snapshot=HUMAN_ACTION,
        agent_proposal_snapshot=DIVERGENT_PROPOSAL,
        human_duration_seconds=300.0,
        agent_duration_seconds=20.0,
        human_cost_yuan=30.0,
        agent_cost_yuan=2.0,
        guardrail_breach_count=1,
    )
    verdict = evaluator.evaluate_session(
        human_action_snapshot=session.human_action_snapshot,
        agent_proposal_snapshot=session.agent_proposal_snapshot,
        human_duration_seconds=session.human_duration_seconds,
        agent_duration_seconds=session.agent_duration_seconds,
        human_cost_yuan=session.human_cost_yuan,
        agent_cost_yuan=session.agent_cost_yuan,
        guardrail_breach_count=session.guardrail_breach_count,
    )
    session.semantic_alignment_score = verdict.semantic_alignment_score
    session.time_saving_seconds = verdict.time_saving_seconds
    session.cost_delta_yuan = verdict.cost_delta_yuan
    assert list(evaluator.explain_stored(session)) == list(verdict.reasons)


async def test_promotion_gate_reports_progress(cf_admin_client):
    """准入看板：连续进度 / 剩余笔数 / 阈值口径。"""
    client, ent_id = cf_admin_client
    await client.post(f"{BASE_URL}/sessions", json=_payload())
    await client.post(
        f"{BASE_URL}/sessions",
        json=_payload(agent_proposal=DIVERGENT_PROPOSAL),
    )
    await client.post(f"{BASE_URL}/sessions", json=_payload())

    resp = await client.get(
        f"{BASE_URL}/promotion-gate",
        params={"enterprise_id": ent_id, "employee_badge": "生产计划员·A"},
    )
    assert resp.status_code == 200, resp.text
    gate = resp.json()["data"]
    assert gate["total_sessions"] == 3
    assert gate["qualified_sessions"] == 2
    # 最后一笔达标 → 连续记录从 0 起算为 1
    assert gate["consecutive_pass_streak"] == 1
    assert gate["remaining_to_promotion"] == 49
    assert gate["is_auto_promoted"] is False
    assert gate["semantic_alignment_threshold"] == (
        evaluator.SEMANTIC_ALIGNMENT_THRESHOLD
    )
    assert gate["max_guardrail_breaches"] == 0


async def test_reset_promotion_gate_clears_promotion(cf_admin_client):
    """管理员重置准入看板 → 转正标记清空。"""
    client, ent_id = cf_admin_client
    await client.post(f"{BASE_URL}/sessions", json=_payload(required_streak=1))
    await client.post(f"{BASE_URL}/sessions", json=_payload(required_streak=1))

    resp = await client.post(
        f"{BASE_URL}/promotion-gate",
        params={"enterprise_id": ent_id, "employee_badge": "生产计划员·A"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["reset"] == 2

    resp = await client.get(
        f"{BASE_URL}/promotion-gate",
        params={"enterprise_id": ent_id, "employee_badge": "生产计划员·A"},
    )
    assert resp.json()["data"]["is_auto_promoted"] is False
    assert resp.json()["data"]["consecutive_pass_streak"] == 0


async def test_replay_recomputes_net_benefit(cf_admin_client):
    """重放裁决：按新时薪重算净收益，不改变落库结果。"""
    client, ent_id = cf_admin_client
    resp = await client.post(f"{BASE_URL}/sessions", json=_payload())
    session_id = resp.json()["data"]["session_id"]

    resp = await client.post(
        f"{BASE_URL}/sessions/{session_id}/replay",
        params={"hourly_labor_rate_yuan": 720.0},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 280s × 720元/h = 56 元，加回 28 元成本节省 → 84 元
    assert data["verdict"]["expected_net_benefit_yuan"] == pytest.approx(84.0)
    assert data["verdict"]["is_qualified"] is True
    assert len(data["diffs"]) == 4

    # 落库值未被改写
    resp = await client.get(f"{BASE_URL}/sessions/{session_id}")
    assert resp.json()["data"]["expected_net_benefit_yuan"] == pytest.approx(42.0)


async def test_session_not_found(cf_admin_client):
    """未知 session_id → 404。"""
    client, _ = cf_admin_client
    resp = await client.get(f"{BASE_URL}/sessions/does-not-exist")
    assert resp.status_code == 404, resp.text


async def test_unknown_enterprise_rejected(cf_admin_client):
    """未知企业 → 404。"""
    client, _ = cf_admin_client
    resp = await client.get(
        f"{BASE_URL}/sessions", params={"enterprise_id": "ent-missing"}
    )
    assert resp.status_code == 404, resp.text


async def test_tenant_isolation_blocks_foreign_enterprise(client, test_engine):
    """跨租户访问被拒绝（403）。"""
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as s:
        s.add(Enterprise(id="ent-a", name="甲方企业"))
        s.add(Enterprise(id="ent-b", name="乙方企业"))
        await s.commit()

    await _register_and_login(client, "tenant-a@test.com", "甲方成员")
    async with factory() as s:
        result = await s.execute(select(User).where(User.email == "tenant-a@test.com"))
        user = result.scalar_one_or_none()
        user.enterprise_id = "ent-a"
        user.role = "admin"
        await s.commit()

    resp = await client.post(
        f"{BASE_URL}/sessions", json=_payload(enterprise_id="ent-b")
    )
    assert resp.status_code == 403, resp.text

    resp = await client.get(
        f"{BASE_URL}/sessions", params={"enterprise_id": "ent-b"}
    )
    assert resp.status_code == 403, resp.text


async def test_required_streak_override_requires_admin(client, test_engine):
    """非管理员不得下调免干预转正门槛。"""
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as s:
        s.add(Enterprise(id="ent-member", name="成员企业"))
        await s.commit()

    await _register_and_login(client, "member@test.com", "普通成员")
    async with factory() as s:
        result = await s.execute(select(User).where(User.email == "member@test.com"))
        user = result.scalar_one_or_none()
        user.enterprise_id = "ent-member"
        user.role = "member"
        await s.commit()

    resp = await client.post(
        f"{BASE_URL}/sessions", json=_payload(required_streak=1, enterprise_id="ent-member")
    )
    assert resp.status_code == 403, resp.text

    # 不传门槛时普通成员可以提交推演
    resp = await client.post(
        f"{BASE_URL}/sessions", json=_payload(enterprise_id="ent-member")
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["promotion"]["required_streak"] == 50


async def test_diffs_are_persisted_with_session(cf_admin_client, test_engine):
    """差分明细按会话落库（数量与维度）。"""
    client, ent_id = cf_admin_client
    resp = await client.post(f"{BASE_URL}/sessions", json=_payload())
    session_id = resp.json()["data"]["session_id"]

    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as s:
        diffs = list(
            (
                await s.execute(
                    select(CounterfactualDiff).where(
                        CounterfactualDiff.session_id == session_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(diffs) == 4
    assert {d.dimension for d in diffs} == set(evaluator.DIFF_DIMENSIONS)

    async with factory() as s:
        row = (
            await s.execute(
                select(ShadowEvaluationSession).where(
                    ShadowEvaluationSession.session_id == session_id
                )
            )
        ).scalar_one()
    assert row.expected_net_benefit_yuan == pytest.approx(42.0)
    assert row.is_qualified is True
