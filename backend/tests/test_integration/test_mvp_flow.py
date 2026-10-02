"""MVP 6 步端到端集成测试。

覆盖 PRD §8 MVP 闭环：
1. Connect（导入企业资料）
2. Analyze（企业分析理解）
3. Compile（五级编译 + 完成度 ≥ 80%）
4. Generate（AI 员工生成）
5. Run（演示案例运行）
6. Evolve（SOP v1→v2 进化）

依据：
    - docs/重构方案_v3.md §9.6 阶段 4 / §9.7（7 步案例数据）
    - docs/AutoTeams项目需求重新梳理产品需求文档.md §8 MVP 范围
"""
import json
import os
from datetime import datetime, timezone
from sqlalchemy import select, text

from app.models.enterprise import Enterprise
from app.models.agent import Agent
from app.models.user import User

# 常量而非 fixture：需要在模块作用域可见
from tests.conftest_extensions import DEMO_ENTERPRISE_ID


# ============================================================
# Step 1: Connect — 导入企业资料
# ============================================================


async def test_step1_connect_enterprise_data_exists(demo_enterprise):
    """Step 1: 示例企业数据已就位（30 文件示例数据集）。"""
    enterprise, agents, users = demo_enterprise
    assert enterprise.id == DEMO_ENTERPRISE_ID
    assert enterprise.is_active is True

    # 验证示例数据目录存在
    sample_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "sample_data", "example-enterprise",
    )
    assert os.path.isdir(sample_dir), f"示例数据目录不存在: {sample_dir}"

    # 验证关键文件存在
    for relpath in [
        "01-company/company-profile.md",
        "02-sales/sales-sop-v1.md",
        "02-sales/sales-sop-v2.md",
        "03-customer-service/service-sop-v1.md",
        "03-customer-service/service-sop-v2.md",
        "04-products/product-catalog.md",
        "04-products/price-list.md",
        "05-crm/customers.csv",
        "05-crm/opportunities.csv",
        "09-finance/approval-flow.md",
    ]:
        filepath = os.path.join(sample_dir, relpath)
        assert os.path.isfile(filepath), f"示例数据文件缺失: {relpath}"


# ============================================================
# Step 2: Analyze — 企业分析理解
# ============================================================


async def test_step2_analyze_enterprise_structure(demo_enterprise, db_session):
    """Step 2: 企业组织结构识别（7 部门 24 岗位）。"""
    enterprise, agents, users = demo_enterprise

    # 验证 Enterprise Runtime 中的组织结构数据
    result = await db_session.execute(
        text("SELECT runtime_data FROM enterprise_runtimes WHERE enterprise_id = :eid"),
        {"eid": enterprise.id},
    )
    row = result.fetchone()
    assert row is not None, "Enterprise Runtime 不存在"
    runtime_data = json.loads(row[0])
    assert runtime_data["departments"] == 7, "部门数应为 7"
    assert runtime_data["positions"] == 24, "岗位数应为 24"


async def test_step2_analyze_users_created(demo_enterprise):
    """Step 2: 演示用户已创建（5 个角色）。"""
    enterprise, agents, users = demo_enterprise
    assert len(users) == 5, "应有 5 个演示用户"

    # 验证角色分布：1 admin + 4 member
    admins = [u for u in users if u.role == "admin"]
    members = [u for u in users if u.role == "member"]
    assert len(admins) == 1, "应有 1 个 admin（CEO）"
    assert len(members) == 4, "应有 4 个 member"


# ============================================================
# Step 3: Compile — 五级编译
# ============================================================


async def test_step3_compile_five_stages(demo_enterprise, db_session):
    """Step 3: 五级编译任务已完成（information→knowledge→process→capability→runtime）。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text(
            "SELECT stage, status, confidence, completeness "
            "FROM compilation_jobs WHERE enterprise_id = :eid ORDER BY stage"
        ),
        {"eid": enterprise.id},
    )
    jobs = result.fetchall()
    assert len(jobs) == 5, f"应有 5 个编译任务，实际 {len(jobs)}"

    stages = [j[0] for j in jobs]
    assert stages == ["capability", "information", "knowledge", "process", "runtime"] or \
           len(set(stages)) == 5, f"编译阶段不完整: {stages}"

    for stage, status, confidence, completeness in jobs:
        assert status == "completed", f"阶段 {stage} 状态应为 completed，实际 {status}"
        assert confidence >= 0.80, f"阶段 {stage} 置信度应 ≥0.80，实际 {confidence}"
        assert completeness >= 0.80, f"阶段 {stage} 完成度应 ≥0.80，实际 {completeness}"


async def test_step3_compile_runtime_completeness(demo_enterprise, db_session):
    """Step 3: Enterprise Runtime 完成度 ≥ 80%。"""
    enterprise, agents, users = demo_enterprise

    result = await db_session.execute(
        text("SELECT completeness, is_active FROM enterprise_runtimes WHERE enterprise_id = :eid"),
        {"eid": enterprise.id},
    )
    row = result.fetchone()
    assert row is not None, "Enterprise Runtime 不存在"
    completeness, is_active = row
    assert completeness >= 0.80, f"Runtime 完成度应 ≥0.80，实际 {completeness}"
    assert is_active == 1, "Runtime 应为激活状态"


# ============================================================
# Step 4: Generate — AI 员工生成
# ============================================================


async def test_step4_generate_five_agents(demo_enterprise):
    """Step 4: 5 个核心岗位 AI 员工已生成。"""
    enterprise, agents, users = demo_enterprise
    assert len(agents) == 5, f"应有 5 个 AI 员工，实际 {len(agents)}"

    # 验证 Agent 名称包含关键岗位
    agent_names = [a.name for a in agents]
    for keyword in ["销售", "产品专家", "财务", "客服", "售后"]:
        assert any(keyword in name for name in agent_names), \
            f"缺少包含 '{keyword}' 的 Agent"


async def test_step4_agent_position_ids(demo_enterprise, db_session):
    """Step 4: AI 员工 position_id 已关联（来自组织架构）。"""
    enterprise, agents, users = demo_enterprise

    for agent in agents:
        result = await db_session.execute(
            text("SELECT position_id FROM agents WHERE id = :id"),
            {"id": agent.id},
        )
        pos_id = result.scalar()
        assert pos_id is not None, f"Agent {agent.name} 缺少 position_id"
        assert pos_id.startswith("SL-"), f"position_id 格式错误: {pos_id}"


async def test_step4_agent_lifecycle_stage(demo_enterprise, db_session):
    """Step 4: AI 员工生命周期阶段已设置。

    MVP 3 阶段：recruit / training / production。
    演示用 Agent 已进入 production（生产）阶段。
    """
    enterprise, agents, users = demo_enterprise

    for agent in agents:
        result = await db_session.execute(
            text("SELECT lifecycle_stage FROM agents WHERE id = :id"),
            {"id": agent.id},
        )
        stage = result.scalar()
        assert stage == "production", \
            f"Agent {agent.name} 生命周期阶段应为 production，实际 {stage}"


# ============================================================
# Step 5: Run — 演示案例运行
# ============================================================


async def test_step5_run_demo_case_data(demo_enterprise, db_session):
    """Step 5: 7 步演示案例数据已初始化。"""
    enterprise, agents, users = demo_enterprise

    # 验证商机事件
    result = await db_session.execute(
        text(
            "SELECT event_type, payload FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'opportunity_created'"
        ),
        {"eid": enterprise.id},
    )
    opps = result.fetchall()
    assert len(opps) == 2, f"应有 2 条商机事件，实际 {len(opps)}"

    # 验证报价单草稿
    result = await db_session.execute(
        text(
            "SELECT event_type, payload FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'quotation_generated'"
        ),
        {"eid": enterprise.id},
    )
    quos = result.fetchall()
    assert len(quos) == 2, f"应有 2 条报价单草稿，实际 {len(quos)}"

    # 验证触发事件
    result = await db_session.execute(
        text(
            "SELECT payload FROM collaboration_events "
            "WHERE enterprise_id = :eid AND event_type = 'inquiry_received'"
        ),
        {"eid": enterprise.id},
    )
    trigger = result.fetchone()
    assert trigger is not None, "缺少新询盘触发事件"
    trigger_data = json.loads(trigger[0])
    assert trigger_data["opp_id"] == "OPP-001"
    assert "SL-T100" in trigger_data["inquiry"]


# ============================================================
# Step 6: Evolve — SOP 进化
# ============================================================


async def test_step6_evolve_sop_versions():
    """Step 6: SOP v1/v2 进化对比数据已就位。"""
    sample_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "sample_data", "example-enterprise",
    )

    # 验证 SOP v1/v2 文件存在
    for relpath in [
        "02-sales/sales-sop-v1.md",
        "02-sales/sales-sop-v2.md",
        "03-customer-service/service-sop-v1.md",
        "03-customer-service/service-sop-v2.md",
    ]:
        filepath = os.path.join(sample_dir, relpath)
        assert os.path.isfile(filepath), f"SOP 文件缺失: {relpath}"

    # 验证 v1/v2 内容差异
    v1 = open(os.path.join(sample_dir, "02-sales/sales-sop-v1.md"), encoding="utf-8").read()
    v2 = open(os.path.join(sample_dir, "02-sales/sales-sop-v2.md"), encoding="utf-8").read()
    assert v1 != v2, "SOP v1/v2 内容不应相同"

    # v2 应包含金额分级审批（V2 优化点）
    assert "分级" in v2 or "5万" in v2 or "20万" in v2, \
        "SOP v2 应包含金额分级审批规则"


# ============================================================
# 全链路验证
# ============================================================


async def test_mvp_full_flow(demo_enterprise, db_session):
    """MVP 6 步全链路验证：Connect → Analyze → Compile → Generate → Run → Evolve。"""
    enterprise, agents, users = demo_enterprise

    # Connect: 企业存在
    assert enterprise.is_active

    # Analyze: Runtime 含组织数据
    rt_result = await db_session.execute(
        text("SELECT runtime_data FROM enterprise_runtimes WHERE enterprise_id = :eid"),
        {"eid": enterprise.id},
    )
    assert rt_result.fetchone() is not None

    # Compile: 5 级编译完成
    compile_result = await db_session.execute(
        text("SELECT COUNT(*) FROM compilation_jobs WHERE enterprise_id = :eid AND status = 'completed'"),
        {"eid": enterprise.id},
    )
    assert compile_result.scalar() == 5

    # Generate: 5 个 AI 员工
    assert len(agents) == 5

    # Run: 7 步案例数据就位
    events_result = await db_session.execute(
        text("SELECT COUNT(*) FROM collaboration_events WHERE enterprise_id = :eid"),
        {"eid": enterprise.id},
    )
    assert events_result.scalar() >= 5  # 2 商机 + 2 报价 + 1 触发

    # Evolve: SOP v1/v2 存在（文件系统验证）
    sample_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "sample_data", "example-enterprise",
    )
    assert os.path.isfile(os.path.join(sample_dir, "02-sales/sales-sop-v1.md"))
    assert os.path.isfile(os.path.join(sample_dir, "02-sales/sales-sop-v2.md"))
