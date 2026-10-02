"""耐久编译 Worker 的增量执行语义回归测试。"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.compiler import job_queue
from app.services.compiler.job_queue import ClaimedCompilationJob


@pytest.mark.asyncio
async def test_claimed_incremental_job_executes_incremental_pipeline_with_snapshot(monkeypatch):
    claimed = ClaimedCompilationJob(
        id="job-1",
        enterprise_id="enterprise-1",
        folder_path="/safe/snapshot",
        interview_completion=0.35,
        affected_stages=("process", "capability", "runtime"),
        attempt=1,
    )
    pipeline = type("Pipeline", (), {
        "run_incremental": AsyncMock(),
        "run_full": AsyncMock(),
    })()

    session = object()

    @asynccontextmanager
    async def fake_session_factory():
        yield session

    pipeline_factory = MagicMock(return_value=pipeline)
    release_lease = AsyncMock()
    # 队列代码已改用受限 Worker 会话工厂；夹具必须跟着改
    monkeypatch.setattr(job_queue, "worker_session_factory", fake_session_factory)
    monkeypatch.setattr(job_queue, "_create_pipeline", pipeline_factory)
    monkeypatch.setattr(job_queue, "_release_terminal_lease", release_lease)

    await job_queue.execute_claimed_compilation_job(claimed, "worker-1")

    pipeline_factory.assert_called_once_with(session, "enterprise-1", "worker-1")
    pipeline.run_incremental.assert_awaited_once_with(
        "job-1",
        ["process", "capability", "runtime"],
        0.35,
        folder_path="/safe/snapshot",
    )
    pipeline.run_full.assert_not_awaited()
    release_lease.assert_awaited_once_with("job-1", "worker-1")
