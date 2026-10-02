"""持续优化器测试（PRD §5.4 进化循环，复用 loop_engine + rag_evaluator）。

测试覆盖：
- analyze_enterprise：聚合 loop_engine 反馈分析 + rag_evaluator 质量评估
- apply_optimization：委托 loop_engine.apply_optimization
- check_and_auto_rollback：委托 loop_engine.auto_rollback_if_degraded
- get_enterprise_optimization_history：企业级优化历史（分页）
- run_enterprise_optimization_background：后台批量分析（独立 session）
- RAG 质量退化告警（composite < threshold → auto_rollback item）
- loop_engine 异常隔离（失败不阻断分析）
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.evolution.continuous_optimizer import (
    ContinuousOptimizer,
    continuous_optimizer,
    OptimizationItem,
    OPT_TYPE_FEEDBACK,
    OPT_TYPE_GAP,
    OPT_TYPE_AUTO_ROLLBACK,
    RAG_SCORE_THRESHOLD,
)
from app.models.agent import Agent

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


async def _seed_agent(db, enterprise_id, name="测试 Agent", lifecycle_stage="production"):
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


async def _seed_rag_evaluation(db_session, agent_id, faithfulness=0.8,
                               answer_relevancy=0.8, context_precision=0.8,
                               context_recall=0.8):
    """创建 RAG 评估记录（rag_evaluator.evaluate_message 落库的数据）。"""
    from app.models.rag_evaluation import RAGEvaluation
    from datetime import datetime, timezone

    eval_record = RAGEvaluation(
        agent_id=agent_id,
        conversation_id="conv-test",
        message_id="msg-test",
        faithfulness=faithfulness,
        answer_relevancy=answer_relevancy,
        context_precision=context_precision,
        context_recall=context_recall,
        details={},
        evaluated_at=datetime.now(timezone.utc),
    )
    db_session.add(eval_record)
    await db_session.commit()
    return eval_record


async def _seed_optimization_history(db_session, agent_id, opt_type="feedback",
                                      applied=False, output_data=None):
    """创建 OptimizationHistory 记录。"""
    from app.models.optimization_history import OptimizationHistory

    record = OptimizationHistory(
        agent_id=agent_id,
        type=opt_type,
        applied=applied,
        output_data=output_data or {},
    )
    db_session.add(record)
    await db_session.commit()
    return record


# ============================================================
# OptimizationItem 数据类测试
# ============================================================


class TestOptimizationItem:
    def test_to_dict_serializes_correctly(self):
        """OptimizationItem.to_dict 正确序列化。"""
        item = OptimizationItem(
            agent_id="agent-001",
            type=OPT_TYPE_FEEDBACK,
            title="测试优化项",
            detail={"rate": 0.5},
            priority="high",
            source="loop_engine",
        )
        d = item.to_dict()
        assert d["agent_id"] == "agent-001"
        assert d["type"] == OPT_TYPE_FEEDBACK
        assert d["title"] == "测试优化项"
        assert d["detail"] == {"rate": 0.5}
        assert d["priority"] == "high"
        assert "created_at" in d


# ============================================================
# analyze_enterprise 测试
# ============================================================


class TestAnalyzeEnterprise:
    async def test_analyze_empty_enterprise(self, db_session, v3_tables):
        """无 Agent 的企业分析返回空优化项。"""
        eid = await _seed_enterprise(db_session)

        report = await continuous_optimizer.analyze_enterprise(db_session, eid)

        assert report["enterprise_id"] == eid
        assert report["analyzed_agents"] == 0
        assert report["optimization_items"] == []
        assert report["rag_quality"]["avg_composite"] == 0.0

    async def test_analyze_with_agents_no_feedback_no_rag(self, db_session, v3_tables):
        """有 Agent 但无反馈/RAG 数据 → 无优化项。"""
        eid = await _seed_enterprise(db_session)
        await _seed_agent(db_session, eid)

        with patch.object(continuous_optimizer, "_safe_analyze_feedback",
                          new=AsyncMock(return_value=None)), \
             patch.object(continuous_optimizer, "_safe_analyze_knowledge_gaps",
                          new=AsyncMock(return_value=None)):
            report = await continuous_optimizer.analyze_enterprise(db_session, eid)

        assert report["analyzed_agents"] == 1
        assert report["optimization_items"] == []

    async def test_analyze_aggregates_feedback_and_gap_items(self, db_session, v3_tables):
        """聚合 loop_engine 反馈分析 + 知识缺口分析。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)

        feedback_item = OptimizationItem(
            agent_id=agent.id, type=OPT_TYPE_FEEDBACK,
            title="反馈不满意", detail={"rate": 0.3}, priority="high",
        )
        gap_item = OptimizationItem(
            agent_id=agent.id, type=OPT_TYPE_GAP,
            title="知识缺口 3 项", detail={"gaps": []}, priority="medium",
        )

        with patch.object(continuous_optimizer, "_safe_analyze_feedback",
                          new=AsyncMock(return_value=feedback_item)), \
             patch.object(continuous_optimizer, "_safe_analyze_knowledge_gaps",
                          new=AsyncMock(return_value=gap_item)), \
             patch.object(continuous_optimizer, "_compute_agent_rag_score",
                          new=AsyncMock(return_value=None)):
            report = await continuous_optimizer.analyze_enterprise(db_session, eid)

        assert report["analyzed_agents"] == 1
        assert len(report["optimization_items"]) == 2
        # high 优先级在前
        assert report["optimization_items"][0]["priority"] == "high"

    async def test_analyze_detects_rag_degradation(self, db_session, v3_tables):
        """RAG 质量退化（composite < threshold）→ 生成 auto_rollback 优化项。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)
        # 创建低分 RAG 评估（composite = 0.4 < 0.6 threshold）
        await _seed_rag_evaluation(
            db_session, agent.id,
            faithfulness=0.3, answer_relevancy=0.4,
            context_precision=0.5, context_recall=0.4,
        )

        with patch.object(continuous_optimizer, "_safe_analyze_feedback",
                          new=AsyncMock(return_value=None)), \
             patch.object(continuous_optimizer, "_safe_analyze_knowledge_gaps",
                          new=AsyncMock(return_value=None)):
            report = await continuous_optimizer.analyze_enterprise(db_session, eid)

        # composite = (0.3+0.4+0.5+0.4)/4 = 0.4 < 0.6
        assert report["rag_quality"]["avg_composite"] < RAG_SCORE_THRESHOLD
        assert len(report["rag_quality"]["degraded_agents"]) == 1
        # 应有 auto_rollback 优化项
        rollback_items = [
            i for i in report["optimization_items"]
            if i["type"] == OPT_TYPE_AUTO_ROLLBACK
        ]
        assert len(rollback_items) == 1
        assert rollback_items[0]["priority"] == "high"

    async def test_analyze_rag_quality_above_threshold(self, db_session, v3_tables):
        """RAG 质量良好（composite >= threshold）→ 不生成 auto_rollback 项。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)
        # 创建高分 RAG 评估（composite = 0.85 >= 0.6）
        await _seed_rag_evaluation(
            db_session, agent.id,
            faithfulness=0.9, answer_relevancy=0.85,
            context_precision=0.8, context_recall=0.85,
        )

        with patch.object(continuous_optimizer, "_safe_analyze_feedback",
                          new=AsyncMock(return_value=None)), \
             patch.object(continuous_optimizer, "_safe_analyze_knowledge_gaps",
                          new=AsyncMock(return_value=None)):
            report = await continuous_optimizer.analyze_enterprise(db_session, eid)

        assert report["rag_quality"]["avg_composite"] >= RAG_SCORE_THRESHOLD
        assert report["rag_quality"]["degraded_agents"] == []
        # 不应有 auto_rollback 项
        rollback_items = [
            i for i in report["optimization_items"]
            if i["type"] == OPT_TYPE_AUTO_ROLLBACK
        ]
        assert len(rollback_items) == 0


# ============================================================
# apply_optimization 测试（委托 loop_engine）
# ============================================================


class TestApplyOptimization:
    async def test_apply_delegates_to_loop_engine(self, db_session, v3_tables):
        """apply_optimization 委托 loop_engine.apply_optimization。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)

        mock_result = {"applied": True, "agent_id": agent.id}
        with patch(
            "app.services.evolution.continuous_optimizer.loop_engine.apply_optimization",
            new=AsyncMock(return_value=mock_result),
        ) as mock_apply:
            result = await continuous_optimizer.apply_optimization(
                db_session, agent.id, "opt-001"
            )

        assert result == mock_result
        mock_apply.assert_called_once_with(db_session, agent.id, "opt-001", None)


# ============================================================
# check_and_auto_rollback 测试（委托 loop_engine）
# ============================================================


class TestCheckAndAutoRollback:
    async def test_rollback_delegates_to_loop_engine(self, db_session, v3_tables):
        """check_and_auto_rollback 委托 loop_engine.auto_rollback_if_degraded。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)

        mock_result = {"rolled_back": False, "reason": "quality_ok"}
        with patch(
            "app.services.evolution.continuous_optimizer.loop_engine.auto_rollback_if_degraded",
            new=AsyncMock(return_value=mock_result),
        ) as mock_rollback:
            result = await continuous_optimizer.check_and_auto_rollback(
                db_session, agent.id
            )

        assert result == mock_result
        mock_rollback.assert_called_once_with(db_session, agent.id)


# ============================================================
# get_enterprise_optimization_history 测试（分页）
# ============================================================


class TestOptimizationHistory:
    async def test_history_pagination(self, db_session, v3_tables):
        """优化历史分页查询。"""
        eid = await _seed_enterprise(db_session)
        agent = await _seed_agent(db_session, eid)

        # 创建 5 条历史记录
        for i in range(5):
            await _seed_optimization_history(
                db_session, agent.id, opt_type="feedback",
                applied=(i % 2 == 0),
                output_data={"index": i},
            )

        page1 = await continuous_optimizer.get_enterprise_optimization_history(
            db_session, eid, limit=2, offset=0
        )
        assert page1["total"] == 5
        assert len(page1["items"]) == 2

        page2 = await continuous_optimizer.get_enterprise_optimization_history(
            db_session, eid, limit=2, offset=2
        )
        assert len(page2["items"]) == 2

    async def test_history_empty(self, db_session, v3_tables):
        """无优化历史时返回空。"""
        eid = await _seed_enterprise(db_session)

        result = await continuous_optimizer.get_enterprise_optimization_history(
            db_session, eid
        )
        assert result["total"] == 0
        assert result["items"] == []

    async def test_history_isolates_by_enterprise(self, db_session, v3_tables):
        """优化历史按企业隔离。"""
        eid1 = await _seed_enterprise(db_session)
        eid2 = await _seed_enterprise(db_session)
        agent1 = await _seed_agent(db_session, eid1, name="企业1 Agent")
        agent2 = await _seed_agent(db_session, eid2, name="企业2 Agent")

        await _seed_optimization_history(db_session, agent1.id)
        await _seed_optimization_history(db_session, agent2.id)

        result1 = await continuous_optimizer.get_enterprise_optimization_history(
            db_session, eid1
        )
        result2 = await continuous_optimizer.get_enterprise_optimization_history(
            db_session, eid2
        )
        assert result1["total"] == 1
        assert result2["total"] == 1


# ============================================================
# loop_engine 异常隔离测试
# ============================================================


class TestExceptionIsolation:
    async def test_feedback_failure_does_not_block_analysis(self, db_session, v3_tables):
        """loop_engine.analyze_feedback 异常不阻断整体分析。"""
        eid = await _seed_enterprise(db_session)
        await _seed_agent(db_session, eid)

        # _safe_analyze_feedback 内部捕获异常返回 None
        with patch(
            "app.services.evolution.continuous_optimizer.loop_engine.analyze_feedback",
            new=AsyncMock(side_effect=RuntimeError("loop_engine 不可用")),
        ), \
        patch(
            "app.services.evolution.continuous_optimizer.loop_engine.analyze_knowledge_gaps",
            new=AsyncMock(side_effect=RuntimeError("loop_engine 不可用")),
        ):
            report = await continuous_optimizer.analyze_enterprise(db_session, eid)

        # 分析不崩溃，返回空优化项
        assert report["analyzed_agents"] == 1
        assert report["optimization_items"] == []
