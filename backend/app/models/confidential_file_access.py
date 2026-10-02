"""涉密文件授权访问模型。

P1-SANDBOX: 记录哪些用户被授权访问极度私密文件。
"""
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship

from app.database import Base
from app.utils.time import utcnow


class ConfidentialFileAccess(Base):
    __tablename__ = "confidential_file_accesses"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_id = Column(
        String(36),
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 授权人（企业管理员或文件上传者）
    granted_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    granted_at = Column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    file = relationship("File", backref="confidential_accesses")
    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_confidential_file_user", "file_id", "user_id", unique=True),
    )
