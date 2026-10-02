"""文档处理 Worker 进程入口（AUD-17）。

执行路径从请求进程搬到独立 worker：API 只入队，取消信号走数据库，
任务状态由租约持有者维护。这样 API 重启、横向扩容、请求落到不同实例
都不会丢失或误伤执行中的任务。

启动方式必须是包入口 ``python -m scripts.run_processing_worker``：
直接 ``python scripts/run_processing_worker.py`` 时 ``sys.path[0]`` 是
``scripts/``，``from app...`` 立刻 ModuleNotFoundError（AUD-06）。
"""
from __future__ import annotations

import asyncio
import logging
import signal

from app.config import settings
from app.services.processing_queue import worker_identity
from app.services.worker_heartbeat import WorkerHeartbeatPublisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _make_db_probe():
    """构造数据库连通性探测协程（心跳里记录 db_ok）。"""

    async def _probe() -> bool:
        from sqlalchemy import text

        from app.database import async_session_factory

        try:
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # noqa: BLE001 - 探测失败只影响心跳标记
            logger.warning("文档处理 Worker 数据库探测失败: %s", exc)
            return False

    return _probe


async def main() -> None:
    from app.services.processing_worker import run_processing_worker_forever

    stop_event = asyncio.Event()

    def _request_stop() -> None:
        logger.info("文档处理 Worker 收到停止信号，等待当前任务安全结束")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            # Windows 事件循环不支持 add_signal_handler；KeyboardInterrupt 仍会退出。
            pass

    # AUD-29：worker 不监听 HTTP 端口，容器 HEALTHCHECK 改查这里写出的心跳。
    heartbeat = WorkerHeartbeatPublisher(
        role="processing",
        worker_id=worker_identity(),
        interval_seconds=max(5.0, settings.COMPILATION_WORKER_LEASE_SECONDS / 4),
    )
    await heartbeat.start(_make_db_probe())

    try:
        await run_processing_worker_forever(
            poll_seconds=settings.COMPILATION_WORKER_POLL_SECONDS,
            lease_seconds=settings.COMPILATION_WORKER_LEASE_SECONDS,
            stop_event=stop_event,
        )
    finally:
        heartbeat.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("文档处理 Worker 已停止")
