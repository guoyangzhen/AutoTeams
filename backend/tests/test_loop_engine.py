"""P3-5: app/services/loop_engine.py 覆盖率补充测试。"""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock
from sqlalchemy import select

from app.models.user import User
from app.models.enterprise import Enterprise
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.optimization_history import OptimizationHistory
from app.services.loop_engine import loop_engine


async def _create_agent(db_session):
    enterprise = Enterprise(name="测试企业")
    db_session.add(enterprise)
    await db_session.flush()
    await db_session.refresh(enterprise)

    user = User(email="loop@test.com", password_hash="hash", name="测试", enterprise_id=enterprise.id)
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    agent = Agent(enterprise_id=enterprise.id, name="测试助手", description="")
    db_session.add(agent)
    await db_session.flush()
    await db_session.refresh(agent)

    return agent, user


async def _create_conversation_with_messages(db_session, agent, user, satisfaction=None, role="assistant"):
    conv = Conversation(user_id=user.id, agent_id=agent.id, title="测试")
    db_session.add(conv)
    await db_session.flush()
    await db_session.refresh(conv)

    msg = Message(
        conversation_id=conv.id,
        role=role,
        content="测试内容",
        satisfaction=satisfaction,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(msg)
    await db_session.commit()
    return conv


class TestAnalyzeFeedback:
    """analyze_feedback 测试。"""

    @pytest.mark.asyncio
    async def test_no_feedback_messages(self, db_session):
        agent, _ = await _create_agent(db_session)
        result = await loop_engine.analyze_feedback(db_session, agent.id, days=7)
        assert result["total_feedback"] == 0
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_analyzes_dissatisfied_messages(self, db_session):
        agent, user = await _create_agent(db_session)
        await _create_conversation_with_messages(db_session, agent, user, satisfaction="unsatisfied")

        llm_json = '{"reason": "检索不准", "expected_info": "正确信息", "improvement": "优化", "priority": "high"}'
        with patch("app.services.loop_engine.llm_service.chat", return_value=llm_json):
            result = await loop_engine.analyze_feedback(db_session, agent.id, days=7)

        assert result["total_feedback"] == 1
        assert len(result["issues"]) == 1
        assert result["issues"][0]["reason"] == "检索不准"

        stmt = select(OptimizationHistory).where(OptimizationHistory.agent_id == agent.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].type == "feedback"

    @pytest.mark.asyncio
    async def test_non_json_analysis_fallback(self, db_session):
        agent, user = await _create_agent(db_session)
        await _create_conversation_with_messages(db_session, agent, user, satisfaction="dissatisfied")

        with patch("app.services.loop_engine.llm_service.chat", return_value="plain analysis"):
            result = await loop_engine.analyze_feedback(db_session, agent.id, days=7)

        assert len(result["issues"]) == 1
        assert result["issues"][0].get("reason") == "plain analysis"


class TestAnalyzeKnowledgeGaps:
    """analyze_knowledge_gaps 测试。"""

    @pytest.mark.asyncio
    async def test_no_user_questions(self, db_session):
        agent, _ = await _create_agent(db_session)
        result = await loop_engine.analyze_knowledge_gaps(db_session, agent.id, days=30)
        assert result["total_questions"] == 0
        assert result["gaps"] == []

    @pytest.mark.asyncio
    async def test_analyzes_questions(self, db_session):
        agent, user = await _create_agent(db_session)
        await _create_conversation_with_messages(db_session, agent, user, role="user")

        llm_json = '{"gaps": ["缺知识"], "suggestions": ["补文档"], "priority_questions": ["问题1"]}'
        with patch("app.services.loop_engine.llm_service.chat", return_value=llm_json):
            result = await loop_engine.analyze_knowledge_gaps(db_session, agent.id, days=30)

        assert result["total_questions"] == 1
        assert result["gaps"] == ["缺知识"]
        assert result["top_keywords"]

        stmt = select(OptimizationHistory).where(OptimizationHistory.agent_id == agent.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].type == "gap"


class TestOptimizeRetrieval:
    """optimize_retrieval 测试。"""

    @pytest.mark.asyncio
    async def test_optimizes_with_mock_vector_store(self, db_session):
        agent, user = await _create_agent(db_session)

        mock_vs = MagicMock()
        mock_vs.search = AsyncMock(return_value=[{"content": "chunk1"}, {"content": "chunk2"}])
        with patch("app.services.loop_engine.VectorStoreService.create", new=AsyncMock(return_value=mock_vs)):
            llm_json = '{"issue": "结果不准", "keep_results": [0], "refined_query": "优化查询", "reason": "原因"}'
            with patch("app.services.loop_engine.llm_service.chat", return_value=llm_json):
                result = await loop_engine.optimize_retrieval(
                    db_session, agent.id, "查询", "反馈", user_id=user.id
                )

        assert result["refined_query"] == "优化查询"

        stmt = select(OptimizationHistory).where(OptimizationHistory.agent_id == agent.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].type == "optimization"

    @pytest.mark.asyncio
    async def test_optimize_retrieval_chroma_error(self, db_session):
        agent, user = await _create_agent(db_session)

        import chromadb.errors
        with patch("app.services.loop_engine.VectorStoreService.create", side_effect=chromadb.errors.ChromaError("boom")):
            result = await loop_engine.optimize_retrieval(
                db_session, agent.id, "查询", "反馈", user_id=user.id
            )
        assert "error" in result


class TestGetLoopStatus:
    """get_loop_status 测试。"""

    @pytest.mark.asyncio
    async def test_get_loop_status(self, db_session):
        agent, _ = await _create_agent(db_session)
        history = OptimizationHistory(
            agent_id=agent.id,
            type="feedback",
            input_data={},
            output_data={},
            applied=False,
        )
        db_session.add(history)
        await db_session.commit()

        result = await loop_engine.get_loop_status(agent.id, db_session)
        assert result["agent_id"] == agent.id
        assert result["total_optimizations"] == 1
        assert result["status"] == "active"
