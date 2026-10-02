"""Celery 应用实例与 Redis 连接配置。

依据重构计划 §4.2.4「引入分布式任务队列」：
    agent_graph.py 用 asyncio.create_task() 在 API 进程内跑 CPU 密集的文档处理，
    _running_tasks（内存字典）在多 Worker 环境下失效。

本模块提供分布式任务基础设施：
- broker / result backend 均走 Redis，与应用缓存共用同一 Redis 实例但**使用不同 DB 序号**，
  避免队列消息与业务缓存键互相污染；
- 显式声明 task_routes，把文档处理与定时任务分流到独立队列，便于分别扩容与监控；
- beat_schedule 驱动定时任务，配置集中在 settings，测试可 monkeypatch 覆盖。

⚠️ 重要：Celery worker 是**同步**进程，而本仓的服务层（document_processor、
vector_store）全部是 async。任务函数内部统一用 ``asyncio.run()`` 做桥接，
不要在 worker 里混入 async def —— Celery 不会 await 它，会静默丢失返回值。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from celery import Celery
from celery.schedules import crontab

from app.config import settings

logger = logging.getLogger(__name__)

# 队列名：与 task_routes 对应，便于按队列独立扩容 worker
QUEUE_DOCUMENTS = "documents"
QUEUE_SCHEDULED = "scheduled"

# broker / result backend 相对应用缓存（REDIS_URL 的 db 0）各偏移一位，
# 避免 Celery 的队列/结果键与应用自己的缓存、限流键（见 utils/cache.py、rate_limit.py）冲突
BROKER_DB_OFFSET = 1
RESULT_DB_OFFSET = 2


def _shift_db(url: str, offset: int) -> str:
    """把 Redis URL 的 db 序号平移 offset，其余部分原样保留。

    URL 形态为 ``scheme://[user:pass@]host[:port][/db][?query]``：

    - ``redis://:pw@host:6379/0``      → ``redis://:pw@host:6379/1``
    - ``redis://host``（无 db 段）      → ``redis://host/1``
    - ``rediss://host:6379/0?a=b``     → ``rediss://host:6379/1?a=b``
    - ``unix:///var/run/redis.sock``    → 原样返回（末段是 socket 文件名，不是 db 号）
    - 非 redis scheme                   → 原样返回
    """
    if not url:
        return url
    scheme, sep, rest = url.partition("://")
    if not sep or scheme not in ("redis", "rediss", "unix"):
        return url

    # 先摘掉 query，剩下的才是 path
    path, qsep, query = rest.partition("?")
    if "/" in path:
        base, _, db_seg = path.rpartition("/")
    else:
        # `redis://host` 没有 db 段，Redis 隐式使用 db 0，故按 0 平移
        base, db_seg = path, "0"

    # db 段不是纯数字（unix socket 文件名等）说明这不是 DB 序号，不动它
    try:
        current_db = int(db_seg)
    except ValueError:
        return url

    shifted = f"{scheme}://{base}/{current_db + offset}"
    return f"{shifted}?{query}" if qsep else shifted


def build_config(redis_url: str | None = None) -> Dict[str, Any]:
    """构造 Celery 配置字典（可单测）。"""
    base = redis_url if redis_url is not None else settings.REDIS_URL
    return {
        "broker_url": _shift_db(base, BROKER_DB_OFFSET),
        "result_backend": _shift_db(base, RESULT_DB_OFFSET),
        # 结果过期：文档处理结果只需短期回查，过期后自动清理，避免 Redis 无限增长
        "result_expires": 3600,
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "timezone": "Asia/Shanghai",
        "enable_utc": True,
        # 本地开发用 eager 模式可同步执行任务，无需真实 broker；
        # 生产必须走真实 worker，勿在生产打开。
        "task_always_eager": False,
        "task_eager_propagates": True,
        "worker_prefetch_multiplier": 1,
        "task_acks_late": True,
        "task_routes": {
            "app.tasks.document_processing.*": {"queue": QUEUE_DOCUMENTS},
            "app.tasks.scheduled_metrics.*": {"queue": QUEUE_SCHEDULED},
        },
        "imports": (
            "app.tasks.document_processing",
            "app.tasks.scheduled_metrics",
        ),
    }


celery_app = Celery("autoteams")
celery_app.config_from_object(build_config())
celery_app.conf.update(
    beat_schedule={
        # 每日 02:00 汇总前一日经营日报
        "daily-report": {
            "task": "app.tasks.scheduled_metrics.generate_daily_report",
            "schedule": crontab(hour=2, minute=0),
        },
        # 每 15 分钟做一次平台健康度检查
        "health-check": {
            "task": "app.tasks.scheduled_metrics.check_platform_health",
            "schedule": crontab(minute="*/15"),
        },
    }
)

logger.info(
    "Celery 已配置: broker=%s result=%s queues=%s",
    celery_app.conf.broker_url,
    celery_app.conf.result_backend,
    [QUEUE_DOCUMENTS, QUEUE_SCHEDULED],
)
