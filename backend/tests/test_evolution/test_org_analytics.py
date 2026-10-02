"""AI Org Analytics 测试（PRD §5.5，5 类指标 + L1-L5 成熟度）。

测试覆盖：
- get_metrics：返回 5 类指标 + maturity_level
- get_maturity_level：L1（无员工）/ L1（有员工无执行）/ L2（有执行 + 审批）
- _compute_agent_workload：员工数/阶段分布/生产率
- _compute_process_efficiency：事件数/类型分布/自动化率
- _compute_tool_usage：Runtime 不可用时优雅降级
- _compute_business_impact：成交数/总金额
- _persist_metrics：5 类指标快照写入 org_metrics
- L1-L5 成熟度等级定义完整性
"""
import pytest

from app.services.evolution.org_analytics import (
    OrgAnalyticsService,
    org_analytics,
    METRIC_AGENT_WORKLOAD,
    METRIC_PROCESS_EFFICIENCY,
    METRIC_TOOL_USAGE,
    METRIC_BUSINESS_IMPACT,
    METRIC_MATURITY,
    ALL_METRIC_TYPES,
    MATURITY_LEVELS,
    MVP_TARGET_LEVEL,
)
from app.models.evolution import OrgMetrics
from app.models.collaboration import CollaborationEvent, ApprovalGate

# 共享 fixtures（conftest_extensions 未被 pytest 自动发现，需显式导入）


# ============================================================
# 测试辅助
# ============================================================


async def _seed_enterprise(db):
    from sqlalchemy import text
    import uuid
    from datetime import datetime, timezone

    eid = f"ent-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    await db.execute(
        text(
            "INSERT INTO enterprises (id, name, is_active, invite_max_uses, invite_used_count, created_at, updated_at) "
            "VALUES (:id, :name, 1, 10, 0, :now, :now)"
        ),
        {"id": eid, "name": "测试企业", "now": now},
    )
    await db.commit()
    return eid


async def _seed_agent(db, enterprise_id, lifecycle_stage="production", name="测试 Agent"):
    from app.models.agent import Agent

    agent = Agent(
        enterprise_id=enterprise_id,
        name=name,
        description="测试用",
        system_prompt="你是测试 Agent",
        status="ready",
        version="1.0.0",
        config={},
        lifecycle_stage=lifecycle_stage,
    )
    db.add(agent)
    await db.commit()
    return agent


async def _seed_event(db, enterprise_id, event_type="inquiry_received", status="pending",
                      payload=None, target_agent_id=None):
    event = CollaborationEvent(
        enterprise_id=enterprise_id,
        event_type=event_type,
        payload=payload or {},
        target_agent_id=target_agent_id,
        status=status,
    )
    db.add(event)
    await db.commit()
    return event


async def _seed_approval_gate(db, enterprise_id, process_id="proc-1",
                              node_id="node-1", status="pending"):
    gate = ApprovalGate(
        enterprise_id=enterprise_id,
        process_id=process_id,
        node_id=node_id,
        status=status,
    )
    db.add(gate)
    await db.commit()
    return gate


# ============================================================
# 常量定义测试
# ============================================================


class TestConstants:
    def test_five_metric_types_defined(self):
        """5 类指标类型定义完整。"""
        assert len(ALL_METRIC_TYPES) == 5
        assert METRIC_AGENT_WORKLOAD in ALL_METRIC_TYPES
        assert METRIC_PROCESS_EFFICIENCY in ALL_METRIC_TYPES
        assert METRIC_TOOL_USAGE in ALL_METRIC_TYPES
        assert METRIC_BUSINESS_IMPACT in ALL_METRIC_TYPES
        assert METRIC_MATURITY in ALL_METRIC_TYPES

    def test_l1_to_l5_maturity_levels_defined(self):
        """L1-L5 成熟度等级定义完整。"""
        assert len(MATURITY_LEVELS) == 5
        for level in ("L1", "L2", "L3", "L4", "L5"):
            assert level in MATURITY_LEVELS
            assert "name" in MATURITY_LEVELS[level]
            assert "description" in MATURITY_LEVELS[level]

    def test_mvp_target_is_l2(self):
        """MVP 目标等级为 L2。"""
        assert MVP_TARGET_LEVEL == "L2"


# ============================================================
# get_metrics 测试
# ============================================================


class TestGetMetrics:
    async def test_get_metrics_returns_all_5_types(self, db_session, v3_tables):
        """get_metrics 返回 5 类指标 + maturity_level。"""
        eid = await _seed_enterprise(db_session)
        await _seed_agent(db_session, eid)

        metrics = await org_analytics.get_metrics(db_session, eid, period="2026-07")

        assert "agent_workload" in metrics
        assert "process_efficiency" in metrics
        assert "tool_usage" in metrics
        assert "business_impact" in metrics
        assert "maturity_level" in metrics
        assert metrics["period"] == "2026-07"

    async def test_get_metrics_persists_5_rows(self, db_session, v3_tables):
        """get_metrics 持久化 5 行 org_metrics 记录。"""
        from sqlalchemy import select, func

        eid = await _seed_enterprise(db_session)
        await _seed_agent(db_session, eid)

        await org_analytics.get_metrics(db_session, eid)

        count_result = await db_session.execute(
            select(func.count(OrgMetrics.id)).where(
                OrgMetrics.enterprise_id == eid
            )
        )
        count = int(count_result.scalar() or 0)
        assert count == 5  # 5 类指标各一行

    async def test_get_metrics_persists_correct_types(self, db_session, v3_tables):
        """持久化的 5 行覆盖全部 5 类指标类型。"""
        from sqlalchemy import select

        eid = await _seed_enterprise(db_session)
        await _seed_agent(db_session, eid)

        await org_analytics.get_metrics(db_session, eid)

        result = await db_session.execute(
            select(OrgMetrics.metric_type).where(
                OrgMetrics.enterprise_id == eid
            )
        )
        types = {row[0] for row in result.fetchall()}
        assert types == set(ALL_METRIC_TYPES)


# ============================================================
# get_maturity_level 测试（L1-L5）
# ============================================================


class TestMaturityLevel:
    async def test_l1_when_no_agents(self, db_session, v3_tables):
        """无 AI 员工 → L1（辅助）。"""
        eid = await _seed_enterprise(db_session)

        rating = await org_analytics.get_maturity_level(db_session, eid)

        assert rating["level"] == "L1"
        assert rating["name"] == "辅助"
        assert rating["achieved"] is False  # L1 < L2 (MVP 目标)

    async def test_l1_when_agents_but_no_execution(self, db_session, v3_tables):
        """有 AI 员工但无协作事件（未执行业务）→ L1。"""
        eid = await _seed_enterprise(db_session)
        await _seed_agent(db_session, eid)

        rating = await org_analytics.get_maturity_level(db_session, eid)

        assert rating["level"] == "L1"
        assert rating["achieved"] is False

    async def test_l2_when_execution_without_approval(self, db_session, v3_tables):
        """有 AI 员工 + 协作事件（无审批门）→ L2。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)
        await _seed_event(db_session, eid, target_agent_id=agent.id)

        rating = await org_analytics.get_maturity_level(db_session, eid)

        assert rating["level"] == "L2"
        assert rating["name"] == "协作"
        assert rating["achieved"] is True  # L2 >= MVP 目标 L2

    async def test_l2_when_execution_with_approval(self, db_session, v3_tables):
        """有 AI 员工 + 协作事件 + 审批门 → 稳固 L2。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)
        await _seed_event(db_session, eid, target_agent_id=agent.id)
        await _seed_approval_gate(db_session, eid)

        rating = await org_analytics.get_maturity_level(db_session, eid)

        assert rating["level"] == "L2"
        assert rating["achieved"] is True

    async def test_maturity_includes_dimensions(self, db_session, v3_tables):
        """成熟度评级包含维度数据。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)
        await _seed_event(db_session, eid, target_agent_id=agent.id)
        await _seed_approval_gate(db_session, eid)

        rating = await org_analytics.get_maturity_level(db_session, eid)

        dims = rating["dimensions"]
        assert dims["agent_count"] == 1.0
        assert dims["collaboration_events"] == 1.0
        assert dims["approval_gates"] == 1.0
        assert 0.0 <= dims["automation_rate"] <= 1.0
        assert 0.0 <= dims["human_intervention"] <= 1.0


# ============================================================
# _compute_agent_workload 测试
# ============================================================


class TestAgentWorkload:
    async def test_workload_with_mixed_stages(self, db_session, v3_tables):
        """混合阶段 Agent 的工作量指标。"""
        eid = await _seed_enterprise(db_session)
        await _seed_agent(db_session, eid, lifecycle_stage="production", name="生产1")
        await _seed_agent(db_session, eid, lifecycle_stage="production", name="生产2")
        await _seed_agent(db_session, eid, lifecycle_stage="recruit", name="招募1")

        workload = await org_analytics._compute_agent_workload(db_session, eid)

        assert workload["total_agents"] == 3
        assert workload["stage_distribution"]["production"] == 2
        assert workload["stage_distribution"]["recruit"] == 1
        assert workload["production_rate"] == round(2 / 3, 3)

    async def test_workload_empty_enterprise(self, db_session, v3_tables):
        """无 Agent 的工作量指标。"""
        eid = await _seed_enterprise(db_session)

        workload = await org_analytics._compute_agent_workload(db_session, eid)

        assert workload["total_agents"] == 0
        assert workload["production_rate"] == 0.0


# ============================================================
# _compute_process_efficiency 测试
# ============================================================


class TestProcessEfficiency:
    async def test_efficiency_with_mixed_events(self, db_session, v3_tables):
        """混合事件状态的流程效率指标。"""
        eid = await _seed_enterprise(db_session)
        await _seed_event(db_session, eid, event_type="inquiry_received", status="processed")
        await _seed_event(db_session, eid, event_type="quotation_generated", status="processed")
        await _seed_event(db_session, eid, event_type="approval_approved", status="pending")

        efficiency = await org_analytics._compute_process_efficiency(db_session, eid)

        assert efficiency["total_events"] == 3
        assert efficiency["processed_count"] == 2
        assert efficiency["automation_rate"] == round(2 / 3, 3)
        assert "inquiry_received" in efficiency["event_type_distribution"]
        assert "quotation_generated" in efficiency["event_type_distribution"]

    async def test_efficiency_empty(self, db_session, v3_tables):
        """无事件的流程效率。"""
        eid = await _seed_enterprise(db_session)

        efficiency = await org_analytics._compute_process_efficiency(db_session, eid)

        assert efficiency["total_events"] == 0
        assert efficiency["automation_rate"] == 0.0


# ============================================================
# _compute_tool_usage 测试
# ============================================================


class TestToolUsage:
    async def test_tool_usage_falls_back_when_runtime_unavailable(self, db_session, v3_tables):
        """Runtime 不可用时工具使用指标优雅降级（返回 0）。"""
        eid = await _seed_enterprise(db_session)

        # runtime_query.get_tool_registry 在无 Runtime 时会抛异常或返回空
        # service 层已捕获异常并返回 0 值
        tool_usage = await org_analytics._compute_tool_usage(db_session, eid)

        assert "total_tools" in tool_usage
        assert "installed" in tool_usage
        assert "verified" in tool_usage
        assert "install_rate" in tool_usage
        assert tool_usage["total_tools"] >= 0


# ============================================================
# _compute_business_impact 测试
# ============================================================


class TestBusinessImpact:
    async def test_impact_with_quotation_events(self, db_session, v3_tables):
        """报价事件含金额时计算业务贡献。"""
        eid = await _seed_enterprise(db_session)
        await _seed_event(
            db_session, eid, event_type="quotation_generated",
            payload={"amount": 128400, "customer": "华智"},
        )
        await _seed_event(
            db_session, eid, event_type="deal_closed",
            payload={"amount": 50000, "customer": "智链"},
        )
        await _seed_event(
            db_session, eid, event_type="inquiry_received",
            payload={"inquiry": "询盘"},  # 无 amount 字段
        )

        # process_efficiency 需要先计算（business_impact 依赖它）
        process_efficiency = await org_analytics._compute_process_efficiency(db_session, eid)
        impact = await org_analytics._compute_business_impact(
            db_session, eid, process_efficiency
        )

        assert impact["deal_count"] == 2  # quotation_generated + deal_closed
        assert impact["total_value"] == 178400.0
        assert impact["avg_deal_value"] == 89200.0

    async def test_impact_empty(self, db_session, v3_tables):
        """无成交事件的业务贡献。"""
        eid = await _seed_enterprise(db_session)

        impact = await org_analytics._compute_business_impact(
            db_session, eid, {"total_events": 0}
        )

        assert impact["deal_count"] == 0
        assert impact["total_value"] == 0.0
        assert impact["avg_deal_value"] == 0.0
