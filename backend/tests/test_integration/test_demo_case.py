"""7 步演示案例集成测试。

覆盖 PRD §8.5「询盘→报价→审批→成交→售后」7 步演示案例：
1. 收到新询盘 → 销售 Agent 提取需求
2. 产品参数查询 → 产品专家 Agent 补充
3. 销售报价 → 报价单生成
4. 财务审核 → 财务 Agent 校验
5. 总监审批 → 人类审批介入
6. 客服同步 → 订单创建
7. 售后接管 → 反馈记录

依据：
    - docs/重构方案_v3.md §9.7（7 步案例数据准备详情）
    - docs/AutoTeams项目需求重新梳理产品需求文档.md §8.5
"""
import json
from sqlalchemy import text


# ============================================================
# Step 1: 询盘响应
# ============================================================


async def test_step1_inquiry_event_exists(demo_enterprise, db_session):
    """Step 1: 新询盘触发事件已创建。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text(
            "SELECT payload FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'inquiry_received'"
        ),
        {"eid": enterprise.id},
    )
    row = result.fetchone()
    assert row is not None, "缺少新询盘触发事件"

    payload = json.loads(row[0])
    assert payload["opp_id"] == "OPP-001"
    assert payload["customer_name"] == "华智新能源"
    assert "SL-T100" in payload["inquiry"]
    assert "SL-GW500" in payload["inquiry"]


async def test_step1_opportunity_data_loaded(demo_enterprise, db_session):
    """Step 1: 商机数据已加载（OPP-001 / OPP-005）。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text(
            "SELECT payload FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'opportunity_created'"
        ),
        {"eid": enterprise.id},
    )
    opps = result.fetchall()
    assert len(opps) == 2

    opp_data = [json.loads(r[0]) for r in opps]
    opp_ids = {o["opp_id"] for o in opp_data}
    assert opp_ids == {"OPP-001", "OPP-005"}

    # 验证 OPP-001 关键字段
    opp001 = next(o for o in opp_data if o["opp_id"] == "OPP-001")
    assert opp001["customer_name"] == "华智新能源"
    assert opp001["estimated_amount"] == 128400
    assert opp001["stage"] == "报价中"


# ============================================================
# Step 2: 产品参数查询
# ============================================================


async def test_step2_product_agent_exists(demo_enterprise):
    """Step 2: 产品专家 Agent 已创建。"""
    enterprise, agents, users = demo_enterprise
    product_agent = agents[1]  # 产品专家
    assert "产品专家" in product_agent.name
    assert product_agent.status == "ready"


async def test_step2_product_spec_data_available():
    """Step 2: 产品规格书数据可用（SL-T100 参数）。"""
    import os
    spec_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "sample_data", "example-enterprise", "04-products", "spec-sheets", "SL-T100.md",
    )
    assert os.path.isfile(spec_path), "SL-T100 规格书不存在"
    content = open(spec_path, encoding="utf-8").read()
    # 验证关键参数
    assert "-40" in content and "125" in content, "SL-T100 测温范围 -40~+125°C 未找到"
    assert "±0.3" in content, "SL-T100 精度 ±0.3°C 未找到"


# ============================================================
# Step 3: 销售报价
# ============================================================


async def test_step3_quotation_drafts_created(demo_enterprise, db_session):
    """Step 3: 报价单草稿已创建（QUO-2026-001 / QUO-2026-002）。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text(
            "SELECT payload FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'quotation_generated'"
        ),
        {"eid": enterprise.id},
    )
    quos = result.fetchall()
    assert len(quos) == 2

    quo_data = [json.loads(r[0]) for r in quos]
    quo_ids = {q["quotation_id"] for q in quo_data}
    assert quo_ids == {"QUO-2026-001", "QUO-2026-002"}

    # 验证 QUO-2026-001 金额（S 级大客户价）
    quo001 = next(q for q in quo_data if q["quotation_id"] == "QUO-2026-001")
    assert quo001["total_amount"] == 128400
    assert quo001["status"] == "草稿"


async def test_step3_quotation_targets_finance(demo_enterprise, db_session):
    """Step 3: 报价单事件目标为财务 Agent（触发 Step 4）。"""
    enterprise, agents, users = demo_enterprise
    finance_agent = agents[2]

    result = await db_session.execute(
        text(
            "SELECT target_agent_id FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'quotation_generated'"
        ),
        {"eid": enterprise.id},
    )
    targets = [r[0] for r in result.fetchall()]
    for t in targets:
        assert t == finance_agent.id, f"报价单目标应为财务 Agent，实际 {t}"


# ============================================================
# Step 4: 财务审核
# ============================================================


async def test_step4_financial_review_gate_exists(demo_enterprise, db_session):
    """Step 4: 财务审核审批门已创建（APR-2026-001 / APR-2026-002）。"""
    enterprise, agents, users = demo_enterprise
    finance_agent = agents[2]

    result = await db_session.execute(
        text(
            "SELECT process_id, node_id, agent_id, status "
            "FROM approval_gates "
            "WHERE enterprise_id = :eid AND node_id = 'financial_review'"
        ),
        {"eid": enterprise.id},
    )
    gates = result.fetchall()
    assert len(gates) == 2, f"应有 2 个财务审核门，实际 {len(gates)}"

    for _process_id, node_id, agent_id, status in gates:
        assert node_id == "financial_review"
        assert agent_id == finance_agent.id
        assert status == "pending"


# ============================================================
# Step 5: 总监审批（人类审批介入）
# ============================================================


async def test_step5_tier_approval_gate_exists(demo_enterprise, db_session):
    """Step 5: 分级审批门已创建（人类审批介入）。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text(
            "SELECT process_id, node_id, agent_id, approver_id, status "
            "FROM approval_gates "
            "WHERE enterprise_id = :eid AND node_id LIKE 'tier_approval_%'"
        ),
        {"eid": enterprise.id},
    )
    gates = result.fetchall()
    assert len(gates) == 2, f"应有 2 个分级审批门，实际 {len(gates)}"

    for _process_id, node_id, agent_id, approver_id, status in gates:        # 人类审批：agent_id 为 NULL
        assert agent_id is None, f"分级审批应为人类审批（agent_id=NULL），实际 {agent_id}"
        assert approver_id is not None, "分级审批应有 approver_id"
        assert status == "pending"
        # 验证金额分级
        assert "5-20万" in node_id or "<5万" in node_id, \
            f"审批节点应包含金额分级信息: {node_id}"


async def test_step5_approval_tier_distribution(demo_enterprise, db_session):
    """Step 5: OPP-001 走总监审批（5-20万），OPP-005 走经理审批（<5万）。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text(
            "SELECT process_id, node_id FROM approval_gates "
            "WHERE enterprise_id = :eid AND node_id LIKE 'tier_approval_%'"
        ),
        {"eid": enterprise.id},
    )
    gates = {r[0]: r[1] for r in result.fetchall()}

    # APR-2026-001（OPP-001, 128400 元）→ 5-20万 → 总监审批
    assert "5-20万" in gates.get("APR-2026-001", "")
    # APR-2026-002（OPP-005, 15300 元）→ <5万 → 经理审批
    assert "<5万" in gates.get("APR-2026-002", "")


# ============================================================
# Step 6: 客服同步（验证数据结构就绪）
# ============================================================


async def test_step6_customer_service_agent_exists(demo_enterprise):
    """Step 6: 客服 Agent 已创建，等待成交后同步订单。"""
    enterprise, agents, users = demo_enterprise
    service_agent = agents[3]  # 客服
    assert "客服" in service_agent.name
    assert service_agent.status == "ready"


# ============================================================
# Step 7: 售后接管（验证数据结构就绪）
# ============================================================


async def test_step7_after_sales_agent_exists(demo_enterprise):
    """Step 7: 售后 Agent 已创建，等待交付后接管。"""
    enterprise, agents, users = demo_enterprise
    after_sales_agent = agents[4]  # 售后
    assert "售后" in after_sales_agent.name
    assert after_sales_agent.status == "ready"


# ============================================================
# 全链路验证（7 步状态流转）
# ============================================================


async def test_7step_case_initial_state(demo_enterprise, db_session):
    """7 步案例初始状态验证：商机=报价中，报价单=草稿，审批=待审核。"""
    enterprise, agents, users = demo_enterprise

    # 商机初始状态：报价中
    opp_result = await db_session.execute(
        text(
            "SELECT payload FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'opportunity_created'"
        ),
        {"eid": enterprise.id},
    )
    for row in opp_result.fetchall():
        payload = json.loads(row[0])
        assert payload["stage"] == "报价中", \
            f"商机 {payload['opp_id']} 初始状态应为报价中"

    # 报价单初始状态：草稿
    quo_result = await db_session.execute(
        text(
            "SELECT payload FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'quotation_generated'"
        ),
        {"eid": enterprise.id},
    )
    for row in quo_result.fetchall():
        payload = json.loads(row[0])
        assert payload["status"] == "草稿", \
            f"报价单 {payload['quotation_id']} 初始状态应为草稿"

    # 审批门初始状态：pending
    gate_result = await db_session.execute(
        text("SELECT status FROM approval_gates WHERE enterprise_id = :eid"),
        {"eid": enterprise.id},
    )
    for row in gate_result.fetchall():
        assert row[0] == "pending", "审批门初始状态应为 pending"

    # 触发事件存在
    trigger_result = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'inquiry_received'"
        ),
        {"eid": enterprise.id},
    )
    assert trigger_result.scalar() == 1, "应有 1 条新询盘触发事件"


async def test_7step_case_agent_coverage(demo_enterprise):
    """7 步案例涉及的全部 AI 员工已就位。"""
    enterprise, agents, users = demo_enterprise

    # 步骤 → Agent 映射（来自 §9.7.2）
    step_agent_map = {
        1: "销售",   # 询盘响应
        2: "产品专家", # 产品参数查询
        3: "销售",   # 报价生成
        4: "财务",   # 财务审核
        5: None,     # 人类审批（无 Agent）
        6: "客服",   # 客服同步
        7: "售后",   # 售后接管
    }

    agent_names = [a.name for a in agents]
    for step, keyword in step_agent_map.items():
        if keyword is None:
            continue  # 人类审批步骤
        assert any(keyword in name for name in agent_names), \
            f"Step {step} 缺少包含 '{keyword}' 的 Agent"
