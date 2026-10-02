"""可恢复的编译作业队列。

编译请求先持久化为 queued Job，由独立 Worker 用数据库租约领取并执行。该实现避免
HTTP 进程内 create_task 在重启、滚动发布或多副本下丢失任务，并以输入快照和幂等键
避免同一企业相同输入重复编译。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import worker_session_factory
from app.models.compiler import CompilationJob
from app.models.enterprise import Enterprise
from app.services.queue_claim import call_queue_function, is_postgres
from app.utils.db_tenant_context import tenant_scope
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
DEFAULT_LEASE_SECONDS = 90
DEFAULT_POLL_SECONDS = 2.0


@dataclass(frozen=True)
class ClaimedCompilationJob:
    """Worker 领取后不可变的任务输入快照。"""

    id: str
    enterprise_id: str
    folder_path: str
    interview_completion: float
    affected_stages: tuple[str, ...]
    attempt: int


def _create_pipeline(
    db: AsyncSession,
    enterprise_id: str,
    worker_id: str,
):
    """延迟导入 Pipeline，避免 compiler/cognition 的模块初始化形成循环依赖。"""
    from app.services.compiler.pipeline import CompilationPipeline

    return CompilationPipeline(db, enterprise_id, expected_worker_id=worker_id)


def worker_identity() -> str:
    """生成可审计的 Worker 标识。"""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def make_idempotency_key(
    enterprise_id: str,
    folder_path: str,
    trigger_source: str,
    interview_completion: float,
) -> str:
    """根据规范化输入构造稳定幂等键，不把用户原始路径直接暴露到索引中。"""
    normalized_path = str(Path(folder_path).expanduser()).replace("\\", "/").rstrip("/").lower()
    material = f"{enterprise_id}|{trigger_source}|{normalized_path}|{interview_completion:.6f}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def enqueue_compilation_job(
    db: AsyncSession,
    *,
    enterprise_id: str,
    folder_path: str,
    trigger_source: str = "manual",
    interview_completion: float = 0.0,
) -> tuple[CompilationJob, bool]:
    """提交一个可恢复的编译任务。

    返回 ``(job, created)``。同企业、同规范化输入已有 queued/running 作业时返回原
    任务，避免双击、重试或多 API 实例创建重复编译。
    """
    key = make_idempotency_key(enterprise_id, folder_path, trigger_source, interview_completion)
    # 以企业行作为低成本串行化边界。PostgreSQL 下 FOR UPDATE 使同一企业的并发
    # 提交在检查活跃幂等键前排队，避免“先查均不存在、后插入两条 Job”的竞态。
    # SQLite 开发环境会退化为事务级写锁，仍保持单进程开发语义。
    await db.execute(
        select(Enterprise.id)
        .where(Enterprise.id == enterprise_id)
        .with_for_update()
    )
    existing_result = await db.execute(
        select(CompilationJob)
        .where(
            CompilationJob.enterprise_id == enterprise_id,
            CompilationJob.idempotency_key == key,
            CompilationJob.status.in_(ACTIVE_STATUSES),
        )
        .order_by(CompilationJob.created_at.desc())
        .limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return existing, False

    job = CompilationJob(
        enterprise_id=enterprise_id,
        trigger_source=trigger_source,
        folder_path=folder_path,
        interview_completion=interview_completion,
        idempotency_key=key,
        status="queued",
        stage="information",
        progress=0.0,
    )
    db.add(job)
    # 先提交后返回，保证独立 Worker 可立即看见任务。
    await db.commit()
    await db.refresh(job)
    return job, True


async def recover_stale_compilation_jobs() -> dict[str, int]:
    """恢复没有有效租约的历史 running Job。

    已持久化输入快照的任务重新入队；旧版本遗留、没有 folder_path 的任务明确标记为
    failed，避免前端永久显示 running。该函数可在 API 启动期安全多次调用。
    """
    requeued = 0
    failed = 0
    async with worker_session_factory() as db:
        if is_postgres(db):
            # 跨租户恢复：只授予队列角色，API 运行角色会被数据库拒绝。
            rows = await call_queue_function(
                db, "SELECT * FROM public.app_recover_compilation_jobs()", {}
            )
            counts = (
                {"requeued": int(rows[0]["requeued"]), "failed": int(rows[0]["failed"])}
                if rows
                else {"requeued": 0, "failed": 0}
            )
            if counts["requeued"] or counts["failed"]:
                logger.warning(
                    "编译任务恢复完成: requeued=%s failed=%s",
                    counts["requeued"],
                    counts["failed"],
                )
            return counts
        now = utcnow()
        result = await db.execute(
            select(CompilationJob)
            .where(
                CompilationJob.status == "running",
                or_(
                    CompilationJob.lease_until.is_(None),
                    CompilationJob.lease_until < now,
                ),
            )
            .with_for_update()
        )
        for job in result.scalars().all():
            if job.folder_path:
                job.status = "queued"
                job.lease_owner = None
                job.lease_until = None
                job.heartbeat_at = now
                job.error_message = "上次 Worker 中断，任务已重新入队等待恢复"
                requeued += 1
            else:
                job.status = "failed"
                job.completed_at = now
                job.error_message = "历史编译任务缺少输入快照，无法安全恢复；请重新发起编译"
                failed += 1
        await db.commit()
    if requeued or failed:
        logger.warning("编译任务恢复完成: requeued=%s failed=%s", requeued, failed)
    return {"requeued": requeued, "failed": failed}


async def claim_next_compilation_job(
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> ClaimedCompilationJob | None:
    """原子领取一条 queued 或租约过期的运行中任务。

    PostgreSQL 使用 ``FOR UPDATE SKIP LOCKED`` 规避多 Worker 竞争；SQLite 开发模式下
    SQLAlchemy 会退化为事务级锁，仍可保持单进程开发的正确语义。
    """
    async with worker_session_factory() as db:
        if is_postgres(db):
            # 跨租户领取：只授予队列角色。
            rows = await call_queue_function(
                db,
                "SELECT * FROM public.app_claim_compilation_job(:worker_id, :lease_seconds)",
                {"worker_id": worker_id, "lease_seconds": lease_seconds},
            )
            if not rows:
                return None
            row = rows[0]
            return ClaimedCompilationJob(
                id=str(row["id"]),
                enterprise_id=str(row["enterprise_id"]),
                folder_path=str(row["folder_path"]),
                interview_completion=float(row["interview_completion"] or 0.0),
                affected_stages=tuple(
                    stage for stage in (row["affected_stages"] or "").split(",") if stage
                ),
                attempt=int(row["attempt"] or 1),
            )
        now = utcnow()
        query = (
            select(CompilationJob)
            .where(
                or_(
                    CompilationJob.status == "queued",
                    and_(
                        CompilationJob.status == "running",
                        or_(
                            CompilationJob.lease_until.is_(None),
                            CompilationJob.lease_until < now,
                        ),
                    ),
                )
            )
            .order_by(CompilationJob.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        result = await db.execute(query)
        job = result.scalar_one_or_none()
        if not job:
            return None

        if job.cancel_requested:
            job.status = "cancelled"
            job.completed_at = now
            job.lease_owner = None
            job.lease_until = None
            await db.commit()
            return None

        if not job.folder_path:
            job.status = "failed"
            job.error_message = "缺少持久化 folder_path，无法由 Worker 恢复编译"
            job.completed_at = now
            job.lease_owner = None
            job.lease_until = None
            await db.commit()
            return None

        previous_owner = job.lease_owner
        job.status = "running"
        job.started_at = job.started_at or now
        job.lease_owner = worker_id
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.heartbeat_at = now
        job.attempt = int(job.attempt or 0) + 1
        if previous_owner and previous_owner != worker_id:
            job.error_message = f"前 Worker 租约已过期，已由 {worker_id} 接管（第 {job.attempt} 次尝试）"
        await db.commit()
        return ClaimedCompilationJob(
            id=str(job.id),
            enterprise_id=str(job.enterprise_id),
            folder_path=str(job.folder_path),
            interview_completion=float(job.interview_completion or 0.0),
            affected_stages=tuple(stage for stage in (job.affected_stages or "").split(",") if stage),
            attempt=int(job.attempt or 1),
        )


async def heartbeat_compilation_job(
    job_id: str,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    """延长当前 Worker 的租约；失去租约时返回 False。"""
    now = utcnow()
    async with worker_session_factory() as db:
        result = await db.execute(
            update(CompilationJob)
            .where(
                CompilationJob.id == job_id,
                CompilationJob.status == "running",
                CompilationJob.lease_owner == worker_id,
                CompilationJob.cancel_requested.is_(False),
            )
            .values(
                heartbeat_at=now,
                lease_until=now + timedelta(seconds=lease_seconds),
            )
        )
        await db.commit()
        return bool(result.rowcount)


async def _release_terminal_lease(job_id: str, worker_id: str) -> None:
    """仅释放自己持有的终态任务租约，防止旧 Worker 覆盖接管者。"""
    async with worker_session_factory() as db:
        await db.execute(
            update(CompilationJob)
            .where(
                CompilationJob.id == job_id,
                CompilationJob.lease_owner == worker_id,
                CompilationJob.status.in_(TERMINAL_STATUSES),
            )
            .values(lease_owner=None, lease_until=None, heartbeat_at=utcnow())
        )
        await db.commit()


async def _mark_failed(job_id: str, worker_id: str, error: Exception) -> None:
    """仅在仍持有租约时标记失败，避免覆盖已被接管的任务结果。"""
    async with worker_session_factory() as db:
        result = await db.execute(
            select(CompilationJob)
            .where(CompilationJob.id == job_id)
            .with_for_update()
        )
        job = result.scalar_one_or_none()
        if not job or job.lease_owner != worker_id or job.status in TERMINAL_STATUSES:
            return
        job.status = "cancelled" if job.cancel_requested else "failed"
        job.error_message = str(error)[:500]
        job.completed_at = utcnow()
        job.lease_owner = None
        job.lease_until = None
        job.heartbeat_at = utcnow()
        await db.commit()


async def execute_claimed_compilation_job(
    claimed: ClaimedCompilationJob,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> None:
    """运行现有五级 Pipeline，并在运行期维持租约心跳。"""
    stop_heartbeat = asyncio.Event()

    async def _heartbeat_loop() -> None:
        interval = max(5.0, lease_seconds / 3)
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=interval)
            except asyncio.TimeoutError:
                if not await heartbeat_compilation_job(claimed.id, worker_id, lease_seconds):
                    logger.warning("编译任务已失去 Worker 租约: job=%s worker=%s", claimed.id, worker_id)
                    return

    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        async with worker_session_factory() as db:
            pipeline = _create_pipeline(db, claimed.enterprise_id, worker_id)
            if claimed.affected_stages:
                await pipeline.run_incremental(
                    claimed.id,
                    list(claimed.affected_stages),
                    claimed.interview_completion,
                    folder_path=claimed.folder_path,
                )
            else:
                await pipeline.run_full(
                    claimed.folder_path,
                    claimed.id,
                    claimed.interview_completion,
                )
    except Exception as exc:  # noqa: BLE001 - 需要将所有 Worker 失败持久化
        logger.exception("耐久编译任务失败: job=%s attempt=%s", claimed.id, claimed.attempt)
        await _mark_failed(claimed.id, worker_id, exc)
    finally:
        stop_heartbeat.set()
        await heartbeat_task
        await _release_terminal_lease(claimed.id, worker_id)


async def run_compilation_worker_forever(
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    """独立编译 Worker 主循环，可由容器进程或集成测试调用。"""
    from app.database import ensure_worker_database_ready

    # 生产 PostgreSQL 下没有队列连接就直接拒绝启动，而不是在领取循环里反复
    # 撞 42501：API 运行角色按设计没有跨租户领取权限。
    ensure_worker_database_ready()
    worker_id = worker_identity()
    logger.info("编译 Worker 启动: %s", worker_id)
    # 崩溃残留由 Worker 自己恢复，API 进程不承担跨租户队列维护。
    await recover_stale_compilation_jobs()
    while not stop_event or not stop_event.is_set():
        try:
            claimed = await claim_next_compilation_job(worker_id, lease_seconds)
            if claimed:
                # 领取到的任务属于哪个租户，后续写回就受哪个租户的策略约束。
                with tenant_scope(claimed.enterprise_id):
                    await execute_claimed_compilation_job(claimed, worker_id, lease_seconds)
                continue
        except Exception:  # noqa: BLE001 - 单个领取异常不能终止 Worker
            logger.exception("编译 Worker 领取或执行任务时发生未处理异常")
        try:
            if stop_event:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            else:
                await asyncio.sleep(poll_seconds)
        except asyncio.TimeoutError:
            continue
    logger.info("编译 Worker 已停止: %s", worker_id)
