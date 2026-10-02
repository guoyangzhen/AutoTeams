"""文档处理 Worker 执行器（AUD-17）。

从 `app/api/process.py` 搬来的 LangGraph 构建执行逻辑，但**持有数据库租约**：

- 领取任务即写租约，运行中周期性续租；
- 续租失败（租约已被别人接管）立即停止，不再对数据库做写操作；
- 观察 `cancel_requested`（跨进程取消信号），协作式停止并标记 cancelled；
- 执行前登记 `external_run_id`：重投时据此判定"已经产生过副作用"，不重复执行。

崩溃/断电恢复：租约过期后 `recover_stale_processing_tasks` 把任务放回 pending，
下一个 worker 重新投递。若 `external_run_id` 已存在，说明上一次已经动过外部系统，
此时只补记结果、不再重放。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import chromadb.errors
import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.services.processing_queue import (
    DEFAULT_LEASE_SECONDS,
    ClaimedProcessingTask,
    claim_next_processing_task,
    complete_processing_task,
    fail_processing_task,
    heartbeat_processing_task,
    is_cancel_requested,
    mark_cancelled,
    record_external_run_id,
    worker_identity,
)
from app.utils.error_codes import ErrorCode
from app.utils.db_tenant_context import tenant_scope
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

#: 允许的业务异常：与原 API 层保持一致，避免把编程错误当成业务失败吞掉。
_EXPECTED_EXCEPTIONS = (
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    httpx.HTTPError,
    chromadb.errors.ChromaError,
    SQLAlchemyError,
    PermissionError,
)


class ProcessingCancelled(Exception):
    """观察到跨进程取消信号，协作式停止。"""


def _extract_stats(messages: list[str]) -> tuple[int, int]:
    """从 LangGraph 消息里提取 (文件数, 知识片段数)。"""
    total_files = 0
    knowledge_count = 0
    for msg in messages:
        if "扫描到" in msg and "个文件" in msg:
            try:
                total_files = int(msg.split("扫描到")[1].split("个文件")[0].strip())
            except (IndexError, ValueError):
                pass
        if "向量化" in msg and "片段" in msg:
            try:
                knowledge_count = int(msg.split("向量化")[1].split("片段")[0].strip())
            except (IndexError, ValueError):
                pass
    return total_files, knowledge_count


async def _run_claimed_task(
    claimed: ClaimedProcessingTask, worker_id: str, lease_seconds: int
) -> None:
    """执行一个已领取的任务，并维护租约与取消信号。"""
    from app.database import worker_session_factory
    from app.services.agent_graph import build_agent_via_graph

    task_id = claimed.task_id
    stop_heartbeat = asyncio.Event()
    cancelled = asyncio.Event()

    async def _heartbeat_loop() -> None:
        interval = max(5.0, lease_seconds / 3)
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            async with worker_session_factory() as db:
                renewed = await heartbeat_processing_task(
                    db, task_id, worker_id, lease_seconds
                )
                cancel_flag = await is_cancel_requested(db, task_id)
            if not renewed:
                # 租约已被别的 worker 接管：立刻停止，避免两个实例同时执行。
                logger.warning("处理任务 %s 租约已失效，停止执行", task_id)
                stop_heartbeat.set()
                return
            if cancel_flag:
                cancelled.set()
                stop_heartbeat.set()
                return

    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    run_id = claimed.run_config.get("run_id") or f"run-{uuid.uuid4().hex[:12]}"

    try:
        if claimed.attempt > 1 and claimed.run_config.get("external_run_id"):
            # 上一次已经产生过外部副作用：只补记结果，不重复执行。
            logger.warning(
                "处理任务 %s 重投但已存在外部执行标识，跳过重复执行", task_id
            )
            async with worker_session_factory() as db:
                await complete_processing_task(
                    db,
                    task_id,
                    worker_id,
                    progress=1.0,
                    message="已在上一次投递中完成（幂等恢复），未重复执行",
                    external_run_id=claimed.run_config["external_run_id"],
                )
            return

        # 副作用之前先登记执行标识：崩溃后重投能被识别为"已动过外部系统"。
        async with worker_session_factory() as db:
            await record_external_run_id(db, task_id, worker_id, run_id)

        result: dict[str, Any] = await build_agent_via_graph(
            db_session_factory=worker_session_factory,
            enterprise_id=claimed.enterprise_id,
            name=claimed.agent_name or "",
            description=claimed.agent_description or "",
            folder_path=claimed.folder_path,
            require_approval=False,
            model=claimed.run_config.get("model"),
            skills=claimed.run_config.get("skills"),
            index_strategy=claimed.run_config.get("index_strategy"),
        )

        if cancelled.is_set():
            raise ProcessingCancelled(task_id)

        total_files, knowledge_count = _extract_stats(result.get("messages", []))
        status = result.get("status", "completed")
        completed_at = datetime.now(timezone.utc)

        async with worker_session_factory() as db:
            if status == "completed":
                await complete_processing_task(
                    db,
                    task_id,
                    worker_id,
                    progress=1.0,
                    message=(
                        f"LangGraph 流水线处理完成！共扫描 {total_files} 个文件，"
                        f"生成 {knowledge_count} 条知识"
                    ),
                    agent_id=result.get("agent_id"),
                    total_files=total_files,
                    processed_files=total_files,
                    knowledge_count=knowledge_count,
                    external_run_id=run_id,
                )
            else:
                await fail_processing_task(
                    db,
                    task_id,
                    worker_id,
                    error=f"构建智能体失败: {status}",
                    error_entry={
                        "file": "unknown",
                        "error_code": ErrorCode.PROCESSING_FAILED,
                        "error_type": "GraphStatusError",
                        "timestamp": completed_at.isoformat(),
                    },
                )

    except ProcessingCancelled:
        logger.info("处理任务 %s 已取消", task_id)
        async with worker_session_factory() as db:
            await mark_cancelled(db, task_id, worker_id)

    except asyncio.CancelledError:
        # 进程级关闭：释放租约，让别的实例能重新投递，而不是永久卡在 processing。
        logger.info("处理任务 %s 因进程关闭而中断，释放租约", task_id)
        async with worker_session_factory() as db:
            await fail_processing_task(
                db,
                task_id,
                worker_id,
                error="worker 进程关闭，任务重新排队",
            )
        raise

    except _EXPECTED_EXCEPTIONS as exc:
        errors_total.labels(module=__name__, exception_type=type(exc).__name__).inc()
        logger.error("处理任务 %s 失败: %s", task_id, exc, exc_info=True)
        completed_at = datetime.now(timezone.utc)
        async with worker_session_factory() as db:
            await fail_processing_task(
                db,
                task_id,
                worker_id,
                error=str(exc),
                error_entry={
                    "file": "unknown",
                    "error_code": ErrorCode.PROCESSING_FAILED,
                    "error_type": type(exc).__name__,
                    "timestamp": completed_at.isoformat(),
                },
            )

    finally:
        stop_heartbeat.set()
        await heartbeat_task


async def run_processing_worker_forever(
    *,
    poll_seconds: float = 2.0,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """领取循环：一次只处理一个任务（文档构建本身是重 IO 任务）。"""
    from app.database import ensure_worker_database_ready, worker_session_factory
    from app.services.processing_queue import recover_stale_processing_tasks

    # 生产 PostgreSQL 下没有队列连接就直接拒绝启动（API 角色无跨租户领取权限）。
    ensure_worker_database_ready()

    worker_id = worker_identity()
    logger.info("文档处理 Worker 启动，worker_id=%s", worker_id)

    # 启动时只回收租约已过期的任务；其他实例持有的有效租约原样保留。
    async with worker_session_factory() as db:
        recovered = await recover_stale_processing_tasks(db)
    if recovered:
        logger.warning("启动时回收了 %s 个租约过期的处理任务", recovered)

    while stop_event is None or not stop_event.is_set():
        claimed: Optional[ClaimedProcessingTask] = None
        try:
            async with worker_session_factory() as db:
                claimed = await claim_next_processing_task(db, worker_id, lease_seconds)
            if claimed is None:
                await _sleep_or_stop(poll_seconds, stop_event)
                continue
            # 领取到的任务属于哪个租户，后续写回就受哪个租户的策略约束。
            with tenant_scope(claimed.enterprise_id):
                await _run_claimed_task(claimed, worker_id, lease_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 领取循环必须活着
            errors_total.labels(module=__name__, exception_type="WorkerLoop").inc()
            logger.exception("文档处理 Worker 领取循环异常")
            await _sleep_or_stop(poll_seconds, stop_event)

    logger.info("文档处理 Worker 已停止")


async def _sleep_or_stop(seconds: float, stop_event: Optional[asyncio.Event]) -> None:
    if stop_event is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return
