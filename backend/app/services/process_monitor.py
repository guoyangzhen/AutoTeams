"""M15: 处理任务自动取消定时任务。

设计要点（沿用 scheduler_service.py 模式，D3 独占文件域）：
- 使用独立的 AsyncIOScheduler 实例，避免修改 scheduler_service.py（D3 文件域限制）。
  多个 AsyncIOScheduler 实例可在同一事件循环上共存，各自管理自身作业。
- 后台任务写 DB 必须用独立 session（async_session_factory()），
  与请求主 session 隔离，避免 SQLite WAL 下的 "database is locked"。
- 定时（每小时）扫描 processing_tasks 表，将超过 _STALE_TASK_THRESHOLD_SECONDS
  仍未完成的 pending/scanning/processing/vectorizing 任务标记为 failed，
  避免僵尸任务永久占用资源与误导前端进度。
- 复用 settings.LOOP_SCHEDULER_ENABLED 开关：测试环境关闭调度器时一并跳过，
  避免在单测中引入真实定时器（与 scheduler_service 保持一致语义）。
"""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, func, and_

from app.config import settings
from app.database import async_session_factory
from app.models.processing_task import ProcessingTask
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

# 模块级独立调度器实例：构造不需要事件循环，start() 才需要（在 lifespan 内调用）
_scheduler = AsyncIOScheduler()

# 作业 ID 与调度参数
_PROCESS_MONITOR_JOB_ID = "cancel_stale_processing_tasks"
# 每小时执行一次
_RUN_INTERVAL_MINUTES = 60
# 超过 1 小时未完成的任务视为僵尸任务
_STALE_TASK_THRESHOLD_SECONDS = 60 * 60
# 需要清理的中间态状态集合（不含 completed/failed/cancelled 等终态）
_INCOMPLETE_STATUSES = ("pending", "scanning", "processing", "vectorizing")


async def cancel_stale_processing_tasks() -> dict:
    """定时任务：取消超过阈值仍未完成的处理任务。

    判定条件：
    - status IN ("pending", "scanning", "processing", "vectorizing")
    - COALESCE(started_at, created_at) < now - 1h
      （started_at 为空时回退到 created_at，兼容 pending 未启动的任务）

    处理方式：
    - 将 status 置为 "failed"
    - 在 error_log 追加一条超时取消记录（保留历史错误信息）
    - message 更新为超时提示

    返回统计字典 {"scanned": int, "cancelled": int}，便于日志排查。
    """
    # 迁移 2026_08_09_0900 已将所有时间戳列改为 TIMESTAMP WITH TIME ZONE（aware），
    # 应用代码统一使用 aware UTC 时间，此处与列类型一致（不再需要 naive 转换）。
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=_STALE_TASK_THRESHOLD_SECONDS
    )
    # COALESCE(started_at, created_at)：pending 任务 started_at 可能为空
    effective_start = func.coalesce(ProcessingTask.started_at, ProcessingTask.created_at)

    logger.info(
        f"ProcessMonitor: 开始扫描僵尸任务（阈值 {_STALE_TASK_THRESHOLD_SECONDS}s，"
        f"截止时间 {cutoff.isoformat()}）"
    )

    try:
        async with async_session_factory() as db:
            # 先查询待取消任务列表（用于日志审计）
            result = await db.execute(
                select(ProcessingTask.id, ProcessingTask.status, effective_start.label("eff_start"))
                .where(
                    and_(
                        ProcessingTask.status.in_(_INCOMPLETE_STATUSES),
                        effective_start < cutoff,
                    )
                )
            )
            stale_rows = result.fetchall()
            scanned = len(stale_rows)

            if scanned == 0:
                logger.info("ProcessMonitor: 无僵尸任务，跳过")
                return {"scanned": 0, "cancelled": 0}

            stale_ids = [str(row[0]) for row in stale_rows]

            # 批量更新：status=failed + 追加 error_log + 更新 message
            # error_log 为 JSON 数组，使用 JSON 配合 append 需数据库侧支持，
            # 这里改为逐条更新以保证 SQLite/PostgreSQL 兼容性
            cancelled = 0
            for task_id in stale_ids:
                try:
                    task_result = await db.execute(
                        select(ProcessingTask).where(ProcessingTask.id == task_id)
                    )
                    task = task_result.scalar_one_or_none()
                    if task is None:
                        continue

                    old_status = task.status
                    task.status = "failed"
                    task.message = "任务超时未完成，已被系统自动取消"
                    # 追加错误日志条目（保留既有错误信息）
                    existing_log = list(task.error_log or [])
                    existing_log.append({
                        "error": "任务超过 1 小时未完成，自动取消",
                        "error_type": "StaleTaskTimeout",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "previous_status": old_status,
                    })
                    task.error_log = existing_log
                    if task.completed_at is None:
                        task.completed_at = datetime.now(timezone.utc)
                    cancelled += 1
                except Exception as e:
                    errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
                    logger.error(
                        f"ProcessMonitor: 取消任务 {task_id} 失败: {e}",
                        exc_info=True,
                    )

            if cancelled > 0:
                await db.commit()

            logger.info(
                f"ProcessMonitor: 扫描完成（扫描 {scanned}，取消 {cancelled}）"
            )
            return {"scanned": scanned, "cancelled": cancelled}
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"ProcessMonitor: 扫描僵尸任务失败: {e}", exc_info=True)
        return {"scanned": 0, "cancelled": 0, "error": str(e)}


def start_process_monitor() -> None:
    """启动处理任务监控调度器。

    幂等：未启用 / 已运行时安全跳过。启动失败仅记录日志，不阻断应用启动。
    复用 LOOP_SCHEDULER_ENABLED 开关，与 scheduler_service 保持一致的启用语义。
    """
    if not settings.LOOP_SCHEDULER_ENABLED:
        logger.info("ProcessMonitor: LOOP_SCHEDULER_ENABLED=false，跳过启动")
        return
    if _scheduler.running:
        logger.warning("ProcessMonitor: 调度器已在运行，跳过重复启动")
        return

    try:
        _scheduler.add_job(
            cancel_stale_processing_tasks,
            IntervalTrigger(minutes=_RUN_INTERVAL_MINUTES, timezone="UTC"),
            id=_PROCESS_MONITOR_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            # 首次启动后立即执行一次，清理重启前残留的僵尸任务
            next_run_time=datetime.now(timezone.utc),
        )
        _scheduler.start()
        logger.info(
            f"ProcessMonitor: 调度器已启动（每 {_RUN_INTERVAL_MINUTES} 分钟执行一次，"
            f"超时阈值 {_STALE_TASK_THRESHOLD_SECONDS}s）"
        )
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"ProcessMonitor: 调度器启动失败（非致命）: {e}", exc_info=True)


def stop_process_monitor() -> None:
    """优雅关闭调度器。wait=False 避免阻塞关闭流程。"""
    if not _scheduler.running:
        return
    try:
        _scheduler.shutdown(wait=False)
        logger.info("ProcessMonitor: 调度器已关闭")
    except Exception as e:
        logger.error(f"ProcessMonitor: 调度器关闭失败: {e}", exc_info=True)
