"""独立五级编译 Worker 进程入口。

AUD-06：容器必须用包入口 `python -m scripts.run_compilation_worker` 启动。
直接 `python scripts/run_compilation_worker.py` 会把 `scripts/` 而非仓库根加入
`sys.path`，第 8 行的 `from app.config import settings` 立即抛
`ModuleNotFoundError: No module named 'app'`，worker 根本起不来。
"""
from __future__ import annotations

import asyncio
import logging
import signal

from app.config import settings
from app.services.compiler.job_queue import run_compilation_worker_forever, worker_identity
from app.services.worker_heartbeat import WorkerHeartbeatPublisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    stop_event = asyncio.Event()

    def _request_stop() -> None:
        logger.info("编译 Worker 收到停止信号，等待当前任务安全结束")
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
        role="compilation",
        worker_id=worker_identity(),
        interval_seconds=max(5.0, settings.COMPILATION_WORKER_LEASE_SECONDS / 4),
    )
    await heartbeat.start(_make_db_probe())

    try:
        await run_compilation_worker_forever(
            poll_seconds=settings.COMPILATION_WORKER_POLL_SECONDS,
            lease_seconds=settings.COMPILATION_WORKER_LEASE_SECONDS,
            stop_event=stop_event,
        )
    finally:
        heartbeat.stop()


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
            logger.warning("编译 Worker 数据库探测失败: %s", exc)
            return False

    return _probe


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("编译 Worker 已停止")
