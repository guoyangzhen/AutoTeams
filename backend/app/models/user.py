import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, CheckConstraint
from sqlalchemy.orm import relationship
from app.database import Base
from app.utils.time import utcnow


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String(32), nullable=False, default="member")

    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'member')",
            name="uq_user_role_valid",
        ),
    )
    # P0-13-F: 加 index=True 提升 enterprise JOIN 查询性能
    enterprise_id = Column(String(36), ForeignKey("enterprises.id"), nullable=True, index=True)
    # 新增：支持 refresh token
    refresh_token_hash = Column(String, nullable=True)
    # P2-T8b: Refresh Token 家族 ID，用于检测 token 重放攻击
    refresh_token_family_id = Column(String(36), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    enterprise = relationship("Enterprise", back_populates="users")
    conversations = relationship("Conversation", back_populates="user")
