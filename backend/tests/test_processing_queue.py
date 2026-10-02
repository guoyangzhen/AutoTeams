"""文档处理耐久队列测试（AUD-17）。

复现来源：审计 §AUD-17 —— 文档处理任务依赖进程内 `asyncio.Task`，重启清理会把
其他实例正在执行的任务误标为 failed；取消信号也无法跨进程投递。

覆盖：
1. 入队 → 领取：第二个 worker 领不到同一任务；
2. 租约过期可重领，`attempt` 递增；
3. **有效租约不被恢复逻辑回收，也不被其他 worker 抢占**；
4. 取消是数据库标志，跨 session 生效；
5. 崩溃（不续租）后重新投递，而不是永久卡在 processing；
6. 幂等键让重复提交复用既有任务。
"""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enterprise import Enterprise
from app.models.processing_task import ProcessingTask
from app.models.user import User
from app.services.processing_queue import (
    LEASE_GRACE_SECONDS,
    claim_next_processing_task,
    complete_processing_task,
    enqueue_processing_task,
    fail_processing_task,
    find_active_by_idempotency_key,
    heartbeat_processing_task,
    is_cancel_requested,
    make_idempotency_key,
    mark_cancelled,
    recover_stale_processing_tasks,
    request_cancel,
    worker_identity,
)
from app.utils.time import utcnow

ENTERPRISE_ID = "ent-processing-queue"


@pytest.fixture
def make_session(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    def _factory():
        return factory()

    return _factory


async def _seed_task(session, **overrides) -> ProcessingTask:
    """创建一条任务及其租户依赖。"""
    user_id = overrides.pop("user_id", f"user-{uuid.uuid4().hex[:8]}")
    session.add(Enterprise(id=ENTERPRISE_ID, name="队列实验室"))
    session.add(
        User(
            id=user_id,
            email=f"{user_id}@test.com",
            password_hash="x",
            name="队列用户",
            role="admin",
            enterprise_id=ENTERPRISE_ID,
        )
    )
    task = ProcessingTask(
        user_id=user_id,
        enterprise_id=ENTERPRISE_ID,
        folder_path=overrides.pop("folder_path", "/tmp/docs"),
        agent_name=overrides.pop("agent_name", "队列Agent"),
        agent_description="",
        status=overrides.pop("status", "pending"),
        **overrides,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@pytest.mark.asyncio
async def test_enqueue_then_claim_excludes_from_second_worker(make_session):
    """领取后第二个 worker 领不到同一任务。"""
    async with make_session() as session:
        task = await _seed_task(session)
        await enqueue_processing_task(
            session,
            task_id=task.id,
            idempotency_key="k1",
            run_config={"model": None},
        )

    async with make_session() as session:
        first = await claim_next_processing_task(session, "worker-A", lease_seconds=90)
        assert first is not None
        assert first.task_id == task.id

    async with make_session() as session:
        second = await claim_next_processing_task(session, "worker-B", lease_seconds=90)
        assert second is None, "同一任务不能被两个 worker 同时领取"


@pytest.mark.asyncio
async def test_expired_lease_is_recoverable_and_attempt_increments(make_session):
    """租约过期后任务可被重新领取，attempt 递增。"""
    from datetime import timedelta

    async with make_session() as session:
        task = await _seed_task(session, lease_owner="worker-A", attempt=1)
        task.status = "processing"
        task.lease_until = utcnow() - timedelta(seconds=LEASE_GRACE_SECONDS + 30)
        await session.commit()

        recovered = await recover_stale_processing_tasks(session)
        assert recovered == 1

    async with make_session() as session:
        reclaimed = await claim_next_processing_task(session, "worker-B", lease_seconds=90)
        assert reclaimed is not None
        assert reclaimed.attempt == 2


@pytest.mark.asyncio
async def test_valid_lease_is_not_recovered_or_stolen(make_session):
    """另一 worker 持有的**有效租约**既不被恢复，也不被抢占。"""
    from datetime import timedelta

    async with make_session() as session:
        task = await _seed_task(session, lease_owner="worker-A", attempt=1)
        task.status = "processing"
        task.lease_until = utcnow() + timedelta(seconds=600)
        await session.commit()

        recovered = await recover_stale_processing_tasks(session)
        assert recovered == 0, "有效租约不得被启动恢复误伤"

        stolen = await claim_next_processing_task(session, "worker-B", lease_seconds=90)
        assert stolen is None, "processing 且租约有效的任务不可被其他 worker 领取"

        still = await session.get(ProcessingTask, task.id)
        assert still.lease_owner == "worker-A"
        assert still.status == "processing"


@pytest.mark.asyncio
async def test_heartbeat_extends_own_lease_only(make_session):
    """续租只对租约持有者生效。"""
    from datetime import timedelta

    async with make_session() as session:
        task = await _seed_task(session, lease_owner="worker-A")
        task.status = "processing"
        task.lease_until = utcnow() + timedelta(seconds=5)
        await session.commit()

        assert await heartbeat_processing_task(session, task.id, "worker-A") is True
        assert await heartbeat_processing_task(session, task.id, "worker-B") is False


@pytest.mark.asyncio
async def test_cancel_is_a_database_signal_visible_from_another_session(make_session):
    """取消是跨进程信号：不同 session（模拟另一进程）也能观察到。"""
    async with make_session() as session:
        task = await _seed_task(session)
        await request_cancel(session, task.id)

    async with make_session() as session:
        assert await is_cancel_requested(session, task.id) is True


@pytest.mark.asyncio
async def test_worker_observes_cancel_and_marks_cancelled(make_session):
    """worker 观察到取消信号后协作式停止并置为 cancelled。"""
    async with make_session() as session:
        task = await _seed_task(session)
        claimed = await claim_next_processing_task(session, "worker-A", lease_seconds=90)
        assert claimed is not None

    async with make_session() as session:
        await request_cancel(session, task.id)

    async with make_session() as session:
        assert await is_cancel_requested(session, task.id) is True
        await mark_cancelled(session, task.id, "worker-A")

    async with make_session() as session:
        final = await session.get(ProcessingTask, task.id)
        assert final.status == "cancelled"
        assert final.lease_owner is None


@pytest.mark.asyncio
async def test_crash_without_heartbeat_results_in_redelivery(make_session):
    """worker 崩溃（不续租）后任务重新投递，而不是永久卡在 processing。"""
    from datetime import timedelta

    async with make_session() as session:
        task = await _seed_task(session, lease_owner="crashed-worker", attempt=1)
        task.status = "processing"
        task.lease_until = utcnow() - timedelta(seconds=LEASE_GRACE_SECONDS + 5)
        await session.commit()

        recovered = await recover_stale_processing_tasks(session)
        assert recovered == 1

    async with make_session() as session:
        redelivered = await claim_next_processing_task(session, "worker-B", lease_seconds=90)
        assert redelivered is not None
        assert redelivered.task_id == task.id


@pytest.mark.asyncio
async def test_completion_releases_lease(make_session):
    """完成后释放租约并落终态。"""
    async with make_session() as session:
        task = await _seed_task(session)
        await claim_next_processing_task(session, "worker-A", lease_seconds=90)
        await complete_processing_task(
            session,
            task.id,
            "worker-A",
            progress=1.0,
            message="完成",
            total_files=3,
            processed_files=3,
            knowledge_count=5,
            external_run_id="run-1",
        )

    async with make_session() as session:
        final = await session.get(ProcessingTask, task.id)
        assert final.status == "completed"
        assert final.lease_owner is None
        assert final.external_run_id == "run-1"
        assert final.knowledge_count == 5


@pytest.mark.asyncio
async def test_fail_releases_lease_and_records_error(make_session):
    async with make_session() as session:
        task = await _seed_task(session)
        await claim_next_processing_task(session, "worker-A", lease_seconds=90)
        await fail_processing_task(
            session,
            task.id,
            "worker-A",
            error="boom",
            error_entry={"error": "boom"},
        )

    async with make_session() as session:
        final = await session.get(ProcessingTask, task.id)
        assert final.status == "failed"
        assert final.lease_owner is None
        assert any(entry.get("error") == "boom" for entry in final.error_log)


@pytest.mark.asyncio
async def test_idempotency_key_dedupes_active_task(make_session):
    """同一 (企业, 目录, Agent) 的未完成任务不会重复入队。"""
    key_a = make_idempotency_key(ENTERPRISE_ID, "/tmp/a", "AgentA")
    key_b = make_idempotency_key(ENTERPRISE_ID, "/tmp/a", "AgentA")
    key_c = make_idempotency_key(ENTERPRISE_ID, "/tmp/a", "AgentB")
    assert key_a == key_b
    assert key_a != key_c

    async with make_session() as session:
        task = await _seed_task(session)
        await enqueue_processing_task(
            session, task_id=task.id, idempotency_key=key_a, run_config={}
        )

    async with make_session() as session:
        found = await find_active_by_idempotency_key(session, key_a)
        assert found is not None
        assert found.id == task.id
        assert await find_active_by_idempotency_key(session, key_c) is None


@pytest.mark.asyncio
async def test_worker_identity_is_stable(make_session):
    assert worker_identity() == worker_identity()
