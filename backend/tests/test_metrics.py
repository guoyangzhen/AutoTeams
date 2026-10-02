"""业务效果指标（metrics）测试。

覆盖：
1. test_get_business_metrics_aggregation —— 服务层聚合准确性
   构造对话/消息/满意度/RAG 评估/技能执行数据，校验 efficiency/cost/coverage/quality/trends
2. test_enterprise_isolation —— 企业隔离（直接测 API 层 _verify_agent_access）
   A 企业用户访问 B 企业 Agent 应抛 404；访问本企业 Agent 应放行
3. test_kpi_model_constraints —— AgentKPI 唯一约束与字段默认值
   同一 (agent_id, period_type, period_start) 重复插入应抛 IntegrityError

说明：内存 SQLite 不同连接互不可见，故 API 层隔离测试直接调用 _verify_agent_access
函数（与 HTTP 端点使用的同一个鉴权函数），避免 db_session 与 client 跨连接数据不可见
导致的误判。聚合与模型测试均在单一 db_session 内完成。
"""
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.models.enterprise import Enterprise
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.rag_evaluation import RAGEvaluation
from app.models.skill import Skill
from app.models.skill_execution import SkillExecution
from app.models.agent_kpi import AgentKPI
from app.services.metrics_service import (
    metrics_service,
    COST_PER_1K_TOKENS_YUAN,
    HUMAN_LOOKUP_MINUTES,
    HUMAN_COST_PER_MINUTE_YUAN,
    REASONING_TOKENS_PER_CONVERSATION,
    COST_PER_TOOL_CALL_YUAN,
    COST_PER_RETRIEVAL_YUAN,
    AVG_RETRIEVALS_PER_CONVERSATION,
    MONTHLY_CONVERSATIONS_PER_EMPLOYEE,
)
from app.api.metrics import _verify_agent_access


# ==================== 辅助构造函数 ====================


async def _make_enterprise_user_agent(db_session, email: str, ent_name: str = "测试企业"):
    """创建企业 + 用户 + Agent，返回 (enterprise, user, agent)。"""
    enterprise = Enterprise(name=ent_name)
    db_session.add(enterprise)
    await db_session.flush()
    await db_session.refresh(enterprise)

    user = User(
        email=email,
        password_hash="hash",
        name="测试用户",
        enterprise_id=enterprise.id,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    agent = Agent(
        enterprise_id=enterprise.id,
        name="业务指标测试助手",
        description="",
        system_prompt="你是助手",
        config={},
        version="1.0.0",
        status="active",
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.refresh(agent)
    return enterprise, user, agent


async def _add_message(
    db_session, conversation, role, content="x", satisfaction=None, token_count=0,
    created_at=None,
):
    """插入一条消息，返回该消息。"""
    msg = Message(
        conversation_id=conversation.id,
        role=role,
        content=content,
        satisfaction=satisfaction,
        token_count=token_count,
        is_deleted=False,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(msg)
    await db_session.flush()
    return msg


# ==================== 1. 聚合准确性测试 ====================


class TestBusinessMetricsAggregation:
    """服务层聚合指标准确性。"""

    @pytest.mark.asyncio
    async def test_get_business_metrics_aggregation(self, db_session):
        """构造已知数据，校验 efficiency/cost/coverage/quality/trends 聚合结果。"""
        enterprise, user, agent = await _make_enterprise_user_agent(
            db_session, email="agg@test.com"
        )

        now = datetime.now(timezone.utc)
        # 构造 2 个会话：
        #   会话1：user 提问 + assistant 回复（satisfied，token=200）→ 已解决、已回答
        #   会话2：user 提问 + assistant 回复（unsatisfied，token=400）→ 转人工、已回答
        conv1 = Conversation(user_id=user.id, agent_id=agent.id, title="c1")
        conv2 = Conversation(user_id=user.id, agent_id=agent.id, title="c2")
        db_session.add_all([conv1, conv2])
        await db_session.flush()

        # 会话1
        u1 = await _add_message(
            db_session, conv1, role="user", content="问题1", created_at=now - timedelta(minutes=10)
        )
        a1 = await _add_message(
            db_session, conv1, role="assistant", content="回答1",
            satisfaction="satisfied", token_count=200, created_at=now - timedelta(minutes=9),
        )
        # 会话2
        await _add_message(
            db_session, conv2, role="user", content="问题2", created_at=now - timedelta(minutes=5)
        )
        a2 = await _add_message(
            db_session, conv2, role="assistant", content="回答2",
            satisfaction="unsatisfied", token_count=400, created_at=now - timedelta(minutes=4),
        )

        # RAG 评估（4 项均为 0.8 → accuracy=0.8）
        db_session.add(RAGEvaluation(
            agent_id=agent.id, conversation_id=conv1.id, message_id=a1.id,
            faithfulness=0.8, answer_relevancy=0.8, context_precision=0.8, context_recall=0.8,
            evaluated_at=now,
        ))

        # 技能执行（成功 3 次）
        skill = Skill(
            agent_id=agent.id, name="FAQ检索", skill_type="retrieval",
            source="manual", status="approved",
        )
        db_session.add(skill)
        await db_session.flush()
        for _ in range(3):
            db_session.add(SkillExecution(
                skill_id=skill.id, user_id=user.id, agent_id=agent.id,
                status="success", execution_time_ms=120,
                created_at=now,
            ))

        await db_session.commit()

        result = await metrics_service.get_business_metrics(
            db_session, agent_id=agent.id, enterprise_id=enterprise.id, range_days=30,
        )

        # ---- summary ----
        assert result["summary"]["total_conversations"] == 2
        assert result["summary"]["total_questions"] == 2
        assert result["summary"]["total_tokens"] == 600  # 200 + 400
        assert result["summary"]["resolved_count"] == 1  # 会话1 已解决

        # ---- efficiency ----
        eff = result["efficiency"]
        assert eff["resolution_rate"] == 0.5          # 1/2
        assert eff["escalation_rate"] == 0.5          # 1/2
        assert eff["human_compare_minutes"] == HUMAN_LOOKUP_MINUTES
        # 节省人力：1 个已解决 × (5 - ai分钟) / 60，ai 分钟基于 token 分桶
        assert eff["hours_saved"] >= 0.0

        # ---- cost ----
        cost = result["cost"]
        assert cost["total_tokens"] == 600
        assert cost["tool_call_count"] == 3     # 3 次成功 SkillExecution

        # 新成本模型：输出 token 实测 + 推理/思考 + 工具调用 + 知识检索
        output_token_cost = 600 / 1000.0 * COST_PER_1K_TOKENS_YUAN
        reasoning_cost = 2 * REASONING_TOKENS_PER_CONVERSATION / 1000.0 * COST_PER_1K_TOKENS_YUAN
        tool_cost = 3 * COST_PER_TOOL_CALL_YUAN
        retrieval_cost = 2 * AVG_RETRIEVALS_PER_CONVERSATION * COST_PER_RETRIEVAL_YUAN
        expected_total_cost = round(
            output_token_cost + reasoning_cost + tool_cost + retrieval_cost, 4
        )
        assert cost["total_cost_yuan"] == expected_total_cost
        assert cost["per_conversation_cost_yuan"] == round(expected_total_cost / 2, 4)
        # 校准到单次协作约 0.8~3 元
        assert 0.8 <= cost["per_conversation_cost_yuan"] <= 3.0
        # 启用 AI 前基线：1 个已解决 × 5 分钟 × 1 元/分钟 = 5 元
        assert cost["pre_ai_cost_yuan"] == round(
            1 * HUMAN_LOOKUP_MINUTES * HUMAN_COST_PER_MINUTE_YUAN, 2
        )
        assert cost["cost_reduction_pct"] > 0.0
        assert cost["cost_per_1k_tokens_yuan"] == COST_PER_1K_TOKENS_YUAN
        # 月估算：单次成本 × 典型月工作量，落在 100~330 元区间
        assert round(cost["estimated_monthly_cost_yuan"], 2) == round(
            (expected_total_cost / 2) * MONTHLY_CONVERSATIONS_PER_EMPLOYEE, 2
        )
        assert 100 <= cost["estimated_monthly_cost_yuan"] <= 330

        # ---- coverage ----
        cov = result["coverage"]
        assert cov["total_questions"] == 2
        assert cov["answered_questions"] == 2          # 两个 user 问题都有 assistant 回复
        assert cov["knowledge_coverage"] == 1.0
        assert len(cov["skill_top5"]) == 1
        assert cov["skill_top5"][0]["name"] == "FAQ检索"
        assert cov["skill_top5"][0]["count"] == 3

        # ---- quality ----
        qual = result["quality"]
        assert qual["accuracy"] == 0.8
        # satisfied(5) + unsatisfied(2) → 均值 3.5
        assert qual["satisfaction_score"] == 3.5
        assert qual["rated_count"] == 2
        assert qual["satisfaction_rate"] == 0.5         # 1 satisfied / 2 rated

        # ---- trends ----
        trends = result["trends"]
        assert len(trends["daily_counts"]) == 30       # 补齐 30 天
        assert sum(d["count"] for d in trends["daily_counts"]) == 2
        assert len(trends["satisfaction_trend"]) == 30

    @pytest.mark.asyncio
    async def test_empty_result_when_no_agents(self, db_session):
        """无智能体时应返回零值骨架，不抛异常。"""
        enterprise = Enterprise(name="空企业")
        db_session.add(enterprise)
        await db_session.flush()
        await db_session.commit()

        result = await metrics_service.get_business_metrics(
            db_session, agent_id=None, enterprise_id=enterprise.id, range_days=7,
        )
        assert result["summary"]["total_conversations"] == 0
        assert result["cost"]["total_cost_yuan"] == 0.0
        assert result["coverage"]["knowledge_coverage"] == 0.0
        assert result["trends"]["daily_counts"] == []

    @pytest.mark.asyncio
    async def test_enterprise_scope_aggregation_service(self, db_session):
        """服务层：按 enterprise_id 聚合时只包含本企业智能体，不泄露其他企业数据。"""
        # 企业 A：1 个 Agent + 1 个会话
        ent_a, user_a, agent_a = await _make_enterprise_user_agent(
            db_session, email="scope-a@test.com", ent_name="范围企业A"
        )
        conv = Conversation(user_id=user_a.id, agent_id=agent_a.id, title="A会话")
        db_session.add(conv)
        await db_session.flush()
        db_session.add(Message(
            conversation_id=conv.id, role="user", content="q", token_count=0,
        ))
        db_session.add(Message(
            conversation_id=conv.id, role="assistant", content="a",
            token_count=100, satisfaction="satisfied",
        ))

        # 企业 B：1 个 Agent + 10 个会话（不应被聚合到 A 的结果中）
        ent_b, user_b, agent_b = await _make_enterprise_user_agent(
            db_session, email="scope-b@test.com", ent_name="范围企业B"
        )
        for _ in range(10):
            db_session.add(Conversation(user_id=user_b.id, agent_id=agent_b.id, title="B会话"))
        await db_session.commit()

        result = await metrics_service.get_business_metrics(
            db_session, agent_id=None, enterprise_id=ent_a.id, range_days=30,
        )
        # 仅聚合 A 企业的 1 个会话，B 企业的 10 个不应出现
        assert result["summary"]["total_conversations"] == 1
        assert result["summary"]["total_tokens"] == 100


# ==================== 2. 企业隔离测试 ====================


class TestEnterpriseIsolation:
    """企业隔离：直接测 API 层 _verify_agent_access（与 HTTP 端点同一鉴权函数）。

    内存 SQLite 不同连接互不可见，故不通过 HTTP client 混用 db_session，
    而是直接调用鉴权函数，确保隔离逻辑本身被验证。
    """

    @pytest.mark.asyncio
    async def test_enterprise_isolation(self, db_session):
        """A 企业用户访问 B 企业 Agent 应抛 404；访问本企业 Agent 应放行。"""
        ent_a, user_a, agent_a = await _make_enterprise_user_agent(
            db_session, email="iso-a@test.com", ent_name="企业A"
        )
        ent_b, user_b, agent_b = await _make_enterprise_user_agent(
            db_session, email="iso-b@test.com", ent_name="企业B"
        )
        await db_session.commit()

        # A 企业用户访问 B 企业的 Agent → 应抛 404
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await _verify_agent_access(db_session, agent_b.id, user_a)
        assert exc_info.value.status_code == 404

        # A 企业用户访问本企业 Agent → 应放行并返回该 Agent
        agent = await _verify_agent_access(db_session, agent_a.id, user_a)
        assert agent.id == agent_a.id
        assert agent.enterprise_id == ent_a.id

    @pytest.mark.asyncio
    async def test_super_admin_bypasses_isolation(self, db_session):
        """超级管理员（enterprise_id=None）应能访问任意企业 Agent。"""
        ent_a, user_a, agent_a = await _make_enterprise_user_agent(
            db_session, email="admin-iso@test.com", ent_name="管理员隔离测试企业"
        )
        await db_session.commit()

        # 构造超级管理员：enterprise_id=None
        super_admin = User(
            email="superadmin@test.com", password_hash="hash", name="超管",
            enterprise_id=None, role="admin",
        )
        db_session.add(super_admin)
        await db_session.commit()

        agent = await _verify_agent_access(db_session, agent_a.id, super_admin)
        assert agent.id == agent_a.id

    @pytest.mark.asyncio
    async def test_nonexistent_agent_returns_404(self, db_session):
        """访问不存在的 Agent 应返回 404。"""
        ent_a, user_a, agent_a = await _make_enterprise_user_agent(
            db_session, email="nonexist@test.com", ent_name="不存在Agent测试企业"
        )
        await db_session.commit()

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await _verify_agent_access(db_session, "nonexistent-agent-id", user_a)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unauthorized_http_access(self, client):
        """未认证访问业务指标端点应返回 401/403。"""
        resp = await client.get("/api/v1/metrics/business")
        assert resp.status_code in (401, 403)


# ==================== 3. AgentKPI 模型约束测试 ====================


class TestAgentKpiModelConstraints:
    """AgentKPI 唯一约束与字段默认值。"""

    @pytest.mark.asyncio
    async def test_kpi_model_constraints(self, db_session):
        """同一 (agent_id, period_type, period_start) 重复插入应抛 IntegrityError。"""
        enterprise, user, agent = await _make_enterprise_user_agent(
            db_session, email="kpi@test.com"
        )

        period_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        period_end = period_start + timedelta(days=1)

        kpi1 = AgentKPI(
            agent_id=agent.id,
            enterprise_id=enterprise.id,
            period_type="daily",
            period_start=period_start,
            period_end=period_end,
            task_count=10,
            avg_response_time=500.0,
            first_resolution_rate=0.8,
            escalation_rate=0.2,
            accuracy=0.85,
            satisfaction_score=4.2,
            token_usage=12000,
            estimated_cost=0.096,
            business_metrics={"ticket_close_rate": 0.75},
        )
        db_session.add(kpi1)
        await db_session.commit()

        # 重复 (agent_id, period_type, period_start) → 违反唯一约束
        kpi2 = AgentKPI(
            agent_id=agent.id,
            enterprise_id=enterprise.id,
            period_type="daily",
            period_start=period_start,        # 相同起始日
            period_end=period_end,
            task_count=20,
        )
        db_session.add(kpi2)
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_kpi_different_periods_allowed(self, db_session):
        """同一 Agent 不同周期类型/起始日应可共存。"""
        enterprise, user, agent = await _make_enterprise_user_agent(
            db_session, email="kpi2@test.com"
        )
        base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        # daily + weekly 同一起始日，因 period_type 不同应可共存
        db_session.add(AgentKPI(
            agent_id=agent.id, enterprise_id=enterprise.id,
            period_type="daily", period_start=base, period_end=base + timedelta(days=1),
        ))
        db_session.add(AgentKPI(
            agent_id=agent.id, enterprise_id=enterprise.id,
            period_type="weekly", period_start=base, period_end=base + timedelta(days=7),
        ))
        await db_session.commit()

        rows = (
            await db_session.execute(
                select(AgentKPI).where(AgentKPI.agent_id == agent.id)
            )
        ).scalars().all()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_kpi_defaults(self, db_session):
        """未显式赋值时，数值字段应取模型默认值（非 NULL）。"""
        enterprise, user, agent = await _make_enterprise_user_agent(
            db_session, email="kpi3@test.com"
        )
        base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        kpi = AgentKPI(
            agent_id=agent.id,
            enterprise_id=enterprise.id,
            period_type="monthly",
            period_start=base,
            period_end=base + timedelta(days=30),
        )
        db_session.add(kpi)
        await db_session.commit()
        await db_session.refresh(kpi)

        assert kpi.task_count == 0
        assert kpi.avg_response_time == 0.0
        assert kpi.first_resolution_rate == 0.0
        assert kpi.escalation_rate == 0.0
        assert kpi.accuracy == 0.0
        assert kpi.satisfaction_score == 0.0
        assert kpi.token_usage == 0
        assert kpi.estimated_cost == 0.0
        assert kpi.id  # TimestampMixin + 主键应已生成
        assert kpi.created_at is not None
