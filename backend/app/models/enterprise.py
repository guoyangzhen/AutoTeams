import uuid
from sqlalchemy import Column, String, DateTime, Boolean, Integer
from sqlalchemy.orm import relationship
from app.database import Base
from app.utils.time import utcnow


class Enterprise(Base):
    __tablename__ = "enterprises"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    invite_token = Column(String, unique=True, nullable=True)
    invite_expires_at = Column(DateTime(timezone=True), nullable=True)
    # P2-2: 软删除标志，is_active=False 表示企业已被删除
    is_active = Column(Boolean, default=True, nullable=False)
    # P2-2: 邀请使用次数限制
    invite_max_uses = Column(Integer, default=10, nullable=False)
    invite_used_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # WT2: 当前激活的 Enterprise Runtime 版本 ID（指向 enterprise_runtimes.id）
    # 仅加性追加字段，不加外键约束（避免与 WT6 迁移冲突），由 service 层维护一致性
    current_runtime_version_id = Column(String(36), nullable=True, index=True)

    users = relationship("User", back_populates="enterprise")
    agents = relationship("Agent", back_populates="enterprise")
