"""渠道身份绑定码服务（AUD-08）。

历史实现（`OmnichannelGateway.generate_bind_token` + `/绑定` 分支）只检查
绑定码长度是否为 6：任意外部消息 `/绑定 ABCDEF` 都会把
`ChannelIdentity.is_bound` 置为 True，而 `internal_user_id` 仍是 NULL。

本模块把绑定码变成真正的一次性凭据：

* 只存 SHA-256 摘要，明文仅在生成响应中返回一次；
* 台账记录发起绑定的内部用户 + 企业 + 可用渠道；
* 短 TTL（默认 10 分钟）+ 一次性消费（`consumed_at`）；
* 消费必须与渠道身份所属企业一致，跨企业消费失败；
* 同一用户同时只允许存在一个未消费的绑定码。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel_bind_token import ChannelBindToken
from app.models.channel_account import ChannelIdentity
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

#: 绑定码字符集：去掉了易混淆的 0/O/1/I。
BIND_TOKEN_ALPHABET = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "O0I1")
BIND_TOKEN_LENGTH = 8
DEFAULT_BIND_TOKEN_TTL_MINUTES = 10


def as_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    """把数据库读出的时间统一成带时区的 UTC。

    PostgreSQL 的 `TIMESTAMPTZ` 会返回带时区的时间，但 SQLite（本地开发路径）
    读回来的是 naive datetime；直接与 `utcnow()` 比较会抛
    "can't compare offset-naive and offset-aware datetimes"。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)



class BindTokenError(Exception):
    """绑定码签发/消费失败。"""

    def __init__(self, reason: str, *, status_code: int = 400) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class IssuedBindToken:
    """签发结果：明文只在这里出现一次。"""

    token: str
    expires_at: datetime


def hash_bind_token(token: str) -> str:
    """绑定码摘要。统一大写后取 SHA-256 十六进制。"""
    return hashlib.sha256(token.strip().upper().encode("utf-8")).hexdigest()


def normalize_bind_token(raw: str) -> str:
    """把用户输入的绑定码归一化。"""
    return raw.strip().upper()


def _generate_token() -> str:
    return "".join(secrets.choice(BIND_TOKEN_ALPHABET) for _ in range(BIND_TOKEN_LENGTH))


async def issue_bind_token(
    db: AsyncSession,
    *,
    enterprise_id: str,
    internal_user_id: str,
    channel_type: Optional[str] = None,
    ttl_minutes: int = DEFAULT_BIND_TOKEN_TTL_MINUTES,
) -> IssuedBindToken:
    """为已登录用户签发一次性绑定码，并作废该用户此前未消费的绑定码。"""
    if not enterprise_id:
        raise BindTokenError("当前用户未归属任何企业，无法签发渠道绑定码", status_code=403)

    now = utcnow()
    # 同一用户此前签发的未消费码全部作废，保证"当前有效码只有一个"。
    await db.execute(
        update(ChannelBindToken)
        .where(
            ChannelBindToken.enterprise_id == enterprise_id,
            ChannelBindToken.internal_user_id == internal_user_id,
            ChannelBindToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    expires_at = now + timedelta(minutes=ttl_minutes)
    # 极小概率碰撞时重试；摘要列有唯一索引兜底。
    for _ in range(5):
        token = _generate_token()
        record = ChannelBindToken(
            id=str(uuid.uuid4()),
            enterprise_id=enterprise_id,
            internal_user_id=internal_user_id,
            channel_type=channel_type,
            token_hash=hash_bind_token(token),
            expires_at=expires_at,
        )
        db.add(record)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            continue
        return IssuedBindToken(token=token, expires_at=expires_at)

    await db.rollback()
    raise BindTokenError("绑定码生成失败，请稍后重试", status_code=500)


async def consume_bind_token(
    db: AsyncSession,
    *,
    raw_token: str,
    identity: ChannelIdentity,
) -> ChannelBindToken:
    """消费绑定码并把内部身份写入渠道身份。

    :raises BindTokenError: 码不存在 / 已过期 / 已使用 / 不属于该企业或渠道。
    """
    normalized = normalize_bind_token(raw_token)
    if not normalized:
        raise BindTokenError("请提供一次性绑定验证码。用法：`/绑定 <码>`")

    stmt = select(ChannelBindToken).where(ChannelBindToken.token_hash == hash_bind_token(normalized))
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None:
        # 不区分"不存在"与"已使用"，避免把有效码的存在性泄露给外部。
        raise BindTokenError("绑定码无效或已失效，请重新生成。", status_code=403)

    if record.consumed_at is not None:
        raise BindTokenError("绑定码已被使用，请重新生成。", status_code=403)
    if record.expires_at is not None and as_aware_utc(record.expires_at) <= utcnow():
        raise BindTokenError("绑定码已过期，请重新生成。", status_code=403)
    if record.enterprise_id != identity.enterprise_id:
        raise BindTokenError("绑定码不属于当前企业。", status_code=403)
    if record.channel_type and record.channel_type != identity.channel_type:
        raise BindTokenError("绑定码不适用于当前渠道。", status_code=403)

    record.consumed_at = utcnow()
    record.consumed_by_identity_id = identity.id

    identity.internal_user_id = record.internal_user_id
    identity.is_bound = True
    identity.bind_token = None
    identity.bind_token_expires_at = None

    await db.commit()
    return record


def token_ttl_seconds(ttl_minutes: int = DEFAULT_BIND_TOKEN_TTL_MINUTES) -> int:
    return ttl_minutes * 60


def bind_token_expiry(now: Optional[datetime] = None) -> datetime:
    reference = now or datetime.now(timezone.utc)
    return reference + timedelta(minutes=DEFAULT_BIND_TOKEN_TTL_MINUTES)


__all__ = [
    "BIND_TOKEN_ALPHABET",
    "BIND_TOKEN_LENGTH",
    "BindTokenError",
    "DEFAULT_BIND_TOKEN_TTL_MINUTES",
    "IssuedBindToken",
    "bind_token_expiry",
    "consume_bind_token",
    "hash_bind_token",
    "issue_bind_token",
    "normalize_bind_token",
    "token_ttl_seconds",
]
