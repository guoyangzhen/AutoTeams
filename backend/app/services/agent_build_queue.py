"""耐久 LangGraph Agent 构建队列。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import worker_session_factory
from app.models.agent_build_task import AgentBuildTask
from app.models.enterprise import Enterprise
from app.services.compiler.job_queue import worker_identity
from app.services.queue_claim import call_queue_function, is_postgres
from app.utils.db_tenant_context import tenant_scope
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedAgentBuild:
    id: str
    enterprise_id: str
    name: str
    description: str
    folder_path: str
    require_approval: bool
    thread_id: str | None
    approval: dict | None


def _idempotency_key(enterprise_id: str, name: str, folder_path: str, require_approval: bool) -> str:
    normalized_path = folder_path.replace("\\", "/").rstrip("/")
    material = f"{enterprise_id}|{name.strip()}|{normalized_path}|{require_approval}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def enqueue_agent_build(
    db: AsyncSession,
    *,
    enterprise_id: str,
    owner_user_id: str,
    name: str,
    description: str,
    folder_path: str,
    require_approval: bool,
) -> tuple[AgentBuildTask, bool]:
    key = _idempotency_key(enterprise_id, name, folder_path, require_approval)
    # 与编译 Job 一样，以企业行锁串行化“查询活动任务 → 创建任务”这一临界区。
    await db.execute(
        select(Enterprise.id)
        .where(Enterprise.id == enterprise_id)
        .with_for_update()
    )
    result = await db.execute(
        select(AgentBuildTask)
        .where(
            AgentBuildTask.enterprise_id == enterprise_id,
            AgentBuildTask.idempotency_key == key,
            AgentBuildTask.status.in_(("queued", "running", "paused")),
        )
        .order_by(AgentBuildTask.created_at.desc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing, False
    task = AgentBuildTask(
        enterprise_id=enterprise_id,
        owner_user_id=owner_user_id,
        name=name,
        description=description,
        folder_path=folder_path,
        require_approval=require_approval,
        status="queued",
        idempotency_key=key,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task, True


async def resume_agent_build_task(
    db: AsyncSession,
    *,
    task: AgentBuildTask,
    approved: bool,
    comment: str | None,
) -> AgentBuildTask:
    if task.status != "paused" or not task.thread_id:
        raise ValueError("agent_build_task_not_paused")
    task.approval = {"approved": approved, "comment": comment or ""}
    task.status = "queued"
    task.error_message = None
    await db.commit()
    await db.refresh(task)
    return task


async def claim_next_agent_build(worker_id: str, lease_seconds: int) -> ClaimedAgentBuild | None:
    async with worker_session_factory() as db:
        if is_postgres(db):
            # 跨租户领取：只授予队列角色。
            rows = await call_queue_function(
                db,
                "SELECT * FROM public.app_claim_agent_build_task(:worker_id, :lease_seconds)",
                {"worker_id": worker_id, "lease_seconds": lease_seconds},
            )
            if not rows:
                return None
            row = rows[0]
            return ClaimedAgentBuild(
                id=str(row["id"]),
                enterprise_id=str(row["enterprise_id"]),
                name=str(row["name"]),
                description=str(row["description"] or ""),
                folder_path=str(row["folder_path"]),
                require_approval=bool(row["require_approval"]),
                thread_id=row["thread_id"],
                approval=row["approval"] if isinstance(row["approval"], dict) else None,
            )
        now = utcnow()
        result = await db.execute(
            select(AgentBuildTask)
            .where(
                or_(
                    AgentBuildTask.status == "queued",
                    and_(
                        AgentBuildTask.status == "running",
                        or_(AgentBuildTask.lease_until.is_(None), AgentBuildTask.lease_until < now),
                    ),
                )
            )
            .order_by(AgentBuildTask.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )

        task = result.scalar_one_or_none()
        if not task:
            return None
        if task.cancel_requested:
            task.status = "cancelled"
            task.completed_at = now
            task.lease_owner = None
            task.lease_until = None
            await db.commit()
            return None
        task.status = "running"
        task.started_at = task.started_at or now
        task.lease_owner = worker_id
        task.lease_until = now + timedelta(seconds=lease_seconds)
        task.heartbeat_at = now
        task.attempt = int(task.attempt or 0) + 1
        await db.commit()
        return ClaimedAgentBuild(
            id=str(task.id), enterprise_id=str(task.enterprise_id), name=task.name,
            description=task.description or "", folder_path=task.folder_path,
            require_approval=bool(task.require_approval), thread_id=task.thread_id,
            approval=task.approval if isinstance(task.approval, dict) else None,
        )


async def recover_stale_agent_build_tasks() -> int:
    """把租约已过期的 running 构建任务放回队列（跨租户，只由队列角色调用）。

    与处理/编译队列一致：崩溃残留的任务由 **Worker 进程**自己恢复，API 进程
    不再承担跨租户队列维护。
    """
    async with worker_session_factory() as db:
        if not is_postgres(db):
            return 0
        rows = await call_queue_function(
            db, "SELECT public.app_recover_agent_build_tasks() AS recovered", {}
        )
    recovered = int(rows[0]["recovered"]) if rows else 0
    if recovered:
        logger.warning("启动时回收了 %s 个租约过期的 Agent 构建任务", recovered)
    return recovered


async def heartbeat_agent_build(task_id: str, worker_id: str, lease_seconds: int) -> bool:
    """延长当前 Worker 的租约；丢失租约时让心跳循环停止。"""
    from sqlalchemy import update

    now = utcnow()
    async with worker_session_factory() as db:
        result = await db.execute(
            update(AgentBuildTask)
            .where(
                AgentBuildTask.id == task_id,
                AgentBuildTask.status == "running",
                AgentBuildTask.lease_owner == worker_id,
                AgentBuildTask.cancel_requested.is_(False),
            )
            .values(heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds))
        )
        await db.commit()
        return bool(result.rowcount)


async def _finish(task_id: str, worker_id: str, *, status: str, result: dict | None = None, error: str | None = None) -> None:
    async with worker_session_factory() as db:
        row = await db.execute(select(AgentBuildTask).where(AgentBuildTask.id == task_id).with_for_update())
        task = row.scalar_one_or_none()
        if not task or task.lease_owner != worker_id:
            return
        task.status = status
        task.result = result
        task.thread_id = (result or {}).get("thread_id") or task.thread_id
        task.current_step = (result or {}).get("current_step")
        task.error_message = error
        task.lease_owner = None
        task.lease_until = None
        task.heartbeat_at = utcnow()
        if status in ("completed", "failed", "cancelled"):
            task.completed_at = utcnow()
        await db.commit()


async def execute_claimed_agent_build(
    claimed: ClaimedAgentBuild, worker_id: str, lease_seconds: int = 90
) -> None:
    """调用现有 LangGraph 编排，并把 pause/terminal 结果投影为耐久任务状态。"""
    stop_heartbeat = asyncio.Event()

    async def _heartbeat_loop() -> None:
        interval = max(5.0, lease_seconds / 3)
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=interval)
            except asyncio.TimeoutError:
                if not await heartbeat_agent_build(claimed.id, worker_id, lease_seconds):
                    logger.warning("Agent 构建任务已失去租约: task=%s", claimed.id)
                    return

    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        if claimed.approval and claimed.thread_id:
            from app.services.agent_graph import resume_agent_build
            result = await resume_agent_build(
                worker_session_factory, claimed.thread_id, claimed.approval
            )
        else:
            from app.services.agent_graph import build_agent_via_graph
            result = await build_agent_via_graph(
                db_session_factory=worker_session_factory,
                enterprise_id=claimed.enterprise_id,
                name=claimed.name,
                description=claimed.description,
                folder_path=claimed.folder_path,
                require_approval=claimed.require_approval,
                thread_id=claimed.thread_id,
            )
        status = str(result.get("status", "completed"))
        if status == "paused":
            await _finish(claimed.id, worker_id, status="paused", result=result)
        elif status == "failed":
            await _finish(claimed.id, worker_id, status="failed", result=result, error="LangGraph 构建失败")
        else:
            await _finish(claimed.id, worker_id, status="completed", result=result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("耐久 Agent 构建失败: task=%s", claimed.id)
        await _finish(claimed.id, worker_id, status="failed", error=str(exc)[:500])
    finally:
        stop_heartbeat.set()
        await heartbeat_task


async def run_agent_build_worker_forever(
    *, poll_seconds: float = 2.0, lease_seconds: int = 90, stop_event: asyncio.Event | None = None
) -> None:
    from app.database import ensure_worker_database_ready

    # 生产 PostgreSQL 下没有队列连接就直接拒绝启动（API 角色无跨租户领取权限）。
    ensure_worker_database_ready()
    worker_id = worker_identity()
    logger.info("Agent 构建 Worker 启动: %s", worker_id)
    # 崩溃残留由 Worker 自己恢复，API 进程不承担跨租户队列维护。
    await recover_stale_agent_build_tasks()
    while not stop_event or not stop_event.is_set():
        try:
            claimed = await claim_next_agent_build(worker_id, lease_seconds)
            if claimed:
                # 领取到的任务属于哪个租户，后续写回就受哪个租户的策略约束。
                with tenant_scope(claimed.enterprise_id):
                    await execute_claimed_agent_build(claimed, worker_id, lease_seconds)
                continue
        except Exception:  # noqa: BLE001
            logger.exception("Agent 构建 Worker 异常")
        try:
            if stop_event:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            else:
                await asyncio.sleep(poll_seconds)
        except asyncio.TimeoutError:
            pass
