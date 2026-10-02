"""技能执行记录模型。

之前 Skill 执行后结果不持久化，无历史记录，无法回溯。
本模型持久化每次 Skill 执行的输入/输出/耗时。
"""
from sqlalchemy import Column, String, ForeignKey, JSON, Integer, Boolean
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base import TimestampMixin


class SkillExecution(Base, TimestampMixin):
    __tablename__ = "skill_executions"

    skill_id = Column(String(36), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    execution_time_ms = Column(Integer, default=0, nullable=False)
    # success / failed
    status = Column(String(32), default="success", nullable=False)
    error_message = Column(String, nullable=True)
    # P1-SKILL: 是否为待审批 Skill 的沙箱试运行
    is_review_run = Column(Boolean, default=False, nullable=False)

    skill = relationship("Skill")
    user = relationship("User")
    agent = relationship("Agent", back_populates="skill_executions")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "skill_id": self.skill_id,
            "input_data": self.input_data,
            "output_data": self.output_data,
            "execution_time_ms": self.execution_time_ms,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
