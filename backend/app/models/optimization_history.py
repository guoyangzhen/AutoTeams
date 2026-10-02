"""优化历史模型。

之前 loop_engine.py 的 _optimization_history 存在内存 list 中，重启即丢。
本模型持久化反馈分析、知识缺口分析、检索优化的全部历史。
"""
from sqlalchemy import Column, String, ForeignKey, JSON, Boolean, DateTime
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base import TimestampMixin


class OptimizationHistory(Base, TimestampMixin):
    __tablename__ = "optimization_histories"

    # BE-REL-02: Agent 删除时级联删除优化历史
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    # feedback / gap / optimization
    type = Column(String(32), nullable=False, index=True)
    # 输入数据（反馈内容/查询统计/检索参数）
    input_data = Column(JSON, nullable=True)
    # 输出数据（分析结论/优化建议/调整后的参数）
    output_data = Column(JSON, nullable=True)
    # 是否已应用优化
    applied = Column(Boolean, default=False, nullable=False)
    # 应用时间（P0-13-C: String → DateTime）
    applied_at = Column(DateTime(timezone=True), nullable=True)

    agent = relationship("Agent", back_populates="optimization_history")
    user = relationship("User")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "type": self.type,
            "input": self.input_data,
            "output": self.output_data,
            "applied": self.applied,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
