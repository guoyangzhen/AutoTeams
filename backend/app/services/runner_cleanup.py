"""本地工具桥接（Local Runner）授权清理定时任务。

设计要点（沿用 process_monitor.py 模式）：
- 使用独立 AsyncIOScheduler 实例，避免修改 scheduler_service.py（D3 独占文件域）。
- 后台任务写 DB 用独立 session（async_session_factory()），与请求主 session 隔离，
  避免 SQLite WAL 下的 "database is locked"。
- 定时扫描 local_path_grants 表：
  1. 将「setup token 已过期」的 pending/offline 授权置为 revoked（清理僵尸授权）。
  2. 将「超过 LOCAL_PATH_EXPIRY_DAYS 未撤销」的 connected/offline 授权置为 revoked（授权生命周期到期）。
- 复用 settings.LOOP_SCHEDULER_ENABLED 开关：测试环境关闭调度器时一并跳过，
  避免在单测中引入真实定时器（与 scheduler_service 保持一致语义）。
"""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory
from app.models.local_path_grant import LocalPathGrant
from app.utils.metrics import errors_total

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()

_RUNNER_CLEANUP_JOB_ID = "cleanup_expired_local_grants"
# 每小时执行一次
_RUN_INTERVAL_MINUTES = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def cleanup_expired_local_grants() -> dict:
    """定时任务：清理 token 过期 / 授权生命周期到期的本地授权。

    规则：
    - 待连接/离线（pending/offline）且 setup_token_expires_at 已过 → revoked（token 失效）。
    - connected/offline 且 created_at 距今超过 LOCAL_PATH_EXPIRY_DAYS → revoked（授权到期）。

    返回统计 {"scanned": int, "revoked": int}。
    """
    now = _utcnow()
    revoked = 0
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(LocalPathGrant).where(
                    LocalPathGrant.status.in_(["pending", "offline", "connected"])
                )
            )
            grants = result.scalars().all()
            scanned = len(grants)

            for grant in grants:
                expired = False
                if grant.status in ("pending", "offline") and grant.setup_token_expires_at is not None:
                    expires = grant.setup_token_expires_at
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if expires < now:
                        expired = True
                # 授权生命周期到期（包含 connected 长期挂机）
                if not expired and grant.created_at is not None:
                    created = grant.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if now - created > timedelta(days=settings.LOCAL_PATH_EXPIRY_DAYS):
                        expired = True
                if expired:
                    grant.status = "revoked"
                    grant.claimed = True
                    grant.setup_token_hash = None
                    grant.setup_token_expires_at = None
                    revoked += 1

            if revoked > 0:
                await db.commit()

            if scanned > 0:
                logger.info(
                    f"RunnerCleanup: 扫描本地授权（扫描 {scanned}，撤销 {revoked}）"
                )
            return {"scanned": scanned, "revoked": revoked}
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"RunnerCleanup: 清理本地授权失败: {e}", exc_info=True)
        return {"scanned": 0, "revoked": 0, "error": str(e)}


def start_runner_cleanup() -> None:
    """启动本地授权清理调度器。

    幂等：未启用 / 已运行时安全跳过。启动失败仅记录日志，不阻断应用启动。
    """
    if not settings.LOOP_SCHEDULER_ENABLED:
        logger.info("RunnerCleanup: LOOP_SCHEDULER_ENABLED=false，跳过启动")
        return
    if _scheduler.running:
        logger.warning("RunnerCleanup: 调度器已在运行，跳过重复启动")
        return

    try:
        _scheduler.add_job(
            cleanup_expired_local_grants,
            IntervalTrigger(minutes=_RUN_INTERVAL_MINUTES, timezone="UTC"),
            id=_RUNNER_CLEANUP_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            # 首次启动后立即执行一次，清理重启前残留的过期授权
            next_run_time=_utcnow(),
        )
        _scheduler.start()
        logger.info(
            f"RunnerCleanup: 调度器已启动（每 {_RUN_INTERVAL_MINUTES} 分钟执行一次，"
            f"授权到期 {settings.LOCAL_PATH_EXPIRY_DAYS} 天）"
        )
    except Exception as e:
        errors_total.labels(module=__name__, exception_type=type(e).__name__).inc()
        logger.error(f"RunnerCleanup: 调度器启动失败（非致命）: {e}", exc_info=True)


def stop_runner_cleanup() -> None:
    """优雅关闭调度器。wait=False 避免阻塞关闭流程。"""
    if not _scheduler.running:
        return
    try:
        _scheduler.shutdown(wait=False)
        logger.info("RunnerCleanup: 调度器已关闭")
    except Exception as e:
        logger.error(f"RunnerCleanup: 调度器关闭失败: {e}", exc_info=True)
