"""外部 Agent 调用 AutoTeams REST API 的机器凭证。"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, JSON, String

from app.database import Base
from app.models.base import TimestampMixin


class AgentApiCredential(Base, TimestampMixin):
    """仅保存 API 密钥的不可逆 HMAC，明文只在创建响应中返回一次。"""

    __tablename__ = "agent_api_credentials"
    __table_args__ = (
        Index("ix_agent_api_credentials_enterprise_active", "enterprise_id", "is_active"),
    )

    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    # 仅用于列表展示和定位，不是认证秘密。
    key_prefix = Column(String(24), nullable=False, index=True)
    key_hash = Column(String(128), nullable=False, unique=True, index=True)
    scopes = Column(JSON, nullable=False, default=list)
    # 空数组表示可访问该企业的所有可调用 Agent；非空时为精确 allow-list。
    allowed_agent_ids = Column(JSON, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
