"""Worker 存活探针（AUD-29）。

容器 HEALTHCHECK 入口。两个 worker 进程不监听任何 HTTP 端口，因此
backend 镜像自带的 `curl http://localhost:8000/health` 对它们永远失败。
本脚本改为检查：

1. 该角色最近一次心跳是否在有效期内（确实有消费者在跑）；
2. 数据库是否可达（worker 的前提条件）。

用法::

    python -m scripts.worker_healthcheck compilation
    python -m scripts.worker_healthcheck agent_build

退出码 0 = 健康，非 0 = 不健康（Docker 语义）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.services.worker_heartbeat import DEFAULT_STALE_SECONDS, is_worker_alive

#: 允许的角色白名单，避免拼错角色名时永远判定"未找到心跳"。
ROLES = ("compilation", "agent_build", "processing")


async def _database_reachable() -> bool:

    from sqlalchemy import text

    from app.database import async_session_factory

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - 探针只需给出结论
        print(f"[worker-health] 数据库不可达: {exc}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 worker 是否有活跃消费者")
    parser.add_argument("role", choices=ROLES, help="worker 角色")
    parser.add_argument(
        "--stale-seconds",
        type=int,
        default=DEFAULT_STALE_SECONDS,
        help=f"心跳过期阈值（秒），默认 {DEFAULT_STALE_SECONDS}",
    )
    args = parser.parse_args(argv)

    alive, reason = is_worker_alive(args.role, stale_seconds=args.stale_seconds)
    if not alive:
        print(f"[worker-health] 不健康: {reason}", file=sys.stderr)
        return 1

    if not asyncio.run(_database_reachable()):
        print(f"[worker-health] 不健康: 数据库不可达（{reason}）", file=sys.stderr)
        return 1

    print(f"[worker-health] 健康: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
