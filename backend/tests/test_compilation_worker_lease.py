"""编译 Worker 租约失效演练测试。

覆盖三类故障场景，验证任务不丢、结果不被旧 Worker 覆盖：
1. 租约过期后被其他 Worker 接管（attempt 递增、留接管审计信息）
2. 失去租约的旧 Worker 心跳/失败标记均被拒绝
3. Worker 重启（原持有者消失）后任务可被重新领取，且 started_at 不被重置
"""
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine

from app.database import Base
import app.models  # noqa: F401 - 确保全部表注册进 metadata
from app.models.compiler import CompilationJob
from app.models.enterprise import Enterprise
from app.services.compiler import job_queue
from app.utils.time import utcnow


@pytest_asyncio.fixture
async def lease_db(monkeypatch):
    """独立内存库，并把 job_queue 的会话工厂指到该库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # 队列代码已改用受限 Worker 会话工厂；夹具必须跟着改，否则会连到别的库
    monkeypatch.setattr(job_queue, "worker_session_factory", factory)

    async with factory() as db:
        db.add(Enterprise(id="ent-lease", name="租约测试企业"))
        await db.commit()
    yield factory
    await engine.dispose()


async def _insert_job(factory, **overrides) -> str:
    async with factory() as db:
        job = CompilationJob(
            enterprise_id="ent-lease",
            trigger_source="manual",
            folder_path="/safe/folder",
            interview_completion=0.0,
            idempotency_key=f"key-{uuid.uuid4().hex}",
            status="queued",
            stage="information",
            progress=0.0,
            **overrides,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return str(job.id)


async def _get_job(factory, job_id: str) -> CompilationJob:
    async with factory() as db:
        result = await db.execute(
            select(CompilationJob).where(CompilationJob.id == job_id)
        )
        job = result.scalar_one()
        db.expunge(job)
        return job


@pytest.mark.asyncio
async def test_expired_lease_is_taken_over_by_another_worker(lease_db):
    """租约过期后任务被新 Worker 接管：attempt+1 且留下接管审计信息。"""
    job_id = await _insert_job(lease_db)

    claimed_a = await job_queue.claim_next_compilation_job("worker-a", lease_seconds=300)
    assert claimed_a is not None and claimed_a.id == job_id
    job = await _get_job(lease_db, job_id)
    assert job.status == "running"
    assert job.lease_owner == "worker-a"
    first_started_at = job.started_at

    # 未到期的租约不能被其他 Worker 抢走
    assert await job_queue.claim_next_compilation_job("worker-b", lease_seconds=300) is None

    # 模拟 worker-a 失联：租约自然过期
    async with lease_db() as db:
        result = await db.execute(select(CompilationJob).where(CompilationJob.id == job_id))
        job = result.scalar_one()
        job.lease_until = utcnow() - timedelta(seconds=1)
        await db.commit()

    # 过期后任务被 worker-b 接管：attempt+1 且保留 started_at
    claimed_b = await job_queue.claim_next_compilation_job("worker-b", lease_seconds=300)
    assert claimed_b is not None and claimed_b.id == job_id
    assert claimed_b.attempt == 2

    job = await _get_job(lease_db, job_id)
    assert job.lease_owner == "worker-b"
    assert job.attempt == 2
    assert job.started_at == first_started_at, "接管不应重置 started_at"
    assert "接管" in (job.error_message or "")


@pytest.mark.asyncio
async def test_stale_worker_cannot_extend_lease_or_mark_failure(lease_db):
    """失去租约的旧 Worker：心跳续租被拒、失败标记也不得覆盖接管者。"""
    job_id = await _insert_job(lease_db)

    assert await job_queue.claim_next_compilation_job("worker-a", lease_seconds=300)
    # worker-b 接管（构造上直接让 a 的租约过期）
    async with lease_db() as db:
        result = await db.execute(select(CompilationJob).where(CompilationJob.id == job_id))
        job = result.scalar_one()
        job.lease_until = utcnow() - timedelta(seconds=1)
        await db.commit()
    assert await job_queue.claim_next_compilation_job("worker-b", lease_seconds=300)

    # 旧 Worker a 的心跳续租被拒
    assert await job_queue.heartbeat_compilation_job(job_id, "worker-a", 300) is False
    # 新持有者 b 的心跳续租成功
    assert await job_queue.heartbeat_compilation_job(job_id, "worker-b", 300) is True

    # 旧 Worker a 的失败标记不会覆盖 b 的租约状态
    await job_queue._mark_failed(job_id, "worker-a", RuntimeError("stale write"))
    job = await _get_job(lease_db, job_id)
    assert job.status == "running"
    assert job.lease_owner == "worker-b"
    assert job.error_message is None or "stale write" not in job.error_message

    # 而持有者 b 自己标记失败是允许的
    await job_queue._mark_failed(job_id, "worker-b", RuntimeError("real failure"))
    job = await _get_job(lease_db, job_id)
    assert job.status == "failed"
    assert "real failure" in job.error_message


@pytest.mark.asyncio
async def test_job_is_reclaimable_after_worker_restart(lease_db):
    """Worker 重启后（原持有者不再续租），任务被新进程重新领取并继续。"""
    job_id = await _insert_job(lease_db)

    # "旧进程"领取后崩溃：既不执行也不释放
    claimed = await job_queue.claim_next_compilation_job("old-process", lease_seconds=1)
    assert claimed is not None

    # 租约（1s）到期后，"重启的新进程"以全新 worker id 领取同一任务
    async with lease_db() as db:
        result = await db.execute(select(CompilationJob).where(CompilationJob.id == job_id))
        job_row = result.scalar_one()
        job_row.lease_until = utcnow() - timedelta(seconds=1)
        await db.commit()

    reclaimed = await job_queue.claim_next_compilation_job("new-process", lease_seconds=300)
    assert reclaimed is not None
    assert reclaimed.id == job_id
    assert reclaimed.attempt == 2

    job = await _get_job(lease_db, job_id)
    assert job.lease_owner == "new-process"
    assert job.status == "running"
