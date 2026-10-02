"""AutoTeams 4.0 业务 SOP 规程卡数据库模型。"""
from __future__ import annotations

import uuid
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, Boolean
from app.database import Base
from app.utils.time import utcnow


class FlowCardModel(Base):
    """业务标准规程卡持久化表。"""
    __tablename__ = "flow_cards"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True)
    flow_id = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    version = Column(String(32), nullable=False, default="1.0.0")
    description = Column(Text, nullable=True)
    flow_data = Column(JSON, nullable=False)  # 完整存储 FlowCard JSON 内容
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<FlowCardModel flow_id={self.flow_id} name={self.name} v={self.version}>"
