"""AI 员工生成器测试（PRD §5.11）。

覆盖 6 步生成流程：
1. 岗位提取（从 CapabilityMatrix / 默认 MVP 矩阵）
2. 优先级排序（P0 > P1 > P2）
3. Agent 配置生成（6 维度匹配）
4. 推荐展示（返回推荐列表）
5. 用户确认（接受 confirmed_position_ids）
6. 正式创建（创建 Agent，状态设为 recruit）

LLM 调用全部 mock；template_service.apply_agent_template 降级为直接创建。
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.workforce.generator import (
    WorkforceGenerator,
    _default_mvp_capability_matrix,
    _POSITION_TO_TEMPLATE_ROLE,
)
from app.models.agent import Agent


class TestDefaultMvpCapabilityMatrix:
    """默认 MVP 能力矩阵测试。"""

    def test_returns_ten_core_positions(self):
        """PRD §5.11: MVP 推荐 10 个核心岗位（5 P0 + 5 P1）。"""
        cm = _default_mvp_capability_matrix("ent-test")
        assert len(cm.positions) == 10

        position_names = [p.position_name for p in cm.positions]
        assert "销售代表" in position_names
        assert "售前技术支持" in position_names
        assert "财务经理" in position_names
        assert "客服专员" in position_names
        assert "售后服务专员" in position_names
        assert "采购专员" in position_names
        assert "人事专员" in position_names
        assert "市场专员" in position_names
        assert "物流专员" in position_names
        assert "技术工程师" in position_names

    def test_priority_distribution(self):
        """10 个岗位优先级：3 个 P0 + 7 个 P1。"""
        cm = _default_mvp_capability_matrix("ent-test")
        p0_count = sum(1 for p in cm.positions if p.priority == "P0")
        p1_count = sum(1 for p in cm.positions if p.priority == "P1")
        assert p0_count == 3  # 销售/售前/财务
        assert p1_count == 7  # 客服/售后/采购/人事/市场/物流/技术

    def test_positions_have_required_fields(self):
        """每个岗位包含 6 维度匹配所需字段。"""
        cm = _default_mvp_capability_matrix("ent-test")
        for pos in cm.positions:
            assert pos.position_id
            assert pos.position_name
            assert pos.department
            assert hasattr(pos, "required_skills")
            assert hasattr(pos, "required_knowledge")
            assert hasattr(pos, "required_tools")
            assert hasattr(pos, "required_permissions")
            assert hasattr(pos, "main_processes")

    def test_template_role_mapping(self):
        """岗位 ID → 预置模板 role 映射完整。"""
        assert _POSITION_TO_TEMPLATE_ROLE["pos_sales"] == "sales"
        assert _POSITION_TO_TEMPLATE_ROLE["pos_pre_sales"] == "pre_sales"
        assert _POSITION_TO_TEMPLATE_ROLE["pos_finance"] == "finance"
        assert _POSITION_TO_TEMPLATE_ROLE["pos_customer_service"] == "customer_service"
        assert _POSITION_TO_TEMPLATE_ROLE["pos_after_sales"] == "after_sales"


class TestGeneratorGenerate:
    """generate（步骤 1-4）测试。"""

    async def test_generate_returns_recommendations(self, db_session):
        """generate 返回推荐岗位列表。"""
        gen = WorkforceGenerator()
        result = await gen.generate(db_session, "ent-gen-1")

        assert "recommendations" in result
        assert "total" in result
        assert result["total"] == 10  # MVP 10 个核心岗位（5 P0 + 5 P1）
        assert len(result["recommendations"]) == 10

    async def test_generate_recommendations_sorted_by_priority(self, db_session):
        """步骤 2: 推荐列表按优先级排序（P0 在前）。"""
        gen = WorkforceGenerator()
        result = await gen.generate(db_session, "ent-gen-2")

        priorities = [r["priority"] for r in result["recommendations"]]
        # P0 应在前 3 位
        assert priorities[:3] == ["P0", "P0", "P0"]
        # P1 应在后 7 位
        assert priorities[3:] == ["P1"] * 7

    async def test_generate_recommendation_contains_scores(self, db_session):
        """步骤 3: 推荐包含 6 维度评分。"""
        gen = WorkforceGenerator()
        result = await gen.generate(db_session, "ent-gen-3")

        rec = result["recommendations"][0]
        assert "scores" in rec
        assert "position" in rec["scores"]
        assert "skills" in rec["scores"]
        assert "knowledge" in rec["scores"]
        assert "tools" in rec["scores"]
        assert "permissions" in rec["scores"]
        assert "system_prompt" in rec["scores"]
        assert "overall_score" in rec
        assert "is_matched" in rec

    async def test_generate_uses_default_matrix_when_no_compilation(self, db_session):
        """WT1 未编译时使用默认 MVP 能力矩阵。"""
        gen = WorkforceGenerator()
        result = await gen.generate(db_session, "ent-no-compile")

        # 应返回 10 个默认岗位
        assert result["total"] == 10

    async def test_generate_reads_wt1_compilation_artifact(self, db_session):
        """WT1 已编译时从 CompilationArtifact 读取 CapabilityMatrix。"""
        from app.models.compiler import CompilationArtifact
        from app.schemas.compiler import (
            CapabilityMatrix,
            PositionCapability,
            CapabilityCompileOutput,
        )
        from datetime import datetime, timezone

        # 写入一个 capability 级编译产物
        cm = CapabilityMatrix(
            enterprise_id="ent-compiled",
            positions=[
                PositionCapability(
                    position_id="custom_pos",
                    position_name="自定义岗位",
                    department="自定义部门",
                    level="L2",
                    required_skills=[],
                    required_knowledge=[],
                    required_tools=[],
                    required_permissions=[],
                    kpi_ids=[],
                    main_processes=["proc_1", "proc_2", "proc_3"],
                    priority="P0",
                ),
            ],
            compiled_at=datetime.now(timezone.utc),
            confidence=0.9,
        )
        output = CapabilityCompileOutput(
            enterprise_id="ent-compiled",
            capability_matrix=cm,
        )
        artifact = CompilationArtifact(
            enterprise_id="ent-compiled",
            job_id="job-1",
            stage="capability",
            output=output.model_dump(mode="json"),
            confidence=0.9,
        )
        db_session.add(artifact)
        await db_session.commit()

        gen = WorkforceGenerator()
        result = await gen.generate(db_session, "ent-compiled")

        # 应返回 WT1 编译的自定义岗位
        assert result["total"] == 1
        assert result["recommendations"][0]["position_name"] == "自定义岗位"


class TestGeneratorConfirm:
    """confirm（步骤 5-6）测试。"""

    async def test_confirm_creates_agents(self, db_session):
        """步骤 6: 确认后创建 Agent。"""
        gen = WorkforceGenerator()
        # 先生成推荐
        rec = await gen.generate(db_session, "ent-confirm-1")
        position_ids = [r["position_id"] for r in rec["recommendations"][:2]]

        # 确认前 2 个
        result = await gen.confirm(db_session, "ent-confirm-1", position_ids)

        assert len(result["created_agents"]) == 2
        assert len(result["failed"]) == 0
        # 验证 Agent 已创建
        for agent_info in result["created_agents"]:
            assert agent_info["agent_id"]
            assert agent_info["lifecycle_stage"] == "recruit"

    async def test_confirm_invalid_position_id(self, db_session):
        """确认不存在的 position_id 记入 failed。"""
        gen = WorkforceGenerator()
        result = await gen.confirm(db_session, "ent-confirm-2", ["nonexistent_pos"])

        assert len(result["failed"]) == 1
        assert result["failed"][0]["position_id"] == "nonexistent_pos"
        assert "不存在" in result["failed"][0]["error"]

    async def test_confirm_partial_failure(self, db_session):
        """部分有效 + 部分无效的 position_id。"""
        gen = WorkforceGenerator()
        rec = await gen.generate(db_session, "ent-confirm-3")
        valid_id = rec["recommendations"][0]["position_id"]

        result = await gen.confirm(db_session, "ent-confirm-3", [valid_id, "invalid_pos"])

        assert len(result["created_agents"]) == 1
        assert len(result["failed"]) == 1

    async def test_confirm_empty_list(self, db_session):
        """空确认列表返回空结果。"""
        gen = WorkforceGenerator()
        result = await gen.confirm(db_session, "ent-confirm-4", [])

        assert result["created_agents"] == []
        assert result["failed"] == []

    async def test_confirm_agent_has_workforce_fields(self, db_session):
        """创建的 Agent 包含 workforce 字段（lifecycle_stage/position_id/memory_config/kpi_ids）。"""
        gen = WorkforceGenerator()
        rec = await gen.generate(db_session, "ent-confirm-5")
        position_id = rec["recommendations"][0]["position_id"]

        result = await gen.confirm(db_session, "ent-confirm-5", [position_id])
        agent_id = result["created_agents"][0]["agent_id"]

        # 从 DB 读取验证
        from sqlalchemy import select
        agent_result = await db_session.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one()

        assert agent.lifecycle_stage == "recruit"
        assert agent.position_id == position_id
        assert agent.memory_config is not None
        assert "short_term" in agent.memory_config
        assert agent.memory_config["short_term"]["max_turns"] == 20
        assert agent.kpi_ids is not None

    async def test_confirm_creates_lifecycle_record(self, db_session):
        """创建 Agent 时写入生命周期记录。"""
        from app.models.workforce import WorkforceLifecycle

        gen = WorkforceGenerator()
        rec = await gen.generate(db_session, "ent-confirm-6")
        position_id = rec["recommendations"][0]["position_id"]

        result = await gen.confirm(db_session, "ent-confirm-6", [position_id])
        agent_id = result["created_agents"][0]["agent_id"]

        # 查询生命周期记录
        from sqlalchemy import select
        lc_result = await db_session.execute(
            select(WorkforceLifecycle).where(WorkforceLifecycle.agent_id == agent_id)
        )
        records = lc_result.scalars().all()
        assert len(records) >= 1
        assert records[0].stage == "recruit"


class TestGeneratorGenerateSystemPrompt:
    """system_prompt 生成测试。"""

    def test_generate_system_prompt_contains_position_info(self):
        """生成的 system_prompt 包含岗位信息。"""
        from app.schemas.compiler import PositionCapability

        gen = WorkforceGenerator()
        position = PositionCapability(
            position_id="pos_test",
            position_name="测试岗位",
            department="测试部门",
            level="L2",
            required_skills=[],
            required_knowledge=[],
            required_tools=[],
            required_permissions=[],
            kpi_ids=[],
            main_processes=["proc_1"],
            priority="P0",
        )
        prompt = gen._generate_system_prompt(position)

        assert "测试岗位" in prompt
        assert "测试部门" in prompt
        assert "proc_1" in prompt
        assert "AI 数字员工" in prompt

    def test_get_template_role_by_position_id(self):
        """通过 position_id 获取模板 role。"""
        from app.schemas.compiler import PositionCapability

        gen = WorkforceGenerator()
        position = PositionCapability(
            position_id="pos_sales",
            position_name="销售代表",
            department="销售部",
            level="L2",
            required_skills=[],
            required_knowledge=[],
            required_tools=[],
            required_permissions=[],
            kpi_ids=[],
            main_processes=[],
            priority="P0",
        )
        assert gen._get_template_role(position) == "sales"

    def test_get_template_role_by_name_keyword(self):
        """通过岗位名称关键词获取模板 role。"""
        from app.schemas.compiler import PositionCapability

        gen = WorkforceGenerator()
        position = PositionCapability(
            position_id="custom_id",
            position_name="高级客服专员",
            department="客服部",
            level="L1",
            required_skills=[],
            required_knowledge=[],
            required_tools=[],
            required_permissions=[],
            kpi_ids=[],
            main_processes=[],
            priority="P1",
        )
        assert gen._get_template_role(position) == "customer_service"

    def test_get_template_role_no_match(self):
        """无匹配时返回 None。"""
        from app.schemas.compiler import PositionCapability

        gen = WorkforceGenerator()
        position = PositionCapability(
            position_id="custom_xyz",
            position_name="未知岗位",
            department="未知部门",
            level="L1",
            required_skills=[],
            required_knowledge=[],
            required_tools=[],
            required_permissions=[],
            kpi_ids=[],
            main_processes=[],
            priority="P2",
        )
        assert gen._get_template_role(position) is None
