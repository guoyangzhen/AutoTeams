"""任务计划模型。

之前 task_planner.py 把所有 plan 状态存在进程内存 dict 中，重启即丢，
无法多 worker 部署，Docker 重启清空用户数据。本模型持久化任务计划与步骤状态。
"""
from sqlalchemy import Column, String, Text, ForeignKey, JSON, Integer
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.base import TimestampMixin


class TaskPlan(Base, TimestampMixin):
    __tablename__ = "task_plans"

    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    # BE-REL-02: Agent 删除后保留计划历史，但解除关联
    agent_id = Column(String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    goal = Column(Text, nullable=False)
    # steps: [{id, title, status, output, error, started_at, completed_at}]
    steps = Column(JSON, nullable=False, default=list)
    # planning / running / completed / failed
    status = Column(String(32), default="planning", nullable=False, index=True)
    # 进度百分比缓存，避免每次解析 steps 计算
    progress = Column(Integer, default=0, nullable=False)

    user = relationship("User")
    agent = relationship("Agent", back_populates="task_plans")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "steps": self.steps or [],
            "status": self.status,
            "progress": self.progress,
            "agent_id": self.agent_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
