"""文档处理任务的耐久队列（AUD-17）。

历史问题（审计 §AUD-17）
-----------------------
`app/api/process.py` 用模块级 `_running_tasks: dict[str, asyncio.Task]` 执行业务，
并由 `lifespan._reset_interrupted_processing_tasks()` 在启动时把**所有**
`processing` 任务标成 failed。后果：

1. 请求进到另一个进程时，`/process/{id}/cancel` 查不到 `asyncio.Task`，取消无效；
2. 滚动更新启动任一实例，会把仍由其他实例**正在执行**的任务误标为 failed；
3. 数据库状态与真实副作用可能分离（任务标 failed，但 LangGraph 仍在写文件）。

本模块给出与 `compiler/job_queue.py` 一致的租约模型：

* **领取**：只有 `pending`（或租约已过期）的任务可被领取，领取即写租约；
* **续租**：执行中周期性 `heartbeat_at` + `lease_until` 延长；
* **恢复**：只回收**租约已过期**或**从未持有租约**的 `processing` 任务，
  绝不碰另一个实例仍然持有的有效租约；
* **取消**：`cancel_requested` 是跨进程信号，API 只写库，worker 观察后协作停止；
* **幂等**：`idempotency_key` 让同一 (企业, 目录, Agent 名) 的重复提交只执行一次；
  `external_run_id` 记录已经产生过副作用的运行标识，重投时据此跳过。
"""
from __future__ import annotations

import hashlib
import json
import logging
import socket
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.processing_task import ProcessingTask
from app.utils.time import utcnow
from app.services.queue_claim import call_queue_function, call_queue_scalar, is_postgres

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 90
#: 视为"可被重新领取"的最小租约过期时间，防止刚入队就被别的实例抢走。
LEASE_GRACE_SECONDS = 5

#: 终态：不会再被领取。
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite 取回的 datetime 可能丢时区，统一补成 UTC 再比较。"""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def worker_identity() -> str:
    """稳定的 worker 身份（进程内复用，便于日志与租约归属排查）。"""
    global _WORKER_IDENTITY
    if _WORKER_IDENTITY is None:
        _WORKER_IDENTITY = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    return _WORKER_IDENTITY


_WORKER_IDENTITY: Optional[str] = None


def make_idempotency_key(
    enterprise_id: str, folder_path: str, agent_name: str
) -> str:
    """同一 (企业, 目录, Agent 名) 的稳定幂等键。"""
    raw = json.dumps(
        {"e": enterprise_id, "f": folder_path, "a": agent_name},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


@dataclass
class ClaimedProcessingTask:
    """被某个 worker 领取的任务及其租约。"""

    task_id: str
    enterprise_id: str
    folder_path: str
    agent_name: Optional[str]
    agent_description: Optional[str]
    lease_until: datetime
    run_config: dict[str, Any]
    attempt: int


async def enqueue_processing_task(
    db: AsyncSession,
    *,
    task_id: str,
    idempotency_key: str,
    run_config: dict[str, Any],
) -> ProcessingTask:
    """把任务置为可领取状态（status=pending）。

    只做入队，不启动任何协程 —— 执行由独立的 worker 进程负责。
    """
    task = await db.get(ProcessingTask, task_id)
    if task is None:
        raise ValueError(f"处理任务不存在: {task_id}")
    task.status = "pending"
    task.idempotency_key = idempotency_key
    task.run_config = run_config
    task.cancel_requested = False
    task.lease_owner = None
    task.lease_until = None
    await db.commit()
    await db.refresh(task)
    return task


async def claim_next_processing_task(
    db: AsyncSession,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Optional[ClaimedProcessingTask]:
    """领取一个可执行的处理任务。

    候选集合 = ``pending`` 且未请求取消的任务。
    已被别的 worker 领取且租约仍有效的任务不会出现在这里。
    """
    if is_postgres(db):
        # 跨租户领取：只授予队列角色，API 运行角色会被数据库拒绝（42501）。
        rows = await call_queue_function(
            db,
            "SELECT * FROM public.app_claim_processing_task(:worker_id, :lease_seconds)",
            {"worker_id": worker_id, "lease_seconds": lease_seconds},
        )
        if not rows:
            return None
        row = rows[0]
        logger.info(
            "worker %s 领取处理任务 %s (attempt=%s)", worker_id, row["id"], row["attempt"]
        )
        return ClaimedProcessingTask(
            task_id=str(row["id"]),
            enterprise_id=str(row["enterprise_id"]),
            folder_path=str(row["folder_path"]),
            agent_name=row["agent_name"],
            agent_description=row["agent_description"],
            lease_until=row["lease_until"],
            run_config=dict(row["run_config"] or {}),
            attempt=int(row["attempt"]),
        )

    now = utcnow()
    lease_until = now + timedelta(seconds=lease_seconds)
    stmt = (
        select(ProcessingTask)
        .where(
            ProcessingTask.status == "pending",
            ProcessingTask.cancel_requested.is_(False),
        )
        .order_by(ProcessingTask.created_at.asc())
        .limit(1)
    )
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if task is None:
        return None

    # 乐观领取：只有仍处于 pending 的那一行会被更新成功。
    claimed = await db.execute(
        update(ProcessingTask)
        .where(
            ProcessingTask.id == task.id,
            ProcessingTask.status == "pending",
        )
        .values(
            status="processing",
            lease_owner=worker_id,
            lease_until=lease_until,
            heartbeat_at=now,
            started_at=task.started_at or now,
            message="任务已由队列 worker 领取",
            attempt=ProcessingTask.attempt + 1,
            worker_id=worker_id,
        )
    )
    if claimed.rowcount != 1:
        # 被并发 worker 抢走，放弃本次领取。
        await db.rollback()
        return None

    await db.commit()
    await db.refresh(task)
    logger.info(
        "worker %s 领取处理任务 %s (attempt=%s)", worker_id, task.id, task.attempt
    )
    return ClaimedProcessingTask(
        task_id=task.id,
        enterprise_id=task.enterprise_id,
        folder_path=task.folder_path,
        agent_name=task.agent_name,
        agent_description=task.agent_description,
        lease_until=lease_until,
        run_config=dict(task.run_config or {}),
        attempt=task.attempt,
    )


async def heartbeat_processing_task(
    db: AsyncSession, task_id: str, worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> bool:
    """续租。租约已不属于本 worker 时返回 False（worker 应停止执行）。"""
    now = utcnow()
    result = await db.execute(
        update(ProcessingTask)
        .where(
            ProcessingTask.id == task_id,
            ProcessingTask.lease_owner == worker_id,
        )
        .values(heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds))
    )
    await db.commit()
    return result.rowcount == 1


async def is_cancel_requested(db: AsyncSession, task_id: str) -> bool:
    """读取跨进程取消信号。"""
    task = await db.get(ProcessingTask, task_id)
    return bool(task and task.cancel_requested)


async def request_cancel(db: AsyncSession, task_id: str) -> bool:
    """写取消信号（跨进程生效，不要求与执行进程相同）。"""
    result = await db.execute(
        update(ProcessingTask)
        .where(ProcessingTask.id == task_id, ProcessingTask.status != "cancelled")
        .values(cancel_requested=True, message="收到取消请求，正在停止")
    )
    await db.commit()
    return result.rowcount > 0


async def recover_stale_processing_tasks(
    db: AsyncSession, grace_seconds: int = LEASE_GRACE_SECONDS
) -> int:
    """只回收**租约已过期**或**从未持有租约**的 processing 任务。

    这是 AUD-17 的关键：历史实现在启动时把所有 `processing` 标成 failed，
    会误伤其他实例正在执行的任务。持有有效租约的任务必须原样保留。
    """
    if is_postgres(db):
        # 跨租户回收：同样只属于队列角色。
        recovered = int(
            await call_queue_scalar(
                db,
                "SELECT public.app_recover_processing_tasks(:grace_seconds)",
                {"grace_seconds": grace_seconds},
            )
            or 0
        )
        if recovered:
            logger.warning("回收了 %s 个租约过期的处理任务", recovered)
        return recovered

    now = utcnow()
    cutoff = now - timedelta(seconds=grace_seconds)

    expired_stmt = select(ProcessingTask).where(
        ProcessingTask.status == "processing",
        or_(
            ProcessingTask.lease_until.is_(None),
            ProcessingTask.lease_until < cutoff,
        ),
    )
    expired = (await db.execute(expired_stmt)).scalars().all()

    for task in expired:
        task.status = "pending"
        task.lease_owner = None
        task.lease_until = None
        task.worker_id = None
        task.message = (
            "上次执行的 worker 租约已过期，重新排队"
            if task.attempt
            else "任务恢复排队"
        )
    if expired:
        await db.commit()
        logger.warning("回收了 %s 个租约过期的处理任务", len(expired))
    return len(expired)


async def complete_processing_task(
    db: AsyncSession,
    task_id: str,
    worker_id: str,
    *,
    progress: float,
    message: str,
    agent_id: Optional[str] = None,
    total_files: int = 0,
    processed_files: int = 0,
    knowledge_count: int = 0,
    external_run_id: Optional[str] = None,
) -> None:
    """标记任务完成并释放租约。"""
    now = utcnow()
    values: dict[str, Any] = {
        "status": "completed",
        "progress": progress,
        "message": message,
        "completed_at": now,
        "lease_owner": None,
        "lease_until": None,
        "total_files": total_files,
        "processed_files": processed_files,
        "knowledge_count": knowledge_count,
    }
    if agent_id:
        values["agent_id"] = agent_id
    if external_run_id:
        # 记录外部执行标识：重复投递时据此判定"已产生副作用"，不再重放。
        values["external_run_id"] = external_run_id
    await db.execute(
        update(ProcessingTask)
        .where(ProcessingTask.id == task_id, ProcessingTask.lease_owner == worker_id)
        .values(**values)
    )
    await db.commit()


async def fail_processing_task(
    db: AsyncSession,
    task_id: str,
    worker_id: str,
    *,
    error: str,
    error_entry: Optional[dict[str, Any]] = None,
) -> None:
    """标记任务失败并释放租约。"""
    now = utcnow()
    task = await db.get(ProcessingTask, task_id)
    if task is None:
        return
    log_entries = list(task.error_log or [])
    if error_entry:
        log_entries.append(error_entry)
    if len(log_entries) > 100:
        log_entries = log_entries[-100:]
    await db.execute(
        update(ProcessingTask)
        .where(ProcessingTask.id == task_id, ProcessingTask.lease_owner == worker_id)
        .values(
            status="failed",
            progress=0.0,
            message="处理失败，请查看错误日志或联系管理员",
            completed_at=now,
            error_log=log_entries,
            lease_owner=None,
            lease_until=None,
        )
    )
    await db.commit()
    logger.error("处理任务 %s 失败: %s", task_id, error)


async def mark_cancelled(db: AsyncSession, task_id: str, worker_id: str) -> None:
    """任务被协作式取消。"""
    now = utcnow()
    await db.execute(
        update(ProcessingTask)
        .where(ProcessingTask.id == task_id, ProcessingTask.lease_owner == worker_id)
        .values(
            status="cancelled",
            progress=0.0,
            message="用户手动取消",
            completed_at=now,
            cancel_requested=True,
            lease_owner=None,
            lease_until=None,
        )
    )
    await db.commit()


async def record_external_run_id(
    db: AsyncSession, task_id: str, worker_id: str, external_run_id: str
) -> None:
    """在产生副作用**之前**登记外部执行标识。"""
    await db.execute(
        update(ProcessingTask)
        .where(ProcessingTask.id == task_id, ProcessingTask.lease_owner == worker_id)
        .values(external_run_id=external_run_id)
    )
    await db.commit()


async def find_active_by_idempotency_key(
    db: AsyncSession, idempotency_key: str
) -> Optional[ProcessingTask]:
    """按幂等键查找未完成任务，避免重复排队。"""
    result = await db.execute(
        select(ProcessingTask).where(
            ProcessingTask.idempotency_key == idempotency_key,
            ProcessingTask.status.notin_(TERMINAL_STATUSES),
        )
    )
    return result.scalar_one_or_none()


__all__ = [
    "ClaimedProcessingTask",
    "DEFAULT_LEASE_SECONDS",
    "LEASE_GRACE_SECONDS",
    "TERMINAL_STATUSES",
    "claim_next_processing_task",
    "complete_processing_task",
    "enqueue_processing_task",
    "fail_processing_task",
    "find_active_by_idempotency_key",
    "heartbeat_processing_task",
    "is_cancel_requested",
    "make_idempotency_key",
    "mark_cancelled",
    "recover_stale_processing_tasks",
    "record_external_run_id",
    "request_cancel",
    "worker_identity",
]
