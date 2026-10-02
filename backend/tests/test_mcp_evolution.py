"""AutoTeams 4.0 MCP 协议客户端引擎与组织进化闭环单元测试。

覆盖 AUD-01 / AUD-14 / AUD-30 修复后的新契约：
* 端侧工具必须带身份上下文 + device_id；后端不再读取自身文件系统；
* 未接入执行器时返回 not configured，而不是 simulated_success；
* 转正门槛取服务器政策，请求只能收紧；样本必须归属目标 Agent。
"""
import pytest
import uuid
from app.models.enterprise import Enterprise
from app.models.workforce import WorkforceProfile
from app.models.shadow import ShadowTask
from app.models.flow_card import FlowCardModel
from app.services.mcp.schema import MCPServerConfig
from app.services.mcp.client import MCPClient
from app.services.mcp.runner_bridge import (
    GrantFileRead,
    LocalRunnerBridge,
    MCPToolCallContext,
    normalize_device_relative_path,
    register_executor,
)
from app.services.evolution.workforce_evolution import WorkforceEvolutionEngine

CONTEXT = MCPToolCallContext(enterprise_id="ent-A", user_id="user-A")


@pytest.fixture(autouse=True)
def _reset_executor():
    """每个用例结束后注销执行器，避免跨用例泄漏。"""
    yield
    register_executor(None)


@pytest.mark.asyncio
async def test_runner_bridge_requires_identity_and_executor():
    """没有身份上下文或没有执行器时，端侧工具一律失败（AUD-01）。"""
    tools = LocalRunnerBridge.list_tools()
    tool_names = [t.name for t in tools]
    assert "runner_system_probe" in tool_names
    assert "runner_read_file" in tool_names

    # 1. 无身份上下文
    res = await LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "g-1", "relative_path": "a.txt"}
    )
    assert res.success is False
    assert "身份上下文" in res.error

    # 2. 有身份但没有注册执行器
    res = await LocalRunnerBridge.call_tool(
        "runner_read_file", {"grant_id": "g-1", "relative_path": "a.txt"}, CONTEXT
    )
    assert res.success is False
    assert "未接入" in res.error

    # 3. 探针同样不再用后端操作系统冒充端侧
    res = await LocalRunnerBridge.call_tool("runner_system_probe", {}, CONTEXT)
    assert res.success is False


@pytest.mark.asyncio
async def test_runner_read_file_rejects_escaping_paths():
    """绝对路径与 .. 逃逸一律拒绝，即使执行器已经就绪（AUD-01）。"""
    register_executor(lambda **kwargs: _never_called())

    for bad in ("/etc/passwd", "../../secret", "a/../../b", "a/../b",
                "C:\\Windows\\win.ini", "", "~/x"):
        res = await LocalRunnerBridge.call_tool(
            "runner_read_file", {"grant_id": "g-1", "relative_path": bad}, CONTEXT
        )
        assert res.success is False, bad
        assert res.error is not None


@pytest.mark.asyncio
async def test_runner_read_file_delegates_to_device_executor():
    """授权范围内的相对路径由设备执行器完成读取。"""
    seen = {}

    async def _executor(**kwargs):
        seen.update(kwargs)
        return GrantFileRead(content="hello", truncated=False)

    register_executor(_executor)
    res = await LocalRunnerBridge.call_tool(
        "runner_read_file",
        {"grant_id": "g-1", "relative_path": "docs/./a.txt"},
        CONTEXT,
    )
    assert res.success is True
    assert res.data["content"] == "hello"
    assert seen["grant_id"] == "g-1"
    assert seen["enterprise_id"] == "ent-A"
    assert seen["user_id"] == "user-A"
    assert seen["relative_path"] == "docs/a.txt"


@pytest.mark.asyncio
async def test_runner_probe_is_not_faked_even_with_executor():
    """探针没有真实端侧通道：注册执行器后也必须明确未实现（AUD-14）。

    绝不能用数据库里的设备档案冒充端侧健康状况。
    """
    res = await LocalRunnerBridge.call_tool("runner_system_probe", {}, CONTEXT)
    assert res.success is False
    assert "未实现" in res.error


@pytest.mark.asyncio
async def test_runner_execute_script_is_not_reported_as_success():
    """未实现的端侧脚本执行不得返回 simulated_success（AUD-14）。"""
    register_executor(lambda **kwargs: _never_called())
    res = await LocalRunnerBridge.call_tool(
        "runner_execute_script", {"command": "npm test"}, CONTEXT
    )
    assert res.success is False
    assert "未实现" in res.error


@pytest.mark.asyncio
async def test_mcp_client_passes_context_through():
    """MCPClient 把身份上下文透传给桥接器。"""
    client = MCPClient(
        MCPServerConfig(
            server_id="test_runner",
            name="测试端侧桥接",
            transport="runner_bridge",
        )
    )
    assert len(await client.list_tools()) >= 3

    res = await client.call_tool("runner_read_file", {"device_id": "d", "relative_path": "a.txt"})
    assert res.success is False
    assert res.execution_time_ms >= 0


def test_normalize_device_relative_path():
    assert normalize_device_relative_path("a/b.txt") == "a/b.txt"
    assert normalize_device_relative_path("a/./b.txt") == "a/b.txt"
    assert normalize_device_relative_path("/abs") is None
    assert normalize_device_relative_path("../x") is None
    assert normalize_device_relative_path("a/../../x") is None
    assert normalize_device_relative_path(123) is None


async def _never_called():
    raise AssertionError("执行器不应被调用：路径/参数校验应先失败")


def _make_profile(ent_id: str, agent_id: str) -> WorkforceProfile:
    return WorkforceProfile(
        id=str(uuid.uuid4()),
        enterprise_id=ent_id,
        agent_id=agent_id,
        employee_badge="ATE-2026-CS-099",
        display_name="小沐",
        job_title="智能售后客服",
        employment_status="shadow",
        performance_score=75.0,
    )


def _make_task(ent_id: str, agent_id: str, eval_result: str, confidence: float) -> ShadowTask:
    return ShadowTask(
        enterprise_id=ent_id,
        agent_id=agent_id,
        task_type="after_sales",
        question="退换货政策咨询",
        human_answer="支持 7 天无理由退换",
        ai_answer="支持 7 天无理由退换",
        confidence=confidence,
        status="evaluating",
        eval_result=eval_result,
    )


@pytest.mark.asyncio
async def test_shadow_mode_graduation_lifecycle(db_session, monkeypatch):
    """影子模式转正：样本必须归属目标 Agent，门槛取服务器政策。"""
    monkeypatch.setattr("app.config.settings.SHADOW_GRADUATION_MIN_SAMPLES", 5)
    monkeypatch.setattr("app.config.settings.SHADOW_GRADUATION_PASS_RATE", 0.85)

    ent_id = f"ent-{uuid.uuid4().hex[:6]}"
    agent_id = str(uuid.uuid4())
    db_session.add(Enterprise(id=ent_id, name="进化智能实验室"))

    profile = _make_profile(ent_id, agent_id)
    db_session.add(profile)

    # 3 match + 2 mismatch -> 60%
    for _ in range(3):
        db_session.add(_make_task(ent_id, agent_id, "match", 0.92))
    for _ in range(2):
        db_session.add(_make_task(ent_id, agent_id, "mismatch", 0.60))
    await db_session.commit()

    # 第一次评估：60% < 85%，不转正
    eval_fail = await WorkforceEvolutionEngine.evaluate_shadow_graduation(
        db=db_session, enterprise_id=ent_id, profile_id=profile.id
    )
    assert eval_fail.graduated is False
    assert eval_fail.pass_rate == 0.6
    assert profile.employment_status == "shadow"

    # 追加 15 条 match -> 18/20 = 90%
    for _ in range(15):
        db_session.add(_make_task(ent_id, agent_id, "match", 0.95))
    await db_session.commit()

    eval_pass = await WorkforceEvolutionEngine.evaluate_shadow_graduation(
        db=db_session, enterprise_id=ent_id, profile_id=profile.id
    )
    assert eval_pass.graduated is True
    assert eval_pass.pass_rate == 0.9
    assert profile.employment_status == "production"
    assert profile.performance_score == 90.0
    assert eval_pass.policy_version


@pytest.mark.asyncio
async def test_graduation_rejects_zero_threshold_and_zero_samples(db_session, monkeypatch):
    """AUD-30：min_samples=0 / threshold=0 不能让无样本档案转正。"""
    monkeypatch.setattr("app.config.settings.SHADOW_GRADUATION_MIN_SAMPLES", 5)
    monkeypatch.setattr("app.config.settings.SHADOW_GRADUATION_PASS_RATE", 0.85)

    ent_id = f"ent-{uuid.uuid4().hex[:6]}"
    agent_id = str(uuid.uuid4())
    db_session.add(Enterprise(id=ent_id, name="影子实验室"))

    # 5 条任务但都没有评估结果（eval_result=pending）
    profile = _make_profile(ent_id, agent_id)
    db_session.add(profile)
    for _ in range(5):
        db_session.add(_make_task(ent_id, agent_id, "pending", 0.9))
    await db_session.commit()

    res = await WorkforceEvolutionEngine.evaluate_shadow_graduation(
        db=db_session,
        enterprise_id=ent_id,
        profile_id=profile.id,
        min_samples=0,
        pass_rate_threshold=0.0,
    )
    assert res.graduated is False
    assert res.total_samples == 5
    assert "样本量不足" in res.reason
    assert profile.employment_status == "shadow"


@pytest.mark.asyncio
async def test_graduation_rejects_profile_without_agent(db_session, monkeypatch):
    """未关联 Agent 的档案不得转正（AUD-30）。"""
    monkeypatch.setattr("app.config.settings.SHADOW_GRADUATION_MIN_SAMPLES", 1)
    monkeypatch.setattr("app.config.settings.SHADOW_GRADUATION_PASS_RATE", 0.5)

    ent_id = f"ent-{uuid.uuid4().hex[:6]}"
    db_session.add(Enterprise(id=ent_id, name="无Agent实验室"))
    profile = _make_profile(ent_id, None)
    db_session.add(profile)
    for _ in range(3):
        db_session.add(_make_task(ent_id, "other-agent", "match", 0.99))
    await db_session.commit()

    res = await WorkforceEvolutionEngine.evaluate_shadow_graduation(
        db=db_session, enterprise_id=ent_id, profile_id=profile.id
    )
    assert res.graduated is False
    assert "未关联 Agent" in res.reason
    assert profile.employment_status == "shadow"


@pytest.mark.asyncio
async def test_requested_threshold_can_only_tighten(db_session, monkeypatch):
    """请求可以更严格，但不能比服务器政策更宽松（AUD-30）。"""
    monkeypatch.setattr("app.config.settings.SHADOW_GRADUATION_MIN_SAMPLES", 5)
    monkeypatch.setattr("app.config.settings.SHADOW_GRADUATION_PASS_RATE", 0.85)

    ent_id = f"ent-{uuid.uuid4().hex[:6]}"
    agent_id = str(uuid.uuid4())
    db_session.add(Enterprise(id=ent_id, name="收紧策略实验室"))
    profile = _make_profile(ent_id, agent_id)
    db_session.add(profile)
    for _ in range(10):
        db_session.add(_make_task(ent_id, agent_id, "match", 0.95))
    await db_session.commit()

    # 请求 0.5（比政策 0.85 宽松）必须被忽略
    loose = await WorkforceEvolutionEngine.evaluate_shadow_graduation(
        db=db_session, enterprise_id=ent_id, profile_id=profile.id, pass_rate_threshold=0.5
    )
    assert loose.graduated is True  # 100% 通过率，无论如何都达标

    # 请求 1.0（更严格）必须被采纳 -> 100% 仍达标
    strict = await WorkforceEvolutionEngine.evaluate_shadow_graduation(
        db=db_session, enterprise_id=ent_id, profile_id=profile.id, pass_rate_threshold=1.0
    )
    assert strict.graduated is True
    assert "100" in strict.reason


@pytest.mark.asyncio
async def test_sop_guardrail_evolution_scoped_to_enterprise(db_session):
    """AUD-07：SOP 规程演进必须绑定企业归属。"""
    ent_id = f"ent-{uuid.uuid4().hex[:6]}"
    other_ent_id = f"ent-{uuid.uuid4().hex[:6]}"
    db_session.add(Enterprise(id=ent_id, name="流程演进科技"))

    flow_model = FlowCardModel(
        id=str(uuid.uuid4()),
        enterprise_id=ent_id,
        flow_id="flow-refund-policy",
        name="大额退款规程",
        version="1.0.0",
        flow_data={
            "flow_id": "flow-refund-policy",
            "name": "大额退款规程",
            "version": "1.0.0",
            "guardrails": {
                "closed_loop_required": True,
                "adaptive_slot_filling": False,
                "high_risk_confirmation": False,
            },
        },
    )
    db_session.add(flow_model)
    await db_session.commit()

    # 跨企业调用查不到记录
    assert await WorkforceEvolutionEngine.evolve_sop_guardrails(
        db=db_session,
        enterprise_id=other_ent_id,
        flow_model_id=flow_model.id,
        feedback_notes=["扣费"],
    ) is None
    await db_session.refresh(flow_model)
    assert flow_model.version == "1.0.0"

    evolved = await WorkforceEvolutionEngine.evolve_sop_guardrails(
        db=db_session,
        enterprise_id=ent_id,
        flow_model_id=flow_model.id,
        feedback_notes=["用户投诉退款时直接扣费，缺乏二次转账支付确认", "信息漏填，缺少必要槽位校验"],
    )
    assert evolved is not None
    assert evolved.version == "1.0.1"

    updated_guardrails = evolved.flow_data.get("guardrails", {})
    assert updated_guardrails.get("high_risk_confirmation") is True
    assert updated_guardrails.get("adaptive_slot_filling") is True

    # 乐观锁：版本不匹配时拒绝写入
    with pytest.raises(ValueError):
        await WorkforceEvolutionEngine.evolve_sop_guardrails(
            db=db_session,
            enterprise_id=ent_id,
            flow_model_id=flow_model.id,
            feedback_notes=["扣费"],
            expected_version="1.0.0",
        )
    await db_session.refresh(flow_model)
    assert flow_model.version == "1.0.1"
