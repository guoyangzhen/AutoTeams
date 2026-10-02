"""P2-2: 邀请记录表。

每次生成邀请都创建一条 Invitation 记录，token 全局唯一。
用于审计与邀请管理（取消、查询历史等）。
"""
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import TimestampMixin


class Invitation(Base, TimestampMixin):
    __tablename__ = "invitations"

    enterprise_id = Column(String(36), ForeignKey("enterprises.id"), nullable=False, index=True)
    invited_by_user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    # 可选：指定邮箱邀请
    email = Column(String, nullable=True)
    # pending / used / expired / cancelled
    status = Column(String(32), default="pending", nullable=False)
    used_by_user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # 关系
    enterprise = relationship("Enterprise")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])
    used_by = relationship("User", foreign_keys=[used_by_user_id])
