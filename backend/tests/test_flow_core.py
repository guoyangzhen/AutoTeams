"""AutoTeams 4.0 业务 SOP 规程状态机（Flow-Core）单元与接口测试。

覆盖 AUD-14（工具节点不得模拟执行）与 AUD-15（服务器状态机、真实审批、
显式分支、迭代调度硬上限、状态落库）。
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Base
from app.models.enterprise import Enterprise
from app.models.flow_run import FlowApproval, FlowRun
from app.models.user import User
from app.services.flow_core.engine import FlowEngine
from app.services.flow_core.schema import (
    FlowApprovalDecision,
    FlowCard,
    FlowEdge,
    FlowExecutionState,
    FlowGuardrails,
    FlowNode,
    FlowRunLimits,
)
from app.services.flow_core.sop_synthesizer import SOPSynthesizer
from app.services.flow_core.tool_executor import (
    register_flow_tool,
    unregister_flow_tool,
)

ENTERPRISE_ID = "ent-flow"


# ----------------------------------------------------------------------
# 图与引擎（单元）
# ----------------------------------------------------------------------
def test_flow_card_validation():
    """测试 FlowCard 图完整性校验。"""
    # 正常合法卡片
    flow = FlowCard(
        flow_id="flow-test-1",
        name="测试规程",
        start_node_id="step-1",
        nodes=[
            FlowNode(node_id="step-1", name="收集信息", node_type="collect_info", expected_slots=["order_id"]),
            FlowNode(node_id="step-2", name="执行查询", node_type="action_tool", bound_tools=["tool_query"]),
        ],
        edges=[
            FlowEdge(source_node_id="step-1", target_node_id="step-2"),
        ],
        terminal_node_ids=["step-2"],
    )
    assert flow.flow_id == "flow-test-1"

    # 起始节点非法测试
    with pytest.raises(ValueError):
        FlowCard(
            flow_id="flow-bad",
            name="非法规程",
            start_node_id="non-existent",
            nodes=[FlowNode(node_id="step-1", name="节点1", node_type="collect_info")],
        )

    # 同一节点不允许出现多条默认边（否则回落路径不确定）
    with pytest.raises(ValueError):
        FlowCard(
            flow_id="flow-bad-default",
            name="多默认边",
            start_node_id="s",
            nodes=[
                FlowNode(node_id="s", name="判定", node_type="branch_condition"),
                FlowNode(node_id="t", name="兜底1", node_type="collect_info"),
                FlowNode(node_id="u", name="兜底2", node_type="collect_info"),
            ],
            edges=[
                FlowEdge(source_node_id="s", target_node_id="t", is_default=True),
                FlowEdge(source_node_id="s", target_node_id="u", is_default=True),
            ],
        )

    # 默认边不得同时带判定条件
    with pytest.raises(ValueError):
        FlowCard(
            flow_id="flow-bad-default-cond",
            name="默认边带条件",
            start_node_id="s",
            nodes=[
                FlowNode(node_id="s", name="判定", node_type="branch_condition"),
                FlowNode(node_id="t", name="兜底", node_type="collect_info"),
            ],
            edges=[FlowEdge(source_node_id="s", target_node_id="t", is_default=True, condition_expression="amount > 1")],
        )


def _refund_flow() -> FlowCard:
    return FlowCard(
        flow_id="flow-refund",
        name="售后服务标准流程",
        start_node_id="step-collect",
        nodes=[
            FlowNode(
                node_id="step-collect",
                name="收集退款订单",
                node_type="collect_info",
                expected_slots=["order_id"],
                instruction="请提供需要申请的订单号",
            ),
            FlowNode(
                node_id="step-audit",
                name="退款合规性确认",
                node_type="approval_human",
                instruction="退款申请需要人工审批确认",
            ),
            FlowNode(
                node_id="step-done",
                name="处理完成",
                node_type="collect_info",
                instruction="退款已办结",
            ),
        ],
        edges=[
            FlowEdge(source_node_id="step-collect", target_node_id="step-audit"),
            FlowEdge(source_node_id="step-audit", target_node_id="step-done"),
        ],
        terminal_node_ids=["step-done"],
    )


def _approval(node_id: str, *, granted: bool = True, approver: str = "boss@test.com") -> FlowApprovalDecision:
    return FlowApprovalDecision(
        node_id=node_id,
        granted=granted,
        approver_user_id="approver-1",
        approver_email=approver,
        approver_role="admin",
        decision_id="decision-1",
        decided_at="2026-09-28T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_flow_engine_lifecycle_and_adaptive_slot():
    """测试 FlowEngine 状态机流转与自适应槽位跳步。"""
    flow = _refund_flow()

    # 1. 启动规程 -> 等待输入
    state = await FlowEngine.start_flow(flow)
    assert state.status == "waiting_user_input"
    assert state.current_node_id == "step-collect"

    # 2. 提供订单号 -> 槽位填充，跳转到人工审批节点
    state2 = await FlowEngine.step(flow, state, user_input="订单号: ORD-998877")
    assert state2.accumulated_slots.get("order_id") == "ORD-998877"
    assert state2.current_node_id == "step-audit"
    assert state2.status == "waiting_approval"
    assert state2.pending_approval_node_id == "step-audit"

    # 2.1 没有审批记录时不允许自行推进（旧的 approval_granted 布尔位已移除）
    state2b = await FlowEngine.step(flow, state2.model_copy(deep=True))
    assert state2b.status == "waiting_approval"
    assert state2b.current_node_id == "step-audit"

    # 3. 服务器读出的审批记录通过 -> 跳转到最终履约节点并完成
    state3 = await FlowEngine.step(flow, state2.model_copy(deep=True), approval=_approval("step-audit"))
    assert state3.current_node_id == "step-done"
    assert state3.status == "completed"

    # 4. 自适应推进（启动时已携带 order_id，直接跳过 collect 节点）
    state_prefilled = await FlowEngine.start_flow(flow, initial_slots={"order_id": "ORD-112233"})
    assert state_prefilled.current_node_id == "step-audit"
    assert state_prefilled.status == "waiting_approval"

    # 5. 审批被拒绝 -> 明确失败
    state_rejected = await FlowEngine.step(
        flow, state2.model_copy(deep=True), approval=_approval("step-audit", granted=False)
    )
    assert state_rejected.status == "failed"
    assert "审批未通过" in (state_rejected.last_output or "")

    # 6. 审批属于别的节点 -> 拒绝套用
    state_mismatch = await FlowEngine.step(
        flow, state2.model_copy(deep=True), approval=_approval("step-collect")
    )
    assert state_mismatch.status == "failed"
    assert "与当前节点" in (state_mismatch.error_message or "")


@pytest.mark.asyncio
async def test_flow_engine_condition_branching():
    """测试条件分支路由判定。"""
    flow = FlowCard(
        flow_id="flow-discount",
        name="折扣审批流",
        start_node_id="step-check",
        nodes=[
            FlowNode(node_id="step-check", name="金额判定", node_type="collect_info", expected_slots=["amount"]),
            FlowNode(node_id="step-vip", name="大额VIP审批", node_type="approval_human"),
            FlowNode(node_id="step-normal", name="普通快速审批", node_type="collect_info"),
        ],
        edges=[
            FlowEdge(source_node_id="step-check", target_node_id="step-vip", condition_expression="amount > 1000", priority=10),
            FlowEdge(source_node_id="step-check", target_node_id="step-normal", condition_expression="amount <= 1000", priority=5),
        ],
    )

    state_high = await FlowEngine.start_flow(flow, initial_slots={"amount": 5000})
    assert state_high.current_node_id == "step-vip"

    state_low = await FlowEngine.start_flow(flow, initial_slots={"amount": 300})
    assert state_low.current_node_id == "step-normal"


@pytest.mark.asyncio
async def test_branch_without_matching_condition_fails_explicitly():
    """AUD-15：条件全 false 且无默认边必须确定性失败，绝不取第一条边。"""
    flow = FlowCard(
        flow_id="flow-no-default",
        name="无默认边分支",
        start_node_id="step-check",
        nodes=[
            FlowNode(node_id="step-check", name="金额判定", node_type="branch_condition"),
            FlowNode(node_id="step-vip", name="大额审批", node_type="collect_info"),
        ],
        edges=[
            FlowEdge(source_node_id="step-check", target_node_id="step-vip", condition_expression="amount > 100"),
        ],
        terminal_node_ids=["step-vip"],
    )

    state = await FlowEngine.start_flow(flow, initial_slots={"amount": 0})
    assert state.status == "failed"
    assert state.current_node_id == "step-check"  # 没有跳到 step-vip
    assert "均不成立" in (state.error_message or "")


@pytest.mark.asyncio
async def test_branch_falls_back_only_to_explicit_default_edge():
    """AUD-15：显式 default 边是唯一允许的回落路径。"""
    flow = FlowCard(
        flow_id="flow-with-default",
        name="带默认边的分支",
        start_node_id="step-check",
        nodes=[
            FlowNode(node_id="step-check", name="金额判定", node_type="branch_condition"),
            FlowNode(node_id="step-vip", name="大额审批", node_type="collect_info"),
            FlowNode(node_id="step-manual", name="转人工", node_type="collect_info"),
        ],
        edges=[
            FlowEdge(source_node_id="step-check", target_node_id="step-vip", condition_expression="amount > 100"),
            FlowEdge(source_node_id="step-check", target_node_id="step-manual", is_default=True),
        ],
        terminal_node_ids=["step-manual"],
    )

    state = await FlowEngine.start_flow(flow, initial_slots={"amount": 0})
    assert state.status == "completed"
    assert state.current_node_id == "step-manual"


@pytest.mark.asyncio
async def test_self_loop_terminates_with_bounded_error():
    """AUD-15：合法 Schema 的自环必须有界终止，不能 RecursionError。"""
    flow = FlowCard(
        flow_id="flow-self-loop",
        name="自环规程",
        start_node_id="step-loop",
        nodes=[FlowNode(node_id="step-loop", name="自环判定", node_type="branch_condition")],
        edges=[FlowEdge(source_node_id="step-loop", target_node_id="step-loop")],
    )

    state = await FlowEngine.start_flow(flow, limits=FlowRunLimits(max_steps=25))
    assert state.status == "failed"
    assert state.step_count == 25
    assert "最大推进步数上限" in (state.error_message or "")

    # 重复推进已终止的运行不会再次执行
    again = await FlowEngine.step(flow, state)
    assert again.step_count == 25


@pytest.mark.asyncio
async def test_tool_node_requires_bound_tools_and_real_executor():
    """AUD-14：工具节点必须真的调用 bound_tools，否则显式失败。"""
    flow = FlowCard(
        flow_id="flow-tool",
        name="工具节点规程",
        start_node_id="step-tool",
        nodes=[
            FlowNode(node_id="step-tool", name="查询订单", node_type="action_tool", bound_tools=["tool_query"]),
        ],
        edges=[],
    )

    # 1. 工具未注册 -> 明确失败（unsupported），不产生任何"已执行"结论
    state = await FlowEngine.start_flow(flow)
    assert state.status == "failed"
    assert "没有注册任何真实执行器" in (state.error_message or "")
    assert state.tool_results == {}

    # 2. 注册真实执行器 -> 产物进入 tool_results
    calls: list[str] = []

    async def fake_query(node: FlowNode, state: FlowExecutionState):
        calls.append(node.node_id)
        return {"rows": 1}

    register_flow_tool("tool_query", fake_query)
    try:
        state2 = await FlowEngine.start_flow(flow)
    finally:
        unregister_flow_tool("tool_query")

    assert calls == ["step-tool"]
    assert state2.status == "completed"
    assert state2.tool_results["step-tool"]["tool_query"] == {"rows": 1}
    assert state2.simulations == []

    # 3. 未绑定任何工具的 action_tool 节点必须显式失败
    flow_no_tool = FlowCard(
        flow_id="flow-tool-empty",
        name="未绑定工具",
        start_node_id="step-tool",
        nodes=[FlowNode(node_id="step-tool", name="执行动作", node_type="action_tool")],
        edges=[],
    )
    state3 = await FlowEngine.start_flow(flow_no_tool)
    assert state3.status == "failed"
    assert "未绑定任何工具" in (state3.error_message or "")


def test_sop_synthesizer():
    """测试经验自然语言提炼为 FlowCard。"""
    text = """
    1. 收集客户退货商品条码与原因
    2. 如果金额大于500元，请主管审批授权
    3. 调用退款接口执行退款操作
    4. 发送短信通知给客户完成通知
    """
    card = SOPSynthesizer.synthesize_from_text(text, name="退款全链路SOP")
    assert card.flow_id.startswith("flow-")
    assert card.name == "退款全链路SOP"
    assert len(card.nodes) >= 3
    assert len(card.edges) >= 2
    # 验证是否识别到了 approval 节点
    assert any(n.node_type == "approval_human" for n in card.nodes)


def test_flow_run_tables_registered_in_metadata():
    """AUD-15：运行态与审批表必须在 ORM 元数据中（否则迁移与运行时会不一致）。"""
    assert {"flow_runs", "flow_approvals"}.issubset(set(Base.metadata.tables))
    for name in ("flow_runs", "flow_approvals"):
        assert Base.metadata.tables[name].primary_key.columns


# ----------------------------------------------------------------------
# 服务器状态机（接口）
# ----------------------------------------------------------------------
async def _setup_tenant(client, db_session: AsyncSession):
    """注册一个普通成员和一个企业管理员，并绑定到同一企业。"""
    from tests.conftest import login_user, register_user

    await register_user(client, "member@flow.test", "pass1234", "普通成员")
    await register_user(client, "boss@flow.test", "pass1234", "企业管理员")

    ent = Enterprise(id=ENTERPRISE_ID, name="流程测试企业")
    db_session.add(ent)
    for email, role in (("member@flow.test", "member"), ("boss@flow.test", "admin")):
        result = await db_session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.enterprise_id = ENTERPRISE_ID
        user.role = role
    await db_session.commit()

    await login_user(client, "member@flow.test", "pass1234")
    return client


async def _save_refund_card(client) -> dict:
    resp = await client.post(
        "/api/v1/flow-core/cards",
        json=_refund_flow().model_dump(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


@pytest.mark.asyncio
async def test_client_supplied_state_and_approval_flag_are_rejected(client, db_session):
    """AUD-15：客户端提交的 state / approval_granted 不再是权威输入。"""
    await _setup_tenant(client, db_session)
    await _save_refund_card(client)

    start = await client.post(
        "/api/v1/flow-core/execute/start", json={"flow_id": "flow-refund"}
    )
    assert start.status_code == 201, start.text
    run_id = start.json()["data"]["run_id"]
    state = start.json()["data"]["state"]

    # 旧契约：提交完整 state + approval_granted 直接被拒
    resp = await client.post(
        "/api/v1/flow-core/execute/step",
        json={"state": state, "approval_granted": True},
    )
    assert resp.status_code == 422, resp.text

    # 即便用 run_id，夹带 state 字段同样被拒
    resp2 = await client.post(
        "/api/v1/flow-core/execute/step",
        json={"run_id": run_id, "state": state},
    )
    assert resp2.status_code == 422, resp2.text

    # 合法调用：只提交 run_id + 用户输入
    ok = await client.post(
        "/api/v1/flow-core/execute/step",
        json={"run_id": run_id, "user_input": "订单号: ORD-778899"},
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()["data"]
    assert data["status"] == "waiting_approval"
    assert data["current_node_id"] == "step-audit"
    assert data["version"] == 2


@pytest.mark.asyncio
async def test_unauthorized_approval_does_not_advance_run(client, db_session):
    """AUD-15：普通成员无权审批，运行不推进且不产生审批记录。"""
    from tests.conftest import login_user

    await _setup_tenant(client, db_session)
    await _save_refund_card(client)

    start = await client.post(
        "/api/v1/flow-core/execute/start", json={"flow_id": "flow-refund"}
    )
    run_id = start.json()["data"]["run_id"]
    step = await client.post(
        "/api/v1/flow-core/execute/step",
        json={"run_id": run_id, "user_input": "订单号: ORD-112233"},
    )
    assert step.json()["data"]["status"] == "waiting_approval"
    version_before = step.json()["data"]["version"]

    # 未授权：普通成员尝试审批
    denied = await client.post(
        f"/api/v1/flow-core/execute/runs/{run_id}/approve",
        json={"node_id": "step-audit", "decision": "granted"},
    )
    assert denied.status_code == 403, denied.text

    # 管理员换人审批，且节点必须匹配
    await login_user(client, "boss@flow.test", "pass1234")
    wrong_node = await client.post(
        f"/api/v1/flow-core/execute/runs/{run_id}/approve",
        json={"node_id": "step-done", "decision": "granted"},
    )
    assert wrong_node.status_code == 409, wrong_node.text

    rows = (await db_session.execute(select(FlowApproval))).scalars().all()
    assert rows == [], "未授权/不匹配的审批不得落库"

    detail = await client.get(f"/api/v1/flow-core/execute/runs/{run_id}")
    body = detail.json()["data"]
    assert body["version"] == version_before
    assert body["current_node_id"] == "step-audit"
    assert body["status"] == "waiting_approval"

    # 授权审批推进运行并留痕
    granted = await client.post(
        f"/api/v1/flow-core/execute/runs/{run_id}/approve",
        json={"node_id": "step-audit", "decision": "granted", "comment": "已核对订单"},
    )
    assert granted.status_code == 200, granted.text
    after = granted.json()["data"]
    assert after["status"] == "completed"
    assert after["version"] == version_before + 1

    rows = (await db_session.execute(select(FlowApproval))).scalars().all()
    assert len(rows) == 1
    assert rows[0].approver_email == "boss@flow.test"
    assert rows[0].node_id == "step-audit"
    assert rows[0].decision == "granted"

    # 已结束的运行不能继续推进
    again = await client.post(
        "/api/v1/flow-core/execute/step", json={"run_id": run_id}
    )
    assert again.status_code == 409, again.text


@pytest.mark.asyncio
async def test_stale_version_conflicts(client, db_session):
    """AUD-15：乐观锁让并发推进产生确定的 409 而不是静默覆盖。"""
    await _setup_tenant(client, db_session)
    await _save_refund_card(client)

    start = await client.post("/api/v1/flow-core/execute/start", json={"flow_id": "flow-refund"})
    run_id = start.json()["data"]["run_id"]

    first = await client.post(
        "/api/v1/flow-core/execute/step",
        json={"run_id": run_id, "user_input": "订单号: ORD-1", "expected_version": 1},
    )
    assert first.status_code == 200, first.text

    stale = await client.post(
        "/api/v1/flow-core/execute/step",
        json={"run_id": run_id, "user_input": "订单号: ORD-2", "expected_version": 1},
    )
    assert stale.status_code == 409, stale.text


@pytest.mark.asyncio
async def test_run_state_is_read_back_from_database_by_a_fresh_instance(client, db_session, test_engine):
    """AUD-15：执行状态存在数据库里，进程重启（新实例）后仍能读回。"""
    await _setup_tenant(client, db_session)
    await _save_refund_card(client)

    start = await client.post(
        "/api/v1/flow-core/execute/start",
        json={"flow_id": "flow-refund", "initial_slots": {"order_id": "ORD-999"}},
    )
    run_id = start.json()["data"]["run_id"]
    assert start.json()["data"]["status"] == "waiting_approval"

    # 模拟"进程重启"：全新的 session / 全新 run_store 调用路径
    from app.services.flow_core.run_store import list_approvals, load_run

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh_session:
        user = (
            await fresh_session.execute(select(User).where(User.email == "member@flow.test"))
        ).scalar_one()
        run = await load_run(fresh_session, run_id, user)
        assert run.status == "waiting_approval"
        assert run.current_node_id == "step-audit"
        assert run.state["accumulated_slots"]["order_id"] == "ORD-999"
        assert run.step_count >= 1
        assert await list_approvals(fresh_session, run) == []


@pytest.mark.asyncio
async def test_run_is_invisible_to_other_enterprise(client, db_session, test_engine):
    """AUD-15：跨企业不可见（复用 tenant_scope 统一 404）。"""
    from tests.conftest import login_user, register_user
    from app.services.flow_core.run_store import load_run

    await _setup_tenant(client, db_session)
    await _save_refund_card(client)
    start = await client.post("/api/v1/flow-core/execute/start", json={"flow_id": "flow-refund"})
    run_id = start.json()["data"]["run_id"]

    other_ent = Enterprise(id="ent-other", name="另一家企业")
    db_session.add(other_ent)
    await register_user(client, "outsider@flow.test", "pass1234", "外部用户")
    result = await db_session.execute(select(User).where(User.email == "outsider@flow.test"))
    outsider = result.scalar_one()
    outsider.enterprise_id = "ent-other"
    await db_session.commit()

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh_session:
        with pytest.raises(Exception) as excinfo:
            await load_run(fresh_session, run_id, outsider)
        assert excinfo.value.status_code == 404

    await login_user(client, "outsider@flow.test", "pass1234")
    resp = await client.get(f"/api/v1/flow-core/execute/runs/{run_id}")
    assert resp.status_code == 404, resp.text
    listed = await client.get("/api/v1/flow-core/execute/runs")
    assert listed.json()["data"] == []


@pytest.mark.asyncio
async def test_unmatched_branch_via_api_fails_run(client, db_session):
    """AUD-15：条件全 false 的分支在接口层同样是确定性失败。"""
    await _setup_tenant(client, db_session)
    card = FlowCard(
        flow_id="flow-api-branch",
        name="接口分支",
        start_node_id="step-check",
        nodes=[
            FlowNode(node_id="step-check", name="金额判定", node_type="branch_condition"),
            FlowNode(node_id="step-vip", name="大额审批", node_type="collect_info"),
        ],
        edges=[
            FlowEdge(source_node_id="step-check", target_node_id="step-vip", condition_expression="amount > 100"),
        ],
        terminal_node_ids=["step-vip"],
    )
    assert (await client.post("/api/v1/flow-core/cards", json=card.model_dump())).status_code == 201

    start = await client.post(
        "/api/v1/flow-core/execute/start",
        json={"flow_id": "flow-api-branch", "initial_slots": {"amount": 0}},
    )
    assert start.status_code == 201, start.text
    data = start.json()["data"]
    assert data["status"] == "failed"
    assert data["current_node_id"] == "step-check"
    assert "均不成立" in data["error_message"]


@pytest.mark.asyncio
async def test_self_loop_via_api_terminates(client, db_session):
    """AUD-15：自环经由接口执行也必须有界终止。"""
    await _setup_tenant(client, db_session)
    card = FlowCard(
        flow_id="flow-api-loop",
        name="接口自环",
        start_node_id="step-loop",
        nodes=[FlowNode(node_id="step-loop", name="自环判定", node_type="branch_condition")],
        edges=[FlowEdge(source_node_id="step-loop", target_node_id="step-loop")],
        guardrails=FlowGuardrails(),
    )
    assert (await client.post("/api/v1/flow-core/cards", json=card.model_dump())).status_code == 201

    start = await client.post(
        "/api/v1/flow-core/execute/start",
        json={"flow_id": "flow-api-loop", "max_steps": 12},
    )
    data = start.json()["data"]
    assert data["status"] == "failed"
    assert data["step_count"] == 12
    assert "最大推进步数上限" in data["error_message"]


@pytest.mark.asyncio
async def test_start_requires_enterprise_bound_user(client, db_session):
    """AUD-15：未归属企业的用户不能启动企业规程执行。"""
    from tests.conftest import register_user

    await register_user(client, "solo@flow.test", "pass1234", "无企业用户")
    resp = await client.post("/api/v1/flow-core/execute/start", json={"flow_id": "flow-refund"})
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_flow_run_rows_persisted(client, db_session):
    """执行与审批都真实落库（不是进程内存）。"""
    from tests.conftest import login_user

    await _setup_tenant(client, db_session)
    await _save_refund_card(client)
    start = await client.post(
        "/api/v1/flow-core/execute/start", json={"flow_id": "flow-refund"}
    )
    run_id = start.json()["data"]["run_id"]
    await client.post(
        "/api/v1/flow-core/execute/step",
        json={"run_id": run_id, "user_input": "订单号: ORD-5"},
    )
    await login_user(client, "boss@flow.test", "pass1234")
    await client.post(
        f"/api/v1/flow-core/execute/runs/{run_id}/approve",
        json={"node_id": "step-audit", "decision": "granted"},
    )

    run = (await db_session.execute(select(FlowRun).where(FlowRun.id == run_id))).scalar_one()
    assert run.enterprise_id == ENTERPRISE_ID
    assert run.status == "completed"
    assert run.version == 3
    assert run.finished_at is not None
    assert run.state["tool_results"] == {}
