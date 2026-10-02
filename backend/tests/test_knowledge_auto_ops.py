"""B5: 知识库自动运维闭环测试。

覆盖 O-11（tester 自动启 file_watcher）核心改造：
tester_node 测试通过后自动调 file_watcher_service.start_watching。

O-12（file_watcher 自动增量）与 O-13（KnowledgePage 配置保存）已在既有代码中完成：
- file_watcher._handle_change 已调 incremental_update（独立 session + 错误处理）
- KnowledgePage.handleSaveConfig 已实现并绑定按钮 + toast
- agents.py PUT /agents/{id} 已支持 config 合并
- incremental_updater.incremental_update 已实现 chunk id 防碰撞

本测试聚焦 B5 唯一新增点：tester_node 自动启动 watcher。
"""
import pytest

from app.services.agent_graph import AgentBuildState


# ============================================================
# tester_node 自动启动 file_watcher 测试
# ============================================================

class TestTesterAutoStartWatcher:
    """验收标准 1：tester 通过后自动 start_watching(agent_id, folder_path)。"""

    @pytest.mark.asyncio
    async def test_tester_starts_watcher_on_pass(self, monkeypatch):
        """测试通过 + folder_path 存在 + watchdog 可用 → 应调 start_watching。"""
        from app.services import agent_graph as ag_mod

        # 捕获 start_watching 调用参数
        start_calls = []

        async def fake_start_watching(agent_id, folder_path):
            start_calls.append((agent_id, folder_path))
            return True

        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.start_watching",
            fake_start_watching,
        )
        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.is_available",
            lambda: True,
        )

        # 构造 tester_node：需要通过 db_session_factory 查 Agent 状态为 ready
        # 使用 mock factory + mock agent
        from app.services.agent_graph import make_tester_node

        class FakeAgent:
            def __init__(self):
                self.status = "ready"

        class FakeResult:
            def scalar_one_or_none(self):
                return FakeAgent()

        class FakeDb:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def execute(self, *args, **kwargs):
                return FakeResult()

        def fake_factory():
            return FakeDb()

        # mock VectorStoreService.create 返回有 count 的 vector_store
        class FakeVectorStore:
            async def count(self):
                return 10

        async def fake_create(name):
            return FakeVectorStore()

        monkeypatch.setattr(ag_mod.VectorStoreService, "create", fake_create)

        tester_node = make_tester_node(fake_factory)
        state: AgentBuildState = {
            "agent_id": "agent-test-1",
            "folder_path": "/tmp/test_docs",
            "skills_created": 2,
            "vector_count": 10,
        }

        result = await tester_node(state)

        # 验证 start_watching 被调用
        assert len(start_calls) == 1
        assert start_calls[0] == ("agent-test-1", "/tmp/test_docs")
        # 验证构建仍标记完成
        assert result["status"] == "completed"
        assert result["test_result"]["vector_store_ok"] is True
        assert result["test_result"]["agent_status_ok"] is True

    @pytest.mark.asyncio
    async def test_tester_skips_watcher_on_fail(self, monkeypatch):
        """测试失败（向量库为空）→ 不应调 start_watching。"""
        from app.services import agent_graph as ag_mod
        from app.services.agent_graph import make_tester_node

        start_calls = []

        async def fake_start_watching(agent_id, folder_path):
            start_calls.append((agent_id, folder_path))
            return True

        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.start_watching",
            fake_start_watching,
        )
        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.is_available",
            lambda: True,
        )

        class FakeAgent:
            def __init__(self):
                self.status = "ready"

        class FakeResult:
            def scalar_one_or_none(self):
                return FakeAgent()

        class FakeDb:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def execute(self, *args, **kwargs):
                return FakeResult()

        def fake_factory():
            return FakeDb()

        # 向量库 count=0 → vector_store_ok=False → test_passed=False
        class FakeVectorStore:
            async def count(self):
                return 0

        async def fake_create(name):
            return FakeVectorStore()

        monkeypatch.setattr(ag_mod.VectorStoreService, "create", fake_create)

        tester_node = make_tester_node(fake_factory)
        state: AgentBuildState = {
            "agent_id": "agent-test-2",
            "folder_path": "/tmp/test_docs",
            "skills_created": 0,
            "vector_count": 0,
        }

        result = await tester_node(state)

        # 测试失败，不应启动 watcher
        assert len(start_calls) == 0
        assert result["status"] == "failed"
        assert result["test_result"]["vector_store_ok"] is False

    @pytest.mark.asyncio
    async def test_tester_skips_watcher_when_watchdog_unavailable(self, monkeypatch):
        """watchdog 未安装（is_available=False）→ 不应调 start_watching，构建仍完成。"""
        from app.services import agent_graph as ag_mod
        from app.services.agent_graph import make_tester_node

        start_calls = []

        async def fake_start_watching(agent_id, folder_path):
            start_calls.append((agent_id, folder_path))
            return True

        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.start_watching",
            fake_start_watching,
        )
        # watchdog 不可用
        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.is_available",
            lambda: False,
        )

        class FakeAgent:
            def __init__(self):
                self.status = "ready"

        class FakeResult:
            def scalar_one_or_none(self):
                return FakeAgent()

        class FakeDb:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def execute(self, *args, **kwargs):
                return FakeResult()

        def fake_factory():
            return FakeDb()

        class FakeVectorStore:
            async def count(self):
                return 10

        async def fake_create(name):
            return FakeVectorStore()

        monkeypatch.setattr(ag_mod.VectorStoreService, "create", fake_create)

        tester_node = make_tester_node(fake_factory)
        state: AgentBuildState = {
            "agent_id": "agent-test-3",
            "folder_path": "/tmp/test_docs",
            "skills_created": 2,
            "vector_count": 10,
        }

        result = await tester_node(state)

        # watchdog 不可用，不应启动 watcher，但构建仍完成
        assert len(start_calls) == 0
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_tester_start_watching_failure_does_not_break_build(self, monkeypatch):
        """start_watching 抛异常 → 仅记录 warning，不影响构建返回值。"""
        from app.services import agent_graph as ag_mod
        from app.services.agent_graph import make_tester_node

        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.start_watching",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("权限不足")),
        )
        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.is_available",
            lambda: True,
        )

        class FakeAgent:
            def __init__(self):
                self.status = "ready"

        class FakeResult:
            def scalar_one_or_none(self):
                return FakeAgent()

        class FakeDb:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def execute(self, *args, **kwargs):
                return FakeResult()

        def fake_factory():
            return FakeDb()

        class FakeVectorStore:
            async def count(self):
                return 10

        async def fake_create(name):
            return FakeVectorStore()

        monkeypatch.setattr(ag_mod.VectorStoreService, "create", fake_create)

        tester_node = make_tester_node(fake_factory)
        state: AgentBuildState = {
            "agent_id": "agent-test-4",
            "folder_path": "/tmp/test_docs",
            "skills_created": 2,
            "vector_count": 10,
        }

        # 即使 start_watching 抛异常，tester_node 也应正常返回
        result = await tester_node(state)
        assert result["status"] == "completed"
        assert result["test_result"]["vector_store_ok"] is True

    @pytest.mark.asyncio
    async def test_tester_skips_watcher_without_folder_path(self, monkeypatch):
        """state 无 folder_path → 不应调 start_watching。"""
        from app.services import agent_graph as ag_mod
        from app.services.agent_graph import make_tester_node

        start_calls = []

        async def fake_start_watching(agent_id, folder_path):
            start_calls.append((agent_id, folder_path))
            return True

        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.start_watching",
            fake_start_watching,
        )
        monkeypatch.setattr(
            "app.services.file_watcher.file_watcher_service.is_available",
            lambda: True,
        )

        class FakeAgent:
            def __init__(self):
                self.status = "ready"

        class FakeResult:
            def scalar_one_or_none(self):
                return FakeAgent()

        class FakeDb:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def execute(self, *args, **kwargs):
                return FakeResult()

        def fake_factory():
            return FakeDb()

        class FakeVectorStore:
            async def count(self):
                return 10

        async def fake_create(name):
            return FakeVectorStore()

        monkeypatch.setattr(ag_mod.VectorStoreService, "create", fake_create)

        tester_node = make_tester_node(fake_factory)
        # 无 folder_path
        state: AgentBuildState = {
            "agent_id": "agent-test-5",
            "skills_created": 2,
            "vector_count": 10,
        }

        result = await tester_node(state)
        assert len(start_calls) == 0
        assert result["status"] == "completed"
