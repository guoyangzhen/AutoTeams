"""渠道回调的两阶段租户引导（AUD-19 / AUD-08）。

问题
----
渠道回调在**验签之前**就必须读到账号配置（回调 token、AES key），而
``channel_accounts`` 是受 RLS 保护的租户表 —— 此时应用还不知道租户是谁。
历史补丁的做法是"凭 ``account_id`` 换 enterprise_id"，那等于把租户交给任何拿到
（甚至猜到）该 ID 的调用方：回调 URL 一旦泄漏，攻击者就能以任意租户身份注入
企业微信消息、完成身份绑定并调度数字员工。

两阶段契约
----------
**第一阶段（引导连接）**：用 ``DATABASE_BOOTSTRAP_URL`` 指向的角色调用
``app_get_channel_webhook_material``。该角色**没有任何表权限**，只返回指定账号
的验签材料，且**不返回 enterprise_id** —— 拿到材料不等于拿到租户。

**第二阶段（业务会话）**：验签/解密成功之后，用账号回调密钥的 sha256 摘要调用
``app_resolve_channel_tenant``。数据库比对 ``channel_accounts.webhook_token_hash``，
确认调用方确实持有该账号的回调密钥，才把该租户绑定到当前事务。业务会话之后的
每一次读写仍然受 RLS 约束。

两步都失败关闭：引导连接不可用 → 回调返回 503；密钥摘要不匹配 → 403。

非 PostgreSQL（开发用 SQLite，没有 RLS）走同一条业务会话读取，不另开连接，
这样测试的内存夹具依然可见。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.db_tenant_context import (
    TenantResolver,
    apply_tenant_context,
    bind_tenant_context,
    resolve_and_bind_tenant,
)

logger = logging.getLogger(__name__)

MATERIAL_SQL = text(
    "SELECT account_id, channel_type, is_active, encrypted_credentials "
    "FROM public.app_get_channel_webhook_material(:account_id)"
)


class ChannelBootstrapUnavailable(RuntimeError):
    """引导连接不可用：生产环境必须配置独立的受限引导角色。"""


@dataclass(frozen=True)
class ChannelWebhookMaterial:
    """验签所需的最小材料（不含任何租户信息）。"""

    account_id: str
    channel_type: str
    is_active: bool
    encrypted_credentials: dict[str, Any]


def secret_fingerprint(secret: str) -> str:
    """回调密钥的 sha256 十六进制摘要。

    落库的只是摘要：数据库据此判断"调用方确实持有密钥"，明文密钥仍然只存在于
    应用进程和外部平台配置里。
    """
    return hashlib.sha256(str(secret).encode("utf-8")).hexdigest()


async def load_channel_webhook_material(
    account_id: str, db: Optional[AsyncSession] = None
) -> Optional[ChannelWebhookMaterial]:
    """第一阶段：取指定账号的验签材料。账号不存在返回 ``None``。

    ``db`` 是调用方的业务会话：只在非 PostgreSQL（无 RLS）下用于读取，
    PostgreSQL 下**不会**退回它做跨租户读取。
    """
    if db is not None and db.get_bind().dialect.name != "postgresql":
        return await _read_material_locally(db, account_id)

    from app.database import bootstrap_session_factory, ensure_bootstrap_database_ready

    try:
        ensure_bootstrap_database_ready()
    except RuntimeError as exc:
        # 引导连接没配好属于"暂时不可用"，调用方据此返回 503；SQL 层的真实错误
        # 不在这里吞掉，仍然按异常抛出。
        raise ChannelBootstrapUnavailable(str(exc)) from exc
    if bootstrap_session_factory is None:  # pragma: no cover - DEBUG 下不会走到
        raise ChannelBootstrapUnavailable("渠道引导连接未配置")
    async with bootstrap_session_factory() as session:
        result = await session.execute(MATERIAL_SQL, {"account_id": account_id})
        row = result.mappings().one_or_none()
        if row is None:
            return None
        await session.rollback()
        return ChannelWebhookMaterial(
            account_id=str(row["account_id"]),
            channel_type=str(row["channel_type"]),
            is_active=bool(row["is_active"]),
            encrypted_credentials=dict(row["encrypted_credentials"] or {}),
        )


async def _read_material_locally(
    db: AsyncSession, account_id: str
) -> Optional[ChannelWebhookMaterial]:
    """开发/SQLite 路径：没有行级安全，用同一条业务会话读取即可。"""
    from app.models.channel_account import ChannelAccount

    account = (
        await db.execute(select(ChannelAccount).where(ChannelAccount.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        return None
    return ChannelWebhookMaterial(
        account_id=str(account.id),
        channel_type=str(account.channel_type),
        is_active=bool(account.is_active),
        encrypted_credentials=dict(account.encrypted_credentials or {}),
    )


async def bind_channel_tenant(
    db: AsyncSession, account_id: str, callback_secret: str
) -> Optional[str]:
    """第二阶段：验签通过后，用回调密钥摘要把该账号的租户绑定到当前事务。

    账号没有配置回调密钥、或摘要不匹配时返回 ``None``（调用方按 403 处理）。
    """
    if not callback_secret:
        return None
    if db.get_bind().dialect.name != "postgresql":
        return await _bind_tenant_locally(db, account_id)
    return await resolve_and_bind_tenant(
        db,
        TenantResolver.CHANNEL_ACCOUNT,
        {"account_id": account_id, "secret_hash": secret_fingerprint(callback_secret)},
    )


async def _bind_tenant_locally(db: AsyncSession, account_id: str) -> Optional[str]:
    """开发/SQLite 路径：账号行就在同一条会话里，直接绑定它的租户。"""
    from app.models.channel_account import ChannelAccount

    account = (
        await db.execute(select(ChannelAccount).where(ChannelAccount.id == account_id))
    ).scalar_one_or_none()
    if account is None or not account.is_active:
        return None
    bind_tenant_context(str(account.enterprise_id))
    if db.get_bind().dialect.name == "postgresql":
        await apply_tenant_context(db, str(account.enterprise_id))
    return str(account.enterprise_id)


__all__ = [
    "ChannelBootstrapUnavailable",
    "ChannelWebhookMaterial",
    "bind_channel_tenant",
    "load_channel_webhook_material",
    "secret_fingerprint",
]
