"""渠道身份绑定码台账（AUD-08）。

`ChannelIdentity.bind_token` 只能表达"某个外部身份当前处于绑定流程中"，
无法回答"这个码是谁申请的、给哪个企业用、什么时候过期、用过没有"。
历史实现因此让任意 6 位字符串都能把 `is_bound` 置真，而
`internal_user_id` 仍是 NULL（AUD-08 复现）。

本表是绑定码的唯一真源：
* 只保存 SHA-256 摘要，数据库泄露也无法反推出可用绑定码；
* 记录发起绑定的内部用户与所属企业，绑定成功后写入
  `ChannelIdentity.internal_user_id`；
* 短 TTL + 一次性消费（`consumed_at` 非空即不可再用）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from app.database import Base
from app.utils.time import utcnow


class ChannelBindToken(Base):
    """一次性渠道绑定码台账。"""

    __tablename__ = "channel_bind_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    internal_user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 限定可用渠道；为 NULL 表示该企业在任意已配置渠道上都可用
    channel_type = Column(String(32), nullable=True)
    #: SHA-256(小写绑定码) 十六进制摘要；明文永不落库
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    consumed_by_identity_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_channel_bind_tokens_active", "enterprise_id", "expires_at"),
    )

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    def __repr__(self) -> str:
        return (
            f"<ChannelBindToken enterprise={self.enterprise_id} "
            f"user={self.internal_user_id} consumed={self.is_consumed}>"
        )

__all__ = ["ChannelBindToken"]

