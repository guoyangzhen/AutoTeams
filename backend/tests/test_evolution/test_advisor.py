"""AI Advisor 测试（PRD §5.4，4 类建议 + 一键应用）。

测试覆盖：
- generate_suggestions：LLM 生成 + 规则化回退
- apply_suggestion：应用建议
- reject_suggestion：拒绝建议
- list_suggestions：分页查询
- LLM 全 mock

LLM mock 策略：advisor.py 通过 `from app.services.llm_service import llm_service`
将 llm_service 绑定到模块命名空间，故需 patch `app.services.evolution.advisor.llm_service`
（消费者模块引用），而非源模块 `app.services.llm_service.llm_service`。
这与 test_copilot.py 的 mock 模式一致。
"""
import json
import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from app.services.evolution.advisor import (
    AdvisorService,
    advisor_service,
    TYPE_KNOWLEDGE,
    TYPE_PROCESS,
    TYPE_CAPABILITY,
    TYPE_ORGANIZATION,
)
from app.models.evolution import AdvisorSuggestion

# 共享 fixtures（conftest_extensions 未被 pytest 自动发现，需显式导入）


# ============================================================
# 测试辅助
# ============================================================


@contextmanager
def _mock_advisor_llm(reply=None, side_effect=None):
    """统一 mock advisor 模块级 llm_service.chat。

    - reply: 设置 return_value（优先级高于 side_effect）
    - side_effect: 设置 side_effect（如抛异常）
    - 二者均不传：返回默认占位响应（仅防止真实 LLM 调用）
    """
    with patch("app.services.evolution.advisor.llm_service") as mock_llm:
        if side_effect is not None:
            mock_llm.chat = AsyncMock(side_effect=side_effect)
        else:
            mock_llm.chat = AsyncMock(return_value=reply or "{}")
        yield mock_llm


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


async def _seed_agent(db, enterprise_id):
    from app.models.agent import Agent

    agent = Agent(
        enterprise_id=enterprise_id,
        name="测试 Agent",
        description="测试用",
        system_prompt="你是测试 Agent",
        status="ready",
        version="1.0.0",
        config={},
        lifecycle_stage="production",
    )
    db.add(agent)
    await db.commit()
    return agent


# ============================================================
# generate_suggestions 测试
# ============================================================


class TestGenerateSuggestions:
    async def test_generate_via_llm_returns_4_types(
        self, db_session, v3_tables
    ):
        """LLM 生成 4 类建议。"""
        eid = await _seed_enterprise(db_session)

        # mock LLM 返回 4 类建议
        llm_response = json.dumps([
            {"type": "knowledge", "title": "知识补充", "description": "缺少产品规格知识", "impact": "提升准确率"},
            {"type": "process", "title": "流程优化", "description": "审批流程瓶颈", "impact": "缩短时长"},
            {"type": "capability", "title": "能力增强", "description": "新增邮件能力", "impact": "覆盖新场景"},
            {"type": "organization", "title": "组织调整", "description": "销售负载不均", "impact": "提升产能"},
        ], ensure_ascii=False)

        with _mock_advisor_llm(reply=llm_response):
            result = await advisor_service.generate_suggestions(db_session, eid)

        assert len(result) == 4
        types = {r["type"] for r in result}
        assert types == {"knowledge", "process", "capability", "organization"}

        # 验证持久化
        from sqlalchemy import select
        db_result = await db_session.execute(
            select(AdvisorSuggestion).where(AdvisorSuggestion.enterprise_id == eid)
        )
        suggestions = db_result.scalars().all()
        assert len(suggestions) == 4
        assert all(s.status == "pending" for s in suggestions)

    async def test_generate_falls_back_to_rule_based_when_llm_fails(
        self, db_session, v3_tables
    ):
        """LLM 失败时回退到规则化建议。"""
        eid = await _seed_enterprise(db_session)

        # mock LLM 抛出异常
        with _mock_advisor_llm(side_effect=RuntimeError("LLM 不可用")):
            result = await advisor_service.generate_suggestions(db_session, eid)

        # 应至少有 1 条规则化建议
        assert len(result) >= 1
        # 兜底建议为 organization 类型
        assert any(r["type"] == "organization" for r in result)

    async def test_generate_falls_back_to_rule_based_when_llm_returns_empty(
        self, db_session, v3_tables
    ):
        """LLM 返回空列表时回退到规则化建议。"""
        eid = await _seed_enterprise(db_session)

        with _mock_advisor_llm(reply="[]"):
            result = await advisor_service.generate_suggestions(db_session, eid)

        assert len(result) >= 1

    async def test_generate_handles_invalid_llm_response(
        self, db_session, v3_tables
    ):
        """LLM 返回非 JSON 时回退到规则化建议。"""
        eid = await _seed_enterprise(db_session)

        with _mock_advisor_llm(reply="这不是 JSON"):
            result = await advisor_service.generate_suggestions(db_session, eid)

        # 应回退到规则化建议
        assert len(result) >= 1


# ============================================================
# apply_suggestion 测试
# ============================================================


class TestApplySuggestion:
    async def test_apply_pending_suggestion(self, db_session, v3_tables):
        """应用 pending 状态的建议。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)

        # 创建一条 pending 建议
        suggestion = AdvisorSuggestion(
            enterprise_id=eid,
            type=TYPE_KNOWLEDGE,
            title="测试建议",
            description="测试描述",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        result = await advisor_service.apply_suggestion(
            db_session, suggestion.id
        )

        assert result["applied"] is True
        assert result["suggestion_id"] == suggestion.id
        assert agent.id in result["affected_agents"]

        # 验证 DB 状态
        await db_session.refresh(suggestion)
        assert suggestion.status == "applied"
        assert suggestion.applied_at is not None

    async def test_apply_nonexistent_suggestion_raises(
        self, db_session, v3_tables
    ):
        """应用不存在的建议抛出 ValueError。"""
        await _seed_enterprise(db_session)
        with pytest.raises(ValueError, match="不存在"):
            await advisor_service.apply_suggestion(db_session, "invalid-id")

    async def test_apply_already_applied_suggestion_raises(
        self, db_session, v3_tables
    ):
        """重复应用已应用的建议抛出 ValueError。"""
        eid = await _seed_enterprise(db_session)
        suggestion = AdvisorSuggestion(
            enterprise_id=eid,
            type=TYPE_KNOWLEDGE,
            title="已应用建议",
            description="测试",
            status="applied",
        )
        db_session.add(suggestion)
        await db_session.commit()

        with pytest.raises(ValueError, match="已应用"):
            await advisor_service.apply_suggestion(db_session, suggestion.id)

    async def test_apply_rejected_suggestion_raises(self, db_session, v3_tables):
        """应用已拒绝的建议抛出 ValueError。"""
        eid = await _seed_enterprise(db_session)
        suggestion = AdvisorSuggestion(
            enterprise_id=eid,
            type=TYPE_PROCESS,
            title="已拒绝建议",
            description="测试",
            status="rejected",
        )
        db_session.add(suggestion)
        await db_session.commit()

        with pytest.raises(ValueError, match="已拒绝"):
            await advisor_service.apply_suggestion(db_session, suggestion.id)

    async def test_apply_organization_type_affects_production_agents_only(
        self, db_session, v3_tables
    ):
        """organization 类型建议仅影响 production 阶段 Agent。"""
        eid = await _seed_enterprise(db_session)
        # 创建 2 个 production Agent + 1 个 recruit Agent
        from app.models.agent import Agent

        prod_agent1 = Agent(
            enterprise_id=eid, name="生产1", description="",
            system_prompt="x", status="ready", version="1.0.0", config={},
            lifecycle_stage="production",
        )
        prod_agent2 = Agent(
            enterprise_id=eid, name="生产2", description="",
            system_prompt="x", status="ready", version="1.0.0", config={},
            lifecycle_stage="production",
        )
        recruit_agent = Agent(
            enterprise_id=eid, name="招募", description="",
            system_prompt="x", status="ready", version="1.0.0", config={},
            lifecycle_stage="recruit",
        )
        db_session.add_all([prod_agent1, prod_agent2, recruit_agent])
        await db_session.commit()

        suggestion = AdvisorSuggestion(
            enterprise_id=eid,
            type=TYPE_ORGANIZATION,
            title="组织调整建议",
            description="测试",
            status="pending",
        )
        db_session.add(suggestion)
        await db_session.commit()

        result = await advisor_service.apply_suggestion(db_session, suggestion.id)

        # 仅影响 production Agent
        affected = set(result["affected_agents"])
        assert prod_agent1.id in affected
        assert prod_agent2.id in affected
        assert recruit_agent.id not in affected


# ============================================================
# reject_suggestion 测试
# ============================================================


class TestRejectSuggestion:
    async def test_reject_pending_suggestion(self, db_session, v3_tables):
        """拒绝 pending 状态的建议。"""
        eid = await _seed_enterprise(db_session)
        suggestion = AdvisorSuggestion(
            enterprise_id=eid,
            type=TYPE_PROCESS,
            title="待拒绝建议",
            description="测试",
            status="pending",
            impact="原始影响",
        )
        db_session.add(suggestion)
        await db_session.commit()

        result = await advisor_service.reject_suggestion(
            db_session, suggestion.id, reason="优先级过低"
        )

        assert result["rejected"] is True

        await db_session.refresh(suggestion)
        assert suggestion.status == "rejected"
        assert "优先级过低" in suggestion.impact

    async def test_reject_nonexistent_raises(self, db_session, v3_tables):
        """拒绝不存在的建议抛出 ValueError。"""
        await _seed_enterprise(db_session)
        with pytest.raises(ValueError, match="不存在"):
            await advisor_service.reject_suggestion(db_session, "invalid-id")

    async def test_reject_already_processed_raises(self, db_session, v3_tables):
        """拒绝已处理（非 pending）的建议抛出 ValueError。"""
        eid = await _seed_enterprise(db_session)
        suggestion = AdvisorSuggestion(
            enterprise_id=eid,
            type=TYPE_PROCESS,
            title="已应用建议",
            description="测试",
            status="applied",
        )
        db_session.add(suggestion)
        await db_session.commit()

        with pytest.raises(ValueError, match="无法拒绝"):
            await advisor_service.reject_suggestion(db_session, suggestion.id)


# ============================================================
# list_suggestions 测试
# ============================================================


class TestListSuggestions:
    async def test_list_pagination(self, db_session, v3_tables):
        """分页查询。"""
        eid = await _seed_enterprise(db_session)

        # 创建 5 条建议
        for i in range(5):
            db_session.add(AdvisorSuggestion(
                enterprise_id=eid,
                type=TYPE_KNOWLEDGE,
                title=f"建议 {i}",
                description="测试",
                status="pending",
            ))
        await db_session.commit()

        items, total = await advisor_service.list_suggestions(
            db_session, eid, limit=2, offset=0
        )
        assert total == 5
        assert len(items) == 2

        items_p2, total = await advisor_service.list_suggestions(
            db_session, eid, limit=2, offset=2
        )
        assert len(items_p2) == 2

    async def test_list_filter_by_status(self, db_session, v3_tables):
        """按 status 过滤。"""
        eid = await _seed_enterprise(db_session)

        db_session.add(AdvisorSuggestion(
            enterprise_id=eid, type=TYPE_KNOWLEDGE,
            title="pending 1", description="", status="pending",
        ))
        db_session.add(AdvisorSuggestion(
            enterprise_id=eid, type=TYPE_PROCESS,
            title="applied 1", description="", status="applied",
        ))
        await db_session.commit()

        pending_items, pending_total = await advisor_service.list_suggestions(
            db_session, eid, status="pending"
        )
        applied_items, applied_total = await advisor_service.list_suggestions(
            db_session, eid, status="applied"
        )

        assert pending_total == 1
        assert applied_total == 1
        assert pending_items[0].title == "pending 1"
        assert applied_items[0].title == "applied 1"

    async def test_list_empty(self, db_session, v3_tables):
        """无建议时返回空。"""
        eid = await _seed_enterprise(db_session)
        items, total = await advisor_service.list_suggestions(db_session, eid)
        assert total == 0
        assert items == []
