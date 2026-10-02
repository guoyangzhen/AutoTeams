"""真实 PostgreSQL 上的 Runner 执行回执并发验收（AUD-18 行锁）。

为什么必须真实 PG：``report_result`` 的并发正确性依赖
``SELECT ... FOR UPDATE`` 行锁。SQLite 会**静默忽略**该子句，因此 SQLite 单测
（``tests/test_runner_v2.py``）只能证明"代码写了 with_for_update"，无法证明
"两个 API 实例并发提交同一 step 时不会重复记账"。

本模块用两条**独立** ``AsyncSession``（= 两个连接 = 两个 API 实例的等价物）
真并发提交回执，断言：

1. 同一 step 并发：confirmed 集合不重复记账，终态审计只有一条；
2. 不同 step 并发：confirmed 集合完整（两个 step 都在），终态审计只有一条；
3. 失败与成功竞争：失败**不得**被后续成功覆盖。

启用方式
--------
默认**跳过**。需要一个已迁移到 head 的**专用**数据库：

* 目标库：``autoteams_runner_acceptance``（本模块只读写自己插入的行）；
* 环境变量 ``RUNNER_RECEIPTS_PG_URL``，例如::

      postgresql+asyncpg://user:pass@127.0.0.1:55434/autoteams_runner_acceptance

不自动建库、不自动跑 Alembic：迁移包含集群级角色操作，应由测试编排层在
专用库串行完成。显式配置 URL 后缺少业务表属于验收失败，不静默跳过。
"""

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.enterprise import Enterprise
from app.models.runner_v2 import RunnerAuditEntry, RunnerDevice, RunnerTask
from app.models.user import User
from app.services.runner_v2_protocol import (
    RECEIPT_LEDGER_KEY,
    PhysicalProtocolError,
    RunnerV2Protocol,
    TaskState,
)

PG_URL_ENV = "RUNNER_RECEIPTS_PG_URL"

#: 收敛上限：行锁等待不应超过这个时间，超过说明锁没有按预期释放。
CONCURRENCY_TIMEOUT_SECONDS = 30.0

pytestmark = pytest.mark.skipif(
    not os.environ.get(PG_URL_ENV, "").strip(),
    reason=f"未设置 {PG_URL_ENV}：跳过真实 PostgreSQL 行锁验收",
)

REQUIRED_TABLES = (
    "enterprises",
    "users",
    "runner_devices",
    "runner_tasks",
    "runner_audit_entries",
)


@pytest_asyncio.fixture
async def pg_engine():
    """每个测试独立连接池，避免跨 pytest 事件循环复用 asyncpg 连接。"""
    url = os.environ[PG_URL_ENV].strip()

    engine = create_async_engine(url, pool_size=4, max_overflow=4, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            present = set(
                (
                    await conn.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).scalars()
            )
        missing = [name for name in REQUIRED_TABLES if name not in present]
        if missing:
            pytest.fail(f"验收库缺少表 {missing}：请先在专用库执行 alembic upgrade head")
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def factory(pg_engine):
    return async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def ledger(factory):
    """建一组企业/用户/设备，返回上下文与清理协程。"""
    enterprise_id = f"ent-pg-{uuid.uuid4().hex[:12]}"
    user_id = f"user-pg-{uuid.uuid4().hex[:12]}"
    device_id = f"dev-pg-{uuid.uuid4().hex[:12]}"
    task_ids: list[str] = []

    async with factory() as session:
        session.add(Enterprise(id=enterprise_id, name="PG 行锁验收"))
        await session.flush()
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@acceptance.invalid",
                password_hash="not-used-in-acceptance",
                name="PG 验收操作员",
                role="admin",
                enterprise_id=enterprise_id,
            )
        )
        await session.flush()
        session.add(
            RunnerDevice(
                id=device_id,
                enterprise_id=enterprise_id,
                owner_user_id=user_id,
                runner_id=f"runner-pg-{uuid.uuid4().hex[:8]}",
                scopes=["physical"],
                platform="win32",
                version="2.0.0",
                capabilities={},
                status="active",
            )
        )
        await session.commit()

    async def make_task(steps: int) -> str:
        task_id = f"ptask-pg-{uuid.uuid4().hex[:12]}"
        task_ids.append(task_id)
        async with factory() as session:
            session.add(
                RunnerTask(
                    task_id=task_id,
                    enterprise_id=enterprise_id,
                    device_id=device_id,
                    channel="browser_action",
                    state=TaskState.EXECUTING.value,
                    verdict={},
                    steps=[
                        {
                            "op": "navigate",
                            "url": f"https://erp.example.com/pg/{index}",
                            "step_id": f"s{index}",
                        }
                        for index in range(steps)
                    ],
                    created_by=user_id,
                )
            )
            await session.commit()
        return task_id

    async def cleanup() -> None:
        async with factory() as session:
            if task_ids:
                await session.execute(
                    delete(RunnerAuditEntry).where(RunnerAuditEntry.task_id.in_(task_ids))
                )
                await session.execute(
                    delete(RunnerTask).where(RunnerTask.task_id.in_(task_ids))
                )
            await session.execute(
                delete(RunnerDevice).where(RunnerDevice.enterprise_id == enterprise_id)
            )
            await session.execute(delete(User).where(User.enterprise_id == enterprise_id))
            await session.execute(
                delete(Enterprise).where(Enterprise.id == enterprise_id)
            )
            await session.commit()

    try:
        yield {"enterprise_id": enterprise_id, "device_id": device_id, "make_task": make_task}
    finally:
        await cleanup()



def _confirmed(result) -> set[str]:
    """从任务结果中取出已确认的 step 身份集合（账本是内部结构，不进 API 响应）。"""
    ledger = (result or {}).get(RECEIPT_LEDGER_KEY) or {}
    return set(ledger.get("step_ids") or [])


async def _report(factory, device_id: str, task_id: str, step_id: str, ok: bool) -> dict:
    """以一条独立会话/连接提交一条回执（等价于另一个 API 实例）。"""
    protocol = RunnerV2Protocol()
    async with factory() as session:
        device = await session.get(RunnerDevice, device_id)
        return await protocol.report_result(
            session,
            device=device,
            task_id=task_id,
            ok=ok,
            data={"step": step_id},
            receipt_id=f"{task_id}::{step_id}",
            step_id=step_id,
        )


async def _final_state(factory, task_id: str):
    async with factory() as session:
        task = (await session.execute(
            select(RunnerTask).where(RunnerTask.task_id == task_id)
        )).scalar_one()
        audits = list(
            (
                await session.execute(
                    select(RunnerAuditEntry).where(RunnerAuditEntry.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        return task.state, _confirmed(task.result), audits


@pytest.mark.asyncio
async def test_concurrent_same_step_receipt_is_recorded_once(factory, ledger):
    """两条独立会话并发提交**同一** step：行锁串行化后只记一次账。"""
    task_id = await ledger["make_task"](2)

    results = await asyncio.wait_for(
        asyncio.gather(
            _report(factory, ledger["device_id"], task_id, "s0", True),
            _report(factory, ledger["device_id"], task_id, "s0", True),
        ),
        timeout=CONCURRENCY_TIMEOUT_SECONDS,
    )
    assert all(item["state"] == TaskState.EXECUTING.value for item in results)

    state, confirmed, audits = await _final_state(factory, task_id)
    assert state == TaskState.EXECUTING.value
    assert confirmed == {"s0"}, "同一 step 不得被记账两次"
    assert sum(1 for a in audits if a.event == "task_step_reported") == 1
    assert not any(a.event in {"task_completed", "task_failed"} for a in audits)


@pytest.mark.asyncio
async def test_concurrent_distinct_steps_complete_once(factory, ledger):
    """两条独立会话并发提交**不同** step：confirmed 集合完整，终态只有一次。"""
    task_id = await ledger["make_task"](2)

    await asyncio.wait_for(
        asyncio.gather(
            _report(factory, ledger["device_id"], task_id, "s0", True),
            _report(factory, ledger["device_id"], task_id, "s1", True),
        ),
        timeout=CONCURRENCY_TIMEOUT_SECONDS,
    )

    state, confirmed, audits = await _final_state(factory, task_id)
    assert confirmed == {"s0", "s1"}, "两个 step 都必须记入账本"
    assert state == TaskState.COMPLETED.value
    assert sum(1 for a in audits if a.event == "task_completed") == 1
    assert sum(1 for a in audits if a.event == "task_step_reported") == 1


@pytest.mark.asyncio
async def test_failure_is_not_overwritten_by_concurrent_success(factory, ledger):
    """失败与成功竞争：终态必须是 failed，不得被成功覆盖。"""
    task_id = await ledger["make_task"](2)

    outcomes = await asyncio.wait_for(
        asyncio.gather(
            _report(factory, ledger["device_id"], task_id, "s0", False),
            _report(factory, ledger["device_id"], task_id, "s1", True),
            return_exceptions=True,
        ),
        timeout=CONCURRENCY_TIMEOUT_SECONDS,
    )
    # 落败的一条会收到"任务已终结"（409 语义的 PhysicalProtocolError）；
    # 不允许出现任何其他异常类型。
    for outcome in outcomes:
        assert isinstance(outcome, (dict, PhysicalProtocolError)), outcome

    state, confirmed, audits = await _final_state(factory, task_id)
    assert state == TaskState.FAILED.value, f"失败被覆盖: {state} / {confirmed}"
    assert sum(1 for a in audits if a.event == "task_failed") == 1
    assert not any(a.event == "task_completed" for a in audits)
