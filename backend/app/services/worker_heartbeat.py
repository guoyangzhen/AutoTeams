"""Worker 存活心跳（AUD-29）。

两个 worker 容器继承 backend 镜像的 `HEALTHCHECK`，而它们**不监听 HTTP 8000**，
于是 `curl http://localhost:8000/health` 永远失败：容器要么一直 unhealthy，
要么被误判为"业务未就绪"。API 的 `/health` 也只做 `SELECT 1`，无法证明队列
真的有人消费。

本模块提供与进程同生共死的落盘心跳：

* worker 主循环周期性原子写入 `<heartbeat_dir>/<role>.json`，含
  `worker_id` / `role` / `last_beat_at` / `db_ok`；
* `scripts/worker_healthcheck.py` 读取心跳，判定是否"仍在消费"，
  并顺带探测数据库连通性。

心跳文件放在 `LANGGRAPH_CHECKPOINT_PATH` 所在的数据卷（Compose 已挂载
`/app/data`），因此容器重启/重建不会留下永久"健康"的假象：文件随卷保留，
但 `last_beat_at` 会随进程停止而变旧。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: 心跳文件所在目录。默认取 checkpoint 目录的父目录，保证与数据卷同盘。
HEARTBEAT_DIR_ENV = "WORKER_HEARTBEAT_DIR"
DEFAULT_HEARTBEAT_DIR = str(Path(os.path.abspath("./data")))

#: 超过该秒数没有心跳即视为"没有消费者"。
DEFAULT_STALE_SECONDS = 90


def heartbeat_dir() -> Path:
    """返回心跳目录，确保存在。"""
    path = Path(os.environ.get(HEARTBEAT_DIR_ENV) or DEFAULT_HEARTBEAT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def heartbeat_path(role: str) -> Path:
    return heartbeat_dir() / f"worker_{role}.json"


@dataclass
class WorkerHeartbeat:
    role: str
    worker_id: str
    last_beat_at: str
    db_ok: bool
    pid: int

    def age_seconds(self, now: Optional[datetime] = None) -> float:
        try:
            last = datetime.fromisoformat(self.last_beat_at)
        except ValueError:
            return float("inf")
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        return (reference - last).total_seconds()

    def to_dict(self) -> dict:
        return asdict(self)


class WorkerHeartbeatPublisher:
    """周期性写心跳；上下文管理器退出时停止写入。"""

    def __init__(self, role: str, worker_id: str, interval_seconds: float = 15.0) -> None:
        self.role = role
        self.worker_id = worker_id
        self.interval_seconds = max(1.0, interval_seconds)
        self._db_ok = False

    async def start(self, db_probe) -> None:
        """启动后台心跳任务。

        :param db_probe: 无参异步可调用，返回 True 表示数据库可用。
        """
        import asyncio

        self._task = asyncio.create_task(self._loop(db_probe))

    async def _loop(self, db_probe) -> None:
        import asyncio

        while True:
            try:
                self._db_ok = bool(await db_probe())
            except Exception:  # noqa: BLE001 - 心跳自身不能因探测失败而中断
                self._db_ok = False
            self._write()
            await asyncio.sleep(self.interval_seconds)

    def _write(self) -> None:
        payload = WorkerHeartbeat(
            role=self.role,
            worker_id=self.worker_id,
            last_beat_at=datetime.now(timezone.utc).isoformat(),
            db_ok=self._db_ok,
            pid=os.getpid(),
        )
        target = heartbeat_path(self.role)
        # 原子替换：健康检查永远读到完整 JSON，不会读到写了一半的文件。
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload.to_dict(), handle, ensure_ascii=False)
            os.replace(tmp, target)
        except Exception:  # noqa: BLE001 - 心跳失败不应中断 worker 主循环
            logger.warning("写入 worker 心跳失败: %s", target, exc_info=True)
            Path(tmp).unlink(missing_ok=True)

    def stop(self) -> None:
        task = getattr(self, "_task", None)
        if task is not None and not task.done():
            task.cancel()


def read_heartbeat(role: str) -> Optional[WorkerHeartbeat]:
    """读取指定角色最近一次心跳；不存在或损坏时返回 None。"""
    path = heartbeat_path(role)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return WorkerHeartbeat(**raw)
    except (OSError, ValueError, TypeError):
        logger.warning("worker 心跳文件损坏: %s", path)
        return None


def is_worker_alive(role: str, stale_seconds: int = DEFAULT_STALE_SECONDS) -> tuple[bool, str]:
    """判断 worker 是否仍在消费。

    :return: ``(是否健康, 人类可读的原因)``
    """
    beat = read_heartbeat(role)
    if beat is None:
        return False, f"未找到 {role} worker 心跳文件"
    age = beat.age_seconds()
    if age > stale_seconds:
        return False, f"{role} worker 心跳已过期 {age:.0f}s（阈值 {stale_seconds}s）"
    if not beat.db_ok:
        return False, f"{role} worker 无法连接数据库"
    return True, f"{role} worker 正常（{age:.0f}s 前心跳，worker_id={beat.worker_id}）"


__all__ = [
    "DEFAULT_STALE_SECONDS",
    "HEARTBEAT_DIR_ENV",
    "WorkerHeartbeat",
    "WorkerHeartbeatPublisher",
    "heartbeat_dir",
    "heartbeat_path",
    "is_worker_alive",
    "read_heartbeat",
]
