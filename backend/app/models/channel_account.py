"""AutoTeams 4.0 全渠道接入网关数据模型。

包含渠道账号配置（ChannelAccount）与跨渠道身份映射（ChannelIdentity）。
"""
from __future__ import annotations

import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Text

from app.database import Base
from app.utils.time import utcnow


class ChannelAccount(Base):
    """企业渠道接入账号表（支持企业微信、飞书、钉钉等多通道）。"""
    __tablename__ = "channel_accounts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_type = Column(String(32), nullable=False, index=True)  # wecom_bot, feishu_app, dingtalk_bot, generic_webhook
    name = Column(String(64), nullable=False)
    description = Column(Text, nullable=True)

    # 渠道认证凭据（落库由 credential_crypto 强加密存储）
    encrypted_credentials = Column(JSON, nullable=False, default=dict)

    # 回调密钥的 sha256 十六进制摘要。渠道回调必须在解析租户之前先验签，而
    # "凭 account_id 就能换到 enterprise_id"等于把租户交给任何拿到该 ID 的人。
    # 验签通过后由业务会话带着这个摘要调用 SECURITY DEFINER 解析函数换租户，
    # 数据库据此确认调用方确实持有该账号的回调密钥。为空表示尚未配置回调密钥。
    webhook_token_hash = Column(String(64), nullable=True)

    # 多员工挂载与调度配置
    mounted_profile_ids = Column(JSON, nullable=False, default=list)  # 挂载的数字员工 profile_id 列表
    default_profile_id = Column(String(36), nullable=True)             # 默认承接员工

    # 运行状态
    status = Column(String(32), nullable=False, default="configured")  # configured, connected, error, disabled
    status_message = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<ChannelAccount id={self.id} name={self.name} type={self.channel_type} status={self.status}>"


class ChannelIdentity(Base):
    """跨渠道外部用户与内部账号/数字员工映射身份表。"""
    __tablename__ = "channel_identities"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_type = Column(String(32), nullable=False, index=True)
    external_user_id = Column(String(128), nullable=False, index=True)  # 企微 userid, 飞书 open_id 等
    external_user_name = Column(String(128), nullable=True)

    # 内部绑定账号（可选绑定至真实系统用户）
    internal_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # 一次性绑定验证凭据 (/绑定 123456)
    bind_token = Column(String(32), nullable=True, index=True)
    bind_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    is_bound = Column(Boolean, nullable=False, default=False)

    # 当前会话粘性：当前锁定的数字员工与 SOP 保护窗
    active_profile_id = Column(String(36), nullable=True)
    sop_window_locked_until = Column(DateTime(timezone=True), nullable=True)
    active_flow_id = Column(String(64), nullable=True)

    extra_metadata = Column("metadata", JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<ChannelIdentity ext={self.external_user_id} channel={self.channel_type} bound={self.is_bound}>"
