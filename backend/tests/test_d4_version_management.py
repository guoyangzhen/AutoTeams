"""D4: 版本管理与自动回滚测试。

覆盖 D4 新增功能：
- 6.5: create_knowledge_snapshot 知识库快照创建
- 6.5: rollback_knowledge 知识库回滚
- M14: auto_rollback_if_degraded 质量退化自动回滚
- D3 对接验证: incremental_updater 钩子调用真实 create_knowledge_snapshot
- D3 对接验证: agents.py rollback-knowledge 路由返回 200

测试使用内存 SQLite（conftest 的 db_session fixture），mock LLM/向量库等外部依赖。
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.conversation import Conversation
from app.models.enterprise import Enterprise
from app.models.file import File
from app.models.message import Message
from app.models.optimization_history import OptimizationHistory
from app.models.rag_evaluation import RAGEvaluation
from app.models.user import User
from app.services.agent_version_service import (
    create_knowledge_snapshot,
    rollback_knowledge,
)
from app.services.loop_engine import loop_engine
from app.utils.rbac import require_admin
from app.utils.security import get_current_user


# ============================================================
# 辅助函数
# ============================================================

async def _create_agent_with_files(db_session, file_count: int = 2):
    """创建 Agent + 关联文件，返回 (agent, user, files)。"""
    enterprise = Enterprise(name="测试企业")
    db_session.add(enterprise)
    await db_session.flush()
    await db_session.refresh(enterprise)

    user = User(
        email="d4@test.com",
        password_hash="hash",
        name="D4测试",
        enterprise_id=enterprise.id,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)

    agent = Agent(
        enterprise_id=enterprise.id,
        name="D4测试助手",
        description="测试用",
        version="1.0.0",
        config={
            "chunk_size": 800,
            "chunk_overlap": 150,
            "top_k": 5,
            "similarity_threshold": 0.75,
        },
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.refresh(agent)

    files = []
    for i in range(file_count):
        f = File(
            agent_id=agent.id,
            original_name=f"doc_{i}.pdf",
            file_path=f"/tmp/docs/doc_{i}.pdf",
            file_size=1024 * (i + 1),
            file_type="document",
            status="completed",
            content_hash=f"hash_{i}_{uuid.uuid4().hex[:8]}",
            chunk_count=10 * (i + 1),
            vector_count=10 * (i + 1),
        )
        db_session.add(f)
        files.append(f)
    await db_session.commit()
    await db_session.refresh(agent)
    return agent, user, files


def _patch_session_factory(monkeypatch, db_session):
    """将 app.database.async_session_factory 替换为返回 db_session 的上下文管理器。

    auto_rollback_if_degraded 在回滚写操作时使用独立 session（项目硬约束），
    测试时将其指向同一个测试 session 以共享数据。
    """
    @asynccontextmanager
    async def fake_factory():
        yield db_session

    # loop_engine.auto_rollback_if_degraded 内部使用 from app.database import async_session_factory
    monkeypatch.setattr("app.database.async_session_factory", fake_factory)


async def _create_rag_evaluations(db_session, agent, conversation, message, daily_scores):
    """按日创建 RAG 评估记录。

    Args:
        daily_scores: list of (days_ago, score) 元组，score 为综合分（0-1）
                     会均匀分配到四项指标
    """
    for days_ago, score in daily_scores:
        evaluated_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
        # 将综合分均匀分配到四项指标
        eval_record = RAGEvaluation(
            agent_id=agent.id,
            conversation_id=conversation.id,
            message_id=message.id,
            faithfulness=score,
            answer_relevancy=score,
            context_precision=score,
            context_recall=score,
            evaluated_at=evaluated_at,
        )
        db_session.add(eval_record)
    await db_session.commit()


# ============================================================
# 6.5: create_knowledge_snapshot 测试
# ============================================================

class TestCreateKnowledgeSnapshot:
    """验收标准：创建快照后 agent_versions 表有记录，含 file_list/vector_collection/chunk_strategy/rag_config。"""

    @pytest.mark.asyncio
    async def test_creates_snapshot_with_full_knowledge_state(self, db_session):
        """快照应包含完整的知识库状态：文件列表、向量集合、分块策略、RAG 配置。"""
        agent, _, files = await _create_agent_with_files(db_session, file_count=3)

        snapshot = await create_knowledge_snapshot(db_session, agent.id)

        assert snapshot is not None
        assert snapshot.agent_id == agent.id
        assert snapshot.is_active is False  # 知识库快照不参与配置版本激活态
        assert snapshot.knowledge_snapshot is not None

        ks = snapshot.knowledge_snapshot
        # 文件列表
        assert len(ks["file_list"]) == 3
        assert ks["file_list"][0]["path"] == "/tmp/docs/doc_0.pdf"
        assert ks["file_list"][0]["hash"].startswith("hash_0_")
        assert ks["file_list"][0]["size"] == 1024
        assert ks["file_list"][0]["file_type"] == "document"
        # 向量集合名
        assert ks["vector_collection"] == f"agent_{agent.id}"
        # 分块策略
        assert ks["chunk_strategy"]["chunk_size"] == 800
        assert ks["chunk_strategy"]["chunk_overlap"] == 150
        # RAG 配置
        assert ks["rag_config"]["top_k"] == 5
        assert ks["rag_config"]["similarity_threshold"] == 0.75

    @pytest.mark.asyncio
    async def test_snapshot_persisted_to_db(self, db_session):
        """快照应持久化到 agent_versions 表。"""
        agent, _, _ = await _create_agent_with_files(db_session, file_count=1)

        await create_knowledge_snapshot(db_session, agent.id)

        stmt = select(AgentVersion).where(AgentVersion.agent_id == agent.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].knowledge_snapshot is not None
        assert "file_list" in rows[0].knowledge_snapshot

    @pytest.mark.asyncio
    async def test_returns_none_when_agent_not_found(self, db_session):
        """Agent 不存在时应返回 None（不抛异常）。"""
        result = await create_knowledge_snapshot(db_session, "non-existent-agent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_snapshot_with_default_config_when_missing(self, db_session):
        """Agent.config 为空时应使用合理默认值填充快照。"""
        enterprise = Enterprise(name="默认值测试企业")
        db_session.add(enterprise)
        await db_session.flush()
        agent = Agent(enterprise_id=enterprise.id, name="默认值测试", config=None)
        db_session.add(agent)
        await db_session.commit()
        await db_session.refresh(agent)

        snapshot = await create_knowledge_snapshot(db_session, agent.id)

        ks = snapshot.knowledge_snapshot
        assert ks["chunk_strategy"]["chunk_size"] == 1000
        assert ks["chunk_strategy"]["chunk_overlap"] == 200
        assert ks["rag_config"]["top_k"] == 5
        assert ks["rag_config"]["similarity_threshold"] == 0.7
        assert ks["file_list"] == []  # 无文件


# ============================================================
# 6.5: rollback_knowledge 测试
# ============================================================

class TestRollbackKnowledge:
    """验收标准：版本存在时恢复 agent.config 并创建回滚记录；不存在时抛 ValueError。"""

    @pytest.mark.asyncio
    async def test_rollback_restores_rag_and_chunk_config(self, db_session):
        """回滚应从快照恢复 chunk_strategy 和 rag_config 到 agent.config。"""
        agent, user, _ = await _create_agent_with_files(db_session, file_count=2)

        # 创建快照（捕获初始配置：chunk_size=800, top_k=5）
        snapshot = await create_knowledge_snapshot(db_session, agent.id)

        # 修改 agent.config（模拟后续配置变更）
        agent.config = {
            "chunk_size": 2000,
            "chunk_overlap": 400,
            "top_k": 20,
            "similarity_threshold": 0.5,
        }
        await db_session.commit()
        await db_session.refresh(agent)

        # 回滚到快照
        result = await rollback_knowledge(db_session, agent, snapshot.id, user.id)

        assert result is agent
        # 验证 config 已恢复为快照中的值
        assert agent.config["chunk_size"] == 800
        assert agent.config["chunk_overlap"] == 150
        assert agent.config["top_k"] == 5
        assert agent.config["similarity_threshold"] == 0.75

    @pytest.mark.asyncio
    async def test_rollback_creates_audit_record(self, db_session):
        """回滚应创建一条新的 AgentVersion 记录标记回滚动作。"""
        agent, user, _ = await _create_agent_with_files(db_session)
        snapshot = await create_knowledge_snapshot(db_session, agent.id)

        await rollback_knowledge(db_session, agent, snapshot.id, user.id)

        stmt = select(AgentVersion).where(AgentVersion.agent_id == agent.id)
        rows = (await db_session.execute(stmt)).scalars().all()
        # 至少 2 条：原始快照 + 回滚记录
        assert len(rows) >= 2
        # 最后一条是回滚记录
        rollback_record = rows[-1]
        assert "回滚知识库" in (rollback_record.changelog or "")
        assert rollback_record.knowledge_snapshot is not None
        assert rollback_record.user_id == user.id

    @pytest.mark.asyncio
    async def test_raises_value_error_when_version_not_found(self, db_session):
        """版本不存在时应抛出 ValueError("version_not_found")。"""
        agent, _, _ = await _create_agent_with_files(db_session)

        with pytest.raises(ValueError, match="version_not_found"):
            await rollback_knowledge(db_session, agent, "non-existent-version-id", "user-1")

    @pytest.mark.asyncio
    async def test_raises_when_knowledge_snapshot_missing(self, db_session):
        """目标版本无 knowledge_snapshot 时应抛出 ValueError。"""
        agent, user, _ = await _create_agent_with_files(db_session)

        # 创建一条只有 config_snapshot、无 knowledge_snapshot 的版本记录
        version_without_kb = AgentVersion(
            agent_id=agent.id,
            version="1.0.0",
            config_snapshot={"name": agent.name},
            knowledge_snapshot=None,
            changelog="仅配置快照",
            is_active=False,
        )
        db_session.add(version_without_kb)
        await db_session.commit()
        await db_session.refresh(version_without_kb)

        with pytest.raises(ValueError, match="knowledge_snapshot_missing"):
            await rollback_knowledge(db_session, agent, version_without_kb.id, user.id)


# ============================================================
# M14: auto_rollback_if_degraded 测试
# ============================================================

class TestAutoRollbackIfDegraded:
    """验收标准：连续 3 天评分 < 0.6 触发回滚；评分正常时不触发。"""

    @pytest.mark.asyncio
    async def test_triggers_rollback_when_scores_below_threshold(self, db_session, monkeypatch):
        """连续 3 天评分均 < 0.6 时应触发自动回滚 + 写 OptimizationHistory。"""
        agent, user, _ = await _create_agent_with_files(db_session)
        # 创建知识库快照（作为回滚目标）
        snapshot = await create_knowledge_snapshot(db_session, agent.id)

        # 创建会话与消息（RAGEvaluation 需要 FK）
        conv = Conversation(user_id=user.id, agent_id=agent.id, title="测试会话")
        db_session.add(conv)
        await db_session.flush()
        msg = Message(conversation_id=conv.id, role="assistant", content="回答")
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(conv)
        await db_session.refresh(msg)

        # 创建 3 天的低分评估记录（均 0.3 < 0.6）
        await _create_rag_evaluations(
            db_session, agent, conv, msg,
            [(0, 0.3), (1, 0.3), (2, 0.3)],
        )

        # patch async_session_factory 让回滚使用测试 session
        _patch_session_factory(monkeypatch, db_session)

        result = await loop_engine.auto_rollback_if_degraded(db_session, agent.id)

        assert result["triggered"] is True
        assert result["rollback_version_id"] == snapshot.id
        assert "0.6" in result["reason"]

        # 验证 OptimizationHistory 已写入
        stmt = select(OptimizationHistory).where(
            OptimizationHistory.agent_id == agent.id,
            OptimizationHistory.type == "auto_rollback",
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].applied is True

    @pytest.mark.asyncio
    async def test_no_rollback_when_scores_normal(self, db_session, monkeypatch):
        """评分正常（>= 0.6）时不应触发回滚。"""
        agent, user, _ = await _create_agent_with_files(db_session)
        snapshot = await create_knowledge_snapshot(db_session, agent.id)

        conv = Conversation(user_id=user.id, agent_id=agent.id, title="测试会话")
        db_session.add(conv)
        await db_session.flush()
        msg = Message(conversation_id=conv.id, role="assistant", content="回答")
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(conv)
        await db_session.refresh(msg)

        # 创建 3 天的高分评估记录（均 0.9 > 0.6）
        await _create_rag_evaluations(
            db_session, agent, conv, msg,
            [(0, 0.9), (1, 0.85), (2, 0.88)],
        )

        _patch_session_factory(monkeypatch, db_session)

        result = await loop_engine.auto_rollback_if_degraded(db_session, agent.id)

        assert result["triggered"] is False
        assert result["rollback_version_id"] is None

        # 不应写入 auto_rollback 类型的 OptimizationHistory
        stmt = select(OptimizationHistory).where(
            OptimizationHistory.agent_id == agent.id,
            OptimizationHistory.type == "auto_rollback",
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_no_rollback_when_data_insufficient(self, db_session, monkeypatch):
        """评估数据不足 3 天时不应触发回滚。"""
        agent, user, _ = await _create_agent_with_files(db_session)

        conv = Conversation(user_id=user.id, agent_id=agent.id, title="测试会话")
        db_session.add(conv)
        await db_session.flush()
        msg = Message(conversation_id=conv.id, role="assistant", content="回答")
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(conv)
        await db_session.refresh(msg)

        # 仅 1 天数据
        await _create_rag_evaluations(
            db_session, agent, conv, msg,
            [(0, 0.3)],
        )

        _patch_session_factory(monkeypatch, db_session)

        result = await loop_engine.auto_rollback_if_degraded(db_session, agent.id)

        assert result["triggered"] is False
        assert "数据不足" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_rollback_when_mixed_scores(self, db_session, monkeypatch):
        """评分有高有低（非持续低）时不应触发回滚。"""
        agent, user, _ = await _create_agent_with_files(db_session)
        await create_knowledge_snapshot(db_session, agent.id)

        conv = Conversation(user_id=user.id, agent_id=agent.id, title="测试会话")
        db_session.add(conv)
        await db_session.flush()
        msg = Message(conversation_id=conv.id, role="assistant", content="回答")
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(conv)
        await db_session.refresh(msg)

        # 2 天低分 + 1 天高分
        await _create_rag_evaluations(
            db_session, agent, conv, msg,
            [(0, 0.3), (1, 0.3), (2, 0.9)],
        )

        _patch_session_factory(monkeypatch, db_session)

        result = await loop_engine.auto_rollback_if_degraded(db_session, agent.id)

        assert result["triggered"] is False
        assert "未持续低于阈值" in result["reason"]

    @pytest.mark.asyncio
    async def test_records_history_even_without_snapshot(self, db_session, monkeypatch):
        """检测到退化但无快照时，仍应记录 OptimizationHistory（applied=False）。"""
        agent, user, _ = await _create_agent_with_files(db_session)
        # 故意不创建知识库快照

        conv = Conversation(user_id=user.id, agent_id=agent.id, title="测试会话")
        db_session.add(conv)
        await db_session.flush()
        msg = Message(conversation_id=conv.id, role="assistant", content="回答")
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(conv)
        await db_session.refresh(msg)

        await _create_rag_evaluations(
            db_session, agent, conv, msg,
            [(0, 0.3), (1, 0.3), (2, 0.3)],
        )

        _patch_session_factory(monkeypatch, db_session)

        result = await loop_engine.auto_rollback_if_degraded(db_session, agent.id)

        assert result["triggered"] is False
        assert "无可回滚" in result["reason"]

        # 应写入一条 applied=False 的 auto_rollback 记录
        stmt = select(OptimizationHistory).where(
            OptimizationHistory.agent_id == agent.id,
            OptimizationHistory.type == "auto_rollback",
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].applied is False


# ============================================================
# D3 对接验证: incremental_updater 钩子调用真实 create_knowledge_snapshot
# ============================================================

class TestD3HookIntegration:
    """验收标准：incremental_updater 的 _create_knowledge_snapshot_hook 能调用真实 create_knowledge_snapshot。"""

    @pytest.mark.asyncio
    async def test_hook_calls_real_create_knowledge_snapshot(self, db_session):
        """D3 钩子现在应成功调用 D4 的真实 create_knowledge_snapshot（不再 ImportError 降级）。"""
        from app.services.incremental_updater import _create_knowledge_snapshot_hook

        agent, _, _ = await _create_agent_with_files(db_session, file_count=1)

        # 调用钩子（D3 路径）
        await _create_knowledge_snapshot_hook(db_session, agent.id)

        # 验证快照已创建
        stmt = select(AgentVersion).where(
            AgentVersion.agent_id == agent.id,
            AgentVersion.knowledge_snapshot.isnot(None),
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) == 1
        assert rows[0].knowledge_snapshot["vector_collection"] == f"agent_{agent.id}"

    @pytest.mark.asyncio
    async def test_hook_does_not_block_on_failure(self, db_session, monkeypatch):
        """快照函数抛异常时，钩子应记录 warning 但不向上抛出（不阻塞增量更新）。"""
        from app.services.incremental_updater import _create_knowledge_snapshot_hook

        # 让 create_knowledge_snapshot 抛异常
        async def failing_snapshot(db, agent_id):
            raise RuntimeError("模拟快照失败")

        monkeypatch.setattr(
            "app.services.agent_version_service.create_knowledge_snapshot",
            failing_snapshot,
        )

        # 不应抛异常
        await _create_knowledge_snapshot_hook(db_session, "agent-fail-test")


# ============================================================
# D3 对接验证: agents.py rollback-knowledge 路由返回 200
# ============================================================

class TestD3RouteIntegration:
    """验收标准：rollback-knowledge 路由现在返回 200（不再 501）。"""

    @pytest.mark.asyncio
    async def test_rollback_knowledge_route_returns_200(
        self, authenticated_client, monkeypatch, db_session
    ):
        """D4 的 rollback_knowledge 已实现，端点应返回 200 而非 501。"""
        from sqlalchemy.ext.asyncio import AsyncSession
        from app.main import app

        # 创建真实 Agent + 快照（用于路由调用真实 rollback_knowledge）
        agent, user, _ = await _create_agent_with_files(db_session, file_count=1)
        snapshot = await create_knowledge_snapshot(db_session, agent.id)

        # 让路由用测试 session
        from app.database import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        # 让 _get_agent_or_404 返回真实 agent
        async def fake_get_agent(db, agent_id, current_user=None):
            # 重新从 db 查询以避免跨 session 实例问题
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            return result.scalar_one_or_none()

        monkeypatch.setattr("app.api.agents.knowledge._get_agent_or_404", fake_get_agent)
        # require_admin 复用 get_current_user（已登录用户）
        app.dependency_overrides[require_admin] = get_current_user

        # mock log_audit 避免副作用
        async def fake_log_audit(*args, **kwargs):
            return None
        monkeypatch.setattr("app.api.agents.knowledge.log_audit", fake_log_audit)

        try:
            resp = await authenticated_client.post(
                f"/api/v1/agents/{agent.id}/rollback-knowledge/{snapshot.id}"
            )
            assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data["success"] is True
            assert "data" in data
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(require_admin, None)

    @pytest.mark.asyncio
    async def test_rollback_knowledge_route_returns_404_for_missing_version(
        self, authenticated_client, monkeypatch, db_session
    ):
        """版本不存在时路由应返回 404。"""
        from app.main import app
        from app.database import get_db

        agent, _, _ = await _create_agent_with_files(db_session, file_count=1)

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db

        async def fake_get_agent(db, agent_id, current_user=None):
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            return result.scalar_one_or_none()

        monkeypatch.setattr("app.api.agents.knowledge._get_agent_or_404", fake_get_agent)
        app.dependency_overrides[require_admin] = get_current_user

        async def fake_log_audit(*args, **kwargs):
            return None
        monkeypatch.setattr("app.api.agents.knowledge.log_audit", fake_log_audit)

        try:
            resp = await authenticated_client.post(
                f"/api/v1/agents/{agent.id}/rollback-knowledge/non-existent-version"
            )
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(require_admin, None)
