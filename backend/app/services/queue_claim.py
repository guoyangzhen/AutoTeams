"""跨租户队列领取/恢复的数据库入口（AUD-19）。

领取与恢复天然跨租户：函数不接收 ``enterprise_id``，而是从全队列里挑一条。
这类能力只授予队列角色（迁移 ``d5e6f7a8b9c0``），API 运行角色调用会被数据库
以 42501 拒绝。

本模块只做两件事：把调用送到数据库函数，以及把"权限不足"翻译成可执行的运维
提示。它**不做**任何租户判断 —— 领取之后的租户作用域由调用方用
``tenant_scope(claimed.enterprise_id)`` 绑定。
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

WORKER_CONNECTION_HINT = (
    "队列领取需要独立的队列 Worker 连接：生产环境请配置 DATABASE_WORKER_URL 与 "
    "DATABASE_WORKER_ROLE（角色 autoteams_worker）。API 运行角色按设计没有跨租户"
    "领取/恢复权限，数据库会直接拒绝。"
)


def is_postgres(db: AsyncSession) -> bool:
    bind = db.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


async def call_queue_function(
    db: AsyncSession,
    sql: str,
    params: Mapping[str, Any],
) -> Sequence[Mapping[str, Any]]:
    """执行一个队列函数并返回结果行（无结果时返回空序列）。"""
    try:
        result = await db.execute(text(sql), params)
    except ProgrammingError as exc:  # 42501 / 42883
        message = str(exc)
        if "app_claim_" in message or "app_recover_" in message:
            raise RuntimeError(WORKER_CONNECTION_HINT) from exc
        raise
    await db.commit()
    return result.mappings().all()


async def call_queue_scalar(
    db: AsyncSession,
    sql: str,
    params: Mapping[str, Any],
) -> Optional[Any]:
    """执行一个返回单值的队列函数。"""
    rows = await call_queue_function(db, sql, params)
    if not rows:
        return None
    return list(rows[0].values())[0]


__all__ = [
    "WORKER_CONNECTION_HINT",
    "call_queue_function",
    "call_queue_scalar",
    "is_postgres",
]
