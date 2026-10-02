"""需求澄清对话会话模型。

之前 setup_dialogue.py 把会话状态存在内存 dict 中，重启即丢。
本模型持久化需求澄清对话与生成的方案。
"""
from sqlalchemy import Column, String, Text, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base import TimestampMixin


class SetupSession(Base, TimestampMixin):
    __tablename__ = "setup_sessions"

    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    enterprise_id = Column(String(36), ForeignKey("enterprises.id"), nullable=False, index=True)
    folder_path = Column(Text, nullable=False)
    # messages: [{role, content, timestamp}]
    messages = Column(JSON, nullable=False, default=list)
    # 生成的方案（agent_name, agent_description, suggested_questions, estimated_time 等）
    plan = Column(JSON, nullable=True)
    # active / confirmed / expired
    status = Column(String(32), default="active", nullable=False, index=True)
    confirmed = Column(Boolean, default=False, nullable=False)

    user = relationship("User")
    enterprise = relationship("Enterprise")

    def to_dict(self) -> dict:
        return {
            "session_id": self.id,
            "folder_path": self.folder_path,
            "messages": self.messages or [],
            "plan": self.plan,
            "status": self.status,
            "confirmed": self.confirmed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
