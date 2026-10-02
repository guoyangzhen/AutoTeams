"""D3: Agent API 与文件监听自动化测试。

覆盖 D3 新增功能：
- M15: process_monitor.cancel_stale_processing_tasks 定时取消超时僵尸任务
- D3→D4 依赖声明: rollback-knowledge 路由占位（D4 未合并时返回 501）
- D3→D4 依赖声明: incremental_updater before_update 快照钩子优雅降级
- M15: process_monitor 调度器启停幂等性 + LOOP_SCHEDULER_ENABLED 开关
- S9 后端: stream_chat SSE 流透传 retrieval_metadata 事件
- 6.7: watch 端点已注册验证

M7（tester_node 自动启 file_watcher）/ M8（_handle_change 自动增量更新）
已在 test_knowledge_auto_ops.py 覆盖，本文件不重复。
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.processing_task import ProcessingTask
from app.utils.rbac import require_admin
from app.utils.security import get_current_user


# ============================================================
# 辅助函数
# ============================================================

# 哨兵：区分「未传参（用 now 默认）」与「显式传 None（保留 None 测试 COALESCE 回退）」
_SENTINEL = object()


def _make_task(
    status: str = "processing",
    started_at: object = _SENTINEL,  # 哨兵 / datetime / None
    created_at: object = _SENTINEL,  # 哨兵 / datetime / None
    agent_id: str | None = None,
) -> ProcessingTask:
    """构造一个 ProcessingTask 记录（未持久化）。

    注意：started_at/created_at 使用哨兵区分「未传参」与「显式 None」。
    - 未传参 → 用 now 填充
    - 显式传 None → 保留 None（用于测试 COALESCE(started_at, created_at) 回退分支）
    """
    now = datetime.now(timezone.utc)
    return ProcessingTask(
        user_id="test-user-id",
        enterprise_id="test-enterprise-id",
        agent_id=agent_id,
        folder_path="/tmp/test_docs",
        agent_name="测试智能体",
        agent_description="测试用",
        status=status,
        progress=0.5,
        message="处理中",
        total_files=10,
        processed_files=5,
        failed_files=0,
        knowledge_count=0,
        processing_time_seconds=0.0,
        started_at=now if started_at is _SENTINEL else started_at,
        created_at=now if created_at is _SENTINEL else created_at,
        error_log=[],
    )


def _patch_session_factory(monkeypatch, db_session):
    """将 process_monitor.async_session_factory 替换为返回 db_session 的上下文管理器。"""
    @asynccontextmanager
    async def fake_factory():
        yield db_session

    monkeypatch.setattr(
        "app.services.process_monitor.async_session_factory",
        fake_factory,
    )


# ============================================================
# M15: cancel_stale_processing_tasks 测试
# ============================================================

class TestCancelStaleProcessingTasks:
    """验收标准：超过 1 小时未完成的 pending/scanning/processing/vectorizing 任务被取消。"""

    @pytest.mark.asyncio
    async def test_cancels_stale_processing_task(self, db_session, monkeypatch):
        """started_at 超过 1 小时的 processing 任务应被标记为 failed。"""
        stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
        task = _make_task(status="processing", started_at=stale_time, created_at=stale_time)
        db_session.add(task)
        await db_session.commit()

        _patch_session_factory(monkeypatch, db_session)

        from app.services.process_monitor import cancel_stale_processing_tasks
        result = await cancel_stale_processing_tasks()

        assert result["scanned"] == 1
        assert result["cancelled"] == 1

        await db_session.refresh(task)
        assert task.status == "failed"
        assert "超时" in (task.message or "")
        assert task.error_log is not None
        assert len(task.error_log) == 1
        assert task.error_log[0]["error_type"] == "StaleTaskTimeout"
        assert task.error_log[0]["previous_status"] == "processing"

    @pytest.mark.asyncio
    async def test_cancels_stale_pending_task_without_started_at(self, db_session, monkeypatch):
        """started_at 为空但 created_at 超过 1 小时的 pending 任务应被取消（COALESCE 回退）。"""
        stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
        task = _make_task(status="pending", started_at=None, created_at=stale_time)
        db_session.add(task)
        await db_session.commit()

        _patch_session_factory(monkeypatch, db_session)

        from app.services.process_monitor import cancel_stale_processing_tasks
        result = await cancel_stale_processing_tasks()

        assert result["cancelled"] == 1
        await db_session.refresh(task)
        assert task.status == "failed"

    @pytest.mark.asyncio
    async def test_skips_fresh_in_progress_task(self, db_session, monkeypatch):
        """刚创建的 processing 任务（未超时）不应被取消。"""
        fresh_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        task = _make_task(status="processing", started_at=fresh_time, created_at=fresh_time)
        db_session.add(task)
        await db_session.commit()

        _patch_session_factory(monkeypatch, db_session)

        from app.services.process_monitor import cancel_stale_processing_tasks
        result = await cancel_stale_processing_tasks()

        assert result["scanned"] == 0
        assert result["cancelled"] == 0
        await db_session.refresh(task)
        assert task.status == "processing"

    @pytest.mark.asyncio
    async def test_skips_terminal_status_tasks(self, db_session, monkeypatch):
        """已完成/失败/取消的任务即使超过 1 小时也不应被触碰。"""
        stale_time = datetime.now(timezone.utc) - timedelta(hours=3)
        for terminal_status in ("completed", "failed", "cancelled"):
            task = _make_task(
                status=terminal_status, started_at=stale_time, created_at=stale_time,
            )
            db_session.add(task)
        await db_session.commit()

        _patch_session_factory(monkeypatch, db_session)

        from app.services.process_monitor import cancel_stale_processing_tasks
        result = await cancel_stale_processing_tasks()

        assert result["scanned"] == 0
        assert result["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_cancels_all_incomplete_statuses(self, db_session, monkeypatch):
        """pending/scanning/processing/vectorizing 四种中间态超时任务均应被取消。"""
        stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
        for incomplete_status in ("pending", "scanning", "processing", "vectorizing"):
            task = _make_task(
                status=incomplete_status, started_at=stale_time, created_at=stale_time,
            )
            db_session.add(task)
        await db_session.commit()

        _patch_session_factory(monkeypatch, db_session)

        from app.services.process_monitor import cancel_stale_processing_tasks
        result = await cancel_stale_processing_tasks()

        assert result["scanned"] == 4
        assert result["cancelled"] == 4

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_tasks(self, db_session, monkeypatch):
        """无任务时应返回 scanned=0, cancelled=0。"""
        _patch_session_factory(monkeypatch, db_session)

        from app.services.process_monitor import cancel_stale_processing_tasks
        result = await cancel_stale_processing_tasks()

        assert result["scanned"] == 0
        assert result["cancelled"] == 0


# ============================================================
# M15: 调度器启停幂等性测试
# ============================================================

class TestProcessMonitorScheduler:
    """验收标准：调度器启停幂等，受 LOOP_SCHEDULER_ENABLED 开关控制。"""

    def test_start_skipped_when_disabled(self, monkeypatch):
        """LOOP_SCHEDULER_ENABLED=false 时跳过启动。"""
        from app.services import process_monitor as pm

        monkeypatch.setattr(pm.settings, "LOOP_SCHEDULER_ENABLED", False)
        if pm._scheduler.running:
            pm._scheduler.shutdown(wait=False)

        pm.start_process_monitor()
        assert not pm._scheduler.running

    def test_start_idempotent_when_already_running(self, monkeypatch):
        """调度器已运行时重复启动应安全跳过（不抛异常）。"""
        from app.services import process_monitor as pm

        monkeypatch.setattr(pm.settings, "LOOP_SCHEDULER_ENABLED", True)

        # 手动启动调度器（通过 start_process_monitor，先确保能启动）
        # 注意：AsyncIOScheduler.start() 需要事件循环；pytest 同步测试无循环，
        # 故此处仅验证幂等守卫：若已运行则直接返回，不重复 add_job。
        # 用 monkeypatch 模拟 running=True
        class FakeScheduler:
            running = True
            def shutdown(self, wait=False):
                pass
        fake = FakeScheduler()
        monkeypatch.setattr(pm, "_scheduler", fake)

        # 已运行时调用应安全跳过
        pm.start_process_monitor()
        assert fake.running is True

    def test_stop_safe_when_not_running(self, monkeypatch):
        """调度器未运行时 stop 应安全返回。"""
        from app.services import process_monitor as pm

        class FakeScheduler:
            running = False
            def shutdown(self, wait=False):
                raise RuntimeError("不应调用 shutdown")
        fake = FakeScheduler()
        monkeypatch.setattr(pm, "_scheduler", fake)

        # 未运行时停止不应抛异常，也不应调用 shutdown
        pm.stop_process_monitor()


# ============================================================
# D3→D4 依赖: rollback-knowledge 路由占位测试
# ============================================================

class TestRollbackKnowledgeRoute:
    """验收标准：D4 未合并时返回 501，路由占位就绪；D4 合并后调用其函数。"""

    @pytest.mark.asyncio
    async def test_rollback_knowledge_returns_501_when_d4_not_implemented(
        self, authenticated_client, monkeypatch
    ):
        """D4 的 rollback_knowledge 函数不存在时，端点应返回 501。

        D4 合并后 rollback_knowledge 已实现，本测试通过 monkeypatch.delattr
        模拟"D4 未实现"场景，验证 501 降级路径仍然可用（防御性设计）。
        """
        from app.main import app
        import app.services.agent_version_service as avs_module

        # 构造 fake agent，绕过 DB 查询
        class FakeAgent:
            id = "agent-test-501"
            status = "ready"
            enterprise_id = None  # 超级管理员放行

        async def fake_get_agent(db, agent_id, current_user=None):
            return FakeAgent()

        monkeypatch.setattr("app.api.agents.knowledge._get_agent_or_404", fake_get_agent)
        # 让 require_admin 放行（复用 get_current_user 的已登录用户）
        app.dependency_overrides[require_admin] = get_current_user

        # D4 合并后 rollback_knowledge 已存在；删除属性模拟"D4 未实现"，
        # 使路由的 `from ... import rollback_knowledge` 抛 ImportError → 501 降级
        if hasattr(avs_module, "rollback_knowledge"):
            monkeypatch.delattr(avs_module, "rollback_knowledge")

        try:
            resp = await authenticated_client.post(
                "/api/v1/agents/agent-test-501/rollback-knowledge/fake-version-id"
            )
            assert resp.status_code == 501
        finally:
            app.dependency_overrides.pop(require_admin, None)

    @pytest.mark.asyncio
    async def test_rollback_knowledge_calls_d4_function_when_available(
        self, authenticated_client, monkeypatch
    ):
        """D4 提供 rollback_knowledge 后，端点应调用它并返回 200。"""
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.main import app

        class FakeAgent:
            id = "agent-test-d4"
            status = "ready"
            # AgentResponse 要求 enterprise_id 为 str；此处 mock _get_agent_or_404
            # 已绕过企业隔离，enterprise_id 仅用于响应序列化
            enterprise_id = "test-enterprise-id"
            version = "1.0.0"
            name = "测试"
            description = ""
            system_prompt = ""
            config = {}
            metrics = {}
            file_count = 0
            knowledge_count = 0
            folder_path = None
            created_at = datetime.now(timezone.utc)
            def __init__(self):
                pass

        fake_agent = FakeAgent()

        async def fake_get_agent(db, agent_id, current_user=None):
            return fake_agent

        monkeypatch.setattr("app.api.agents.knowledge._get_agent_or_404", fake_get_agent)
        app.dependency_overrides[require_admin] = get_current_user

        # FakeAgent 非映射类：patch AsyncSession.refresh 对其跳过，
        # 避免 UnmappedInstanceError；其余真实实例走原逻辑
        original_refresh = AsyncSession.refresh

        async def patched_refresh(self, instance, *args, **kwargs):
            if isinstance(instance, FakeAgent):
                return None
            return await original_refresh(self, instance, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "refresh", patched_refresh)

        # log_audit 会向 session 写入审计记录，FakeAgent 场景下跳过避免副作用
        async def fake_log_audit(*args, **kwargs):
            return None

        monkeypatch.setattr("app.api.agents.knowledge.log_audit", fake_log_audit)

        # 注入 D4 的 rollback_knowledge 函数（模块原本无此属性）
        call_log = []

        async def fake_rollback_knowledge(db, agent_obj, version_id, user_id):
            call_log.append({
                "agent_id": agent_obj.id,
                "version_id": version_id,
            })

        import app.services.agent_version_service as avs_module
        monkeypatch.setattr(
            avs_module, "rollback_knowledge", fake_rollback_knowledge, raising=False
        )

        try:
            resp = await authenticated_client.post(
                "/api/v1/agents/agent-test-d4/rollback-knowledge/version-xyz"
            )
            assert resp.status_code == 200
            assert len(call_log) == 1
            assert call_log[0]["version_id"] == "version-xyz"
        finally:
            app.dependency_overrides.pop(require_admin, None)


# ============================================================
# D3→D4 依赖: incremental_updater before_update 钩子测试
# ============================================================

class TestKnowledgeSnapshotHook:
    """验收标准：D4 未合并时优雅跳过；D4 合并后调用快照函数；失败不阻塞。"""

    @pytest.mark.asyncio
    async def test_hook_skips_when_d4_not_available(self, db_session):
        """create_knowledge_snapshot 不存在时，钩子应优雅跳过，不抛异常。"""
        from app.services.incremental_updater import _create_knowledge_snapshot_hook

        # agent_version_service 没有 create_knowledge_snapshot（D4 未合并）
        await _create_knowledge_snapshot_hook(db_session, "test-agent-id")
        # 无异常抛出即通过

    @pytest.mark.asyncio
    async def test_hook_calls_d4_function_when_available(self, db_session, monkeypatch):
        """D4 提供 create_knowledge_snapshot 后，钩子应调用它。"""
        call_log = []

        async def fake_create_knowledge_snapshot(db, agent_id):
            call_log.append({"agent_id": agent_id})

        import app.services.agent_version_service as avs_module
        monkeypatch.setattr(
            avs_module,
            "create_knowledge_snapshot",
            fake_create_knowledge_snapshot,
            raising=False,
        )

        from app.services.incremental_updater import _create_knowledge_snapshot_hook
        await _create_knowledge_snapshot_hook(db_session, "agent-xyz")

        assert len(call_log) == 1
        assert call_log[0]["agent_id"] == "agent-xyz"

    @pytest.mark.asyncio
    async def test_hook_does_not_block_on_snapshot_failure(self, db_session, monkeypatch):
        """快照函数抛异常时，钩子应记录 warning 但不向上抛出。"""
        async def failing_snapshot(db, agent_id):
            raise RuntimeError("D4 快照失败模拟")

        import app.services.agent_version_service as avs_module
        monkeypatch.setattr(
            avs_module, "create_knowledge_snapshot", failing_snapshot, raising=False
        )

        from app.services.incremental_updater import _create_knowledge_snapshot_hook
        await _create_knowledge_snapshot_hook(db_session, "agent-fail")
        # 到这里即通过（未抛异常）


# ============================================================
# S9 后端: stream_chat retrieval_metadata 事件测试
# ============================================================

class TestStreamChatRetrievalMetadata:
    """验收标准：stream_chat SSE 流中包含 retrieval_metadata 事件。"""

    @pytest.mark.asyncio
    async def test_retrieval_metadata_event_emitted(self, authenticated_client, monkeypatch):
        """SSE 流应在内容开始前发出 retrieval_metadata 事件。"""
        from app.main import app

        class FakeAgent:
            id = "agent-sse-test"
            status = "ready"
            enterprise_id = None
            system_prompt = "你是助手"
            version = "1.0.0"
            # 知识库页保存的 Top-K 配置，检索链路应真实读取（此前硬编码 5）
            config = {"knowledge": {"topK": 8}}

        async def fake_get_agent(db, agent_id, current_user=None):
            return FakeAgent()

        monkeypatch.setattr("app.api.agents.chat._get_agent_or_404", fake_get_agent)

        # mock _search_knowledge 返回带 self_rag_score 的 rag_meta（D2 提供）
        # n_results 由 _resolve_top_k(agent) 传入（知识库页 topK 配置真实生效）
        captured: dict = {}

        async def fake_search_knowledge(*args, **kwargs):
            n_results = kwargs.get("n_results", args[3] if len(args) > 3 else (args[2] if len(args) > 2 else 5))
            captured["n_results"] = n_results
            docs = [{"content": "答案片段", "metadata": {"source": "doc.pdf", "file_type": "document"}}]
            meta = {
                "query_type": "agentic",
                "iterations": 2,
                "refined_queries": ["改写查询1"],
                "self_rag_score": 0.85,
            }
            return docs, meta

        monkeypatch.setattr("app.api.agents.chat._search_knowledge", fake_search_knowledge)

        # mock LLM stream 返回单个 chunk 后结束
        async def fake_chat_stream(messages, tier=None):
            yield "你好"

        from app.services import llm_service as llm_mod
        monkeypatch.setattr(
            llm_mod.llm_service, "chat_stream", lambda messages, tier=None: fake_chat_stream(messages, tier)
        )

        # mock 持久化与评估
        async def fake_save(*args, **kwargs):
            return "fake-msg-id"
        monkeypatch.setattr("app.api.agents.chat._save_assistant_message_with_retry", fake_save)
        monkeypatch.setattr("app.api.agents.chat.rag_evaluator.evaluate_message", lambda *a, **kw: None)

        resp = await authenticated_client.post(
            "/api/v1/agents/agent-sse-test/chat/stream",
            json={"content": "测试问题"},
        )
        assert resp.status_code == 200

        body = resp.text
        assert "retrieval_metadata" in body
        assert "retrieval_rounds" in body
        assert "self_rag_score" in body
        assert "0.85" in body
        # Agent 配置的 topK 应真实传入检索层，而非被硬编码的 5 覆盖
        assert captured["n_results"] == 8


# ============================================================
# 6.7: watch 端点存在性验证
# ============================================================

class TestWatchEndpointsExist:
    """验收标准：POST/DELETE /agents/{id}/watch 端点已注册（非 404）。"""

    @pytest.mark.asyncio
    async def test_watch_endpoints_registered(self, authenticated_client, monkeypatch):
        """watch 端点应已注册（非 404），通过 mock agent + file_watcher 验证。"""
        from app.main import app

        class FakeAgent:
            id = "agent-watch-test"
            status = "ready"
            enterprise_id = None
            folder_path = None

        async def fake_get_agent_for_watching(db, agent_id, current_user):
            return FakeAgent()

        monkeypatch.setattr(
            "app.api.agents.knowledge._get_agent_or_404_for_watching", fake_get_agent_for_watching
        )

        # mock 文件监控服务可用，否则测试环境会返回 503
        from app.services import file_watcher
        monkeypatch.setattr(file_watcher.file_watcher_service, "is_available", lambda: True)

        # POST /watch: agent 无 folder_path → 应返回 400（端点存在）
        resp = await authenticated_client.post("/api/v1/agents/agent-watch-test/watch")
        assert resp.status_code != 404
        assert resp.status_code == 400  # AGENT_FOLDER_MISSING

        # DELETE /watch: 停止不存在的监听也安全 → 200
        # mock file_watcher_service.stop_watching
        async def fake_stop(agent_id):
            return None
        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.stop_watching", fake_stop
        )

        resp = await authenticated_client.delete("/api/v1/agents/agent-watch-test/watch")
        assert resp.status_code == 200
