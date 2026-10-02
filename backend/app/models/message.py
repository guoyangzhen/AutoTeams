import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, Integer, Boolean, Index
from sqlalchemy.orm import relationship
from app.database import Base
from app.utils.time import utcnow


class Message(Base):
    __tablename__ = "messages"

    # BE-PER-03/DB-01: 为按会话 + 时间查询创建复合索引（最常用查询模式）
    __table_args__ = (
        Index("idx_messages_conversation_created", "conversation_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    sources = Column(JSON, nullable=True)
    satisfaction = Column(String, nullable=True)
    # 新增：token 消耗
    token_count = Column(Integer, default=0, nullable=False)
    # 新增：使用的模型
    model_used = Column(String(64), nullable=True)
    # 新增：软删除
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)

    conversation = relationship("Conversation", back_populates="messages")
