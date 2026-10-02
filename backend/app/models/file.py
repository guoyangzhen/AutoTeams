import uuid
from sqlalchemy import Column, String, BigInteger, Boolean, DateTime, ForeignKey, Integer, Text, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base
from app.utils.time import utcnow


class File(Base):
    __tablename__ = "files"

    # BE-PER-03/DB-01: 为按 Agent + 状态/类型查询创建复合索引（文件列表/状态过滤）
    # DB-02: content_hash 唯一约束防止同一 Agent 重复上传相同内容文件
    __table_args__ = (
        Index("idx_files_agent_status", "agent_id", "status"),
        Index("idx_files_agent_type", "agent_id", "file_type"),
        UniqueConstraint("agent_id", "content_hash", name="uq_files_agent_content_hash"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_size = Column(BigInteger, nullable=False)
    file_type = Column(String, nullable=False)
    status = Column(String, default="uploaded")
    is_confidential = Column(Boolean, default=False)
    # P1-SANDBOX: 极度私密文件，需要显式授权才能访问
    is_highly_confidential = Column(Boolean, default=False)
    # P1-SANDBOX: 涉密状态：auto_detected（自动识别）/ confirmed（已确认）/
    #             denied（已拒绝/误报）/ authorized（已授权访问）
    confidential_status = Column(String(32), default="none", nullable=False)
    # 新增：分块数与向量数
    chunk_count = Column(Integer, default=0, nullable=False)
    vector_count = Column(Integer, default=0, nullable=False)
    # 新增：失败原因
    error_message = Column(Text, nullable=True)
    # 新增：内容哈希用于去重
    content_hash = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    agent = relationship("Agent", back_populates="files")
