"""Production API must not use worker-only recovery grants; close all owned pools."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import database
from app.core import lifespan as lifecycle


@pytest.mark.asyncio
async def test_production_api_does_not_claim_worker_recovery(monkeypatch):
    from app.services.compiler import job_queue

    recovery = AsyncMock(side_effect=AssertionError("API must not recover worker jobs"))
    monkeypatch.setattr(job_queue, "recover_stale_compilation_jobs", recovery)
    monkeypatch.setattr(database, "async_session_factory", lambda: pytest.fail("API opened a recovery session"))
    monkeypatch.setattr(lifecycle.settings, "DEBUG", False)
    monkeypatch.setattr(lifecycle, "engine", SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))

    await lifecycle._reset_interrupted_processing_tasks()
    await lifecycle._recover_stale_compilation_jobs()
    recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_releases_additional_roles_even_if_api_pool_fails(monkeypatch):
    primary = SimpleNamespace(dispose=AsyncMock(side_effect=RuntimeError("primary close failed")))
    worker = SimpleNamespace(dispose=AsyncMock())
    bootstrap = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(lifecycle, "engine", primary)
    monkeypatch.setattr(database, "worker_engine", worker, raising=False)
    monkeypatch.setattr(database, "bootstrap_engine", bootstrap, raising=False)
    for name in ("_verify_database_connection", "_reset_interrupted_processing_tasks", "_recover_stale_compilation_jobs", "init_checkpointer", "_resume_file_watchers", "_stop_file_watchers", "shutdown_checkpointer"):
        monkeypatch.setattr(lifecycle, name, AsyncMock())
    for name in ("_warn_if_metrics_unprotected", "_start_background_services", "_stop_background_services"):
        monkeypatch.setattr(lifecycle, name, lambda: None)

    with pytest.raises(RuntimeError, match="primary close failed"):
        async with lifecycle.lifespan(None):
            pass
    worker.dispose.assert_awaited_once()
    bootstrap.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# AUD-01：授权执行器的注册 / 注销钩子
# ---------------------------------------------------------------------------


def _mock_startup(monkeypatch, *, checkpointer_error=None):
    """把 lifespan 里与本用例无关的启动/关闭动作全部 mock 掉。"""
    for name in ("_verify_database_connection", "_reset_interrupted_processing_tasks",
                 "_recover_stale_compilation_jobs", "_resume_file_watchers",
                 "_stop_file_watchers", "shutdown_checkpointer"):
        monkeypatch.setattr(lifecycle, name, AsyncMock())
    for name in ("_warn_if_metrics_unprotected", "_start_background_services",
                 "_stop_background_services"):
        monkeypatch.setattr(lifecycle, name, lambda: None)
    primary = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(lifecycle, "engine", primary)
    monkeypatch.setattr(database, "async_session_factory", lambda: pytest.fail("opened a session"))
    if checkpointer_error is not None:
        monkeypatch.setattr(
            lifecycle, "init_checkpointer",
            AsyncMock(side_effect=checkpointer_error),
        )
    else:
        monkeypatch.setattr(lifecycle, "init_checkpointer", AsyncMock())
    return primary


@pytest.mark.asyncio
async def test_lifespan_registers_executor_on_healthy_start(monkeypatch):
    """启动成功后端侧读取可用（执行器已注册）。"""
    from app.services.mcp import runner_bridge

    _mock_startup(monkeypatch)
    runner_bridge.register_executor(None)
    try:
        async with lifecycle.lifespan(None):
            assert runner_bridge.executor_configured() is True
    finally:
        runner_bridge.register_executor(None)


@pytest.mark.asyncio
async def test_lifespan_unregisters_executor_on_normal_exit(monkeypatch):
    """正常退出后必须回到"未接入"，不残留上一份执行器。"""
    from app.services.mcp import runner_bridge

    _mock_startup(monkeypatch)
    runner_bridge.register_executor(None)
    async with lifecycle.lifespan(None):
        assert runner_bridge.executor_configured() is True
    assert runner_bridge.executor_configured() is False


@pytest.mark.asyncio
async def test_lifespan_unregisters_executor_when_context_exits_with_exception(monkeypatch):
    """上下文抛异常退出也必须注销（`try/finally`，不是裸 yield）。"""
    from app.services.mcp import runner_bridge

    _mock_startup(monkeypatch)
    runner_bridge.register_executor(None)
    with pytest.raises(RuntimeError, match="boom"):
        async with lifecycle.lifespan(None):
            assert runner_bridge.executor_configured() is True
            raise RuntimeError("boom")
    assert runner_bridge.executor_configured() is False


@pytest.mark.asyncio
async def test_lifespan_does_not_register_executor_when_startup_fails(monkeypatch):
    """启动失败（生产 checkpointer 初始化失败会直接 raise）不得留下已注册执行器。

    注册被放在启动全部成功之后、yield 之前，正是为了这个场景。
    """
    from app.services.mcp import runner_bridge

    _mock_startup(monkeypatch, checkpointer_error=RuntimeError("checkpointer down"))
    monkeypatch.setattr(lifecycle.settings, "DEBUG", False)
    runner_bridge.register_executor(None)
    with pytest.raises(RuntimeError, match="checkpointer down"):
        async with lifecycle.lifespan(None):
            pytest.fail("启动失败时不应进入服务状态")
    assert runner_bridge.executor_configured() is False
