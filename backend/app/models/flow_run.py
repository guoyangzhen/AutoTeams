"""AutoTeams 4.0 Flow 执行运行态与审批记录持久化模型（AUD-15）。

AUD-15 要求流程执行状态是服务器侧状态机：客户端提交的 ``state`` 不再是权威数据，
所有推进都落库到 ``flow_runs``（含版本号做乐观锁），审批落在 ``flow_approvals``
并绑定 (run_id, node_id, 审批人身份)。因此 HTTP 进程重启后仍能读回原执行进度。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.utils.time import utcnow


class FlowRun(Base):
    """规程执行运行态（服务器权威）。"""

    __tablename__ = "flow_runs"
    __table_args__ = (
        # 支撑"按企业 + 时间倒序列出运行"的运维查询与后续按时间清理。
        Index("ix_flow_runs_enterprise_created", "enterprise_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    flow_id = Column(String(64), nullable=False, index=True)
    flow_version = Column(String(32), nullable=False, default="1.0.0")
    current_node_id = Column(String(64), nullable=False)
    # 完整 FlowExecutionState JSON（槽位、流水、工具产物、模拟标记、上限等）
    state = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False, default="running", index=True)
    # 乐观锁：每次状态推进 +1，冲突时调用方收到 409
    version = Column(Integer, nullable=False, default=1)
    step_count = Column(Integer, nullable=False, default=0)
    last_output = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    started_by = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    last_actor_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    approvals = relationship(
        "FlowApproval", back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )

    def to_state_dict(self) -> Dict[str, Any]:
        return dict(self.state or {})

    def __repr__(self) -> str:
        return (
            f"<FlowRun {self.id} flow={self.flow_id} node={self.current_node_id} "
            f"status={self.status} v={self.version}>"
        )


class FlowApproval(Base):
    """审批记录：绑定 (run_id, node_id, 审批人身份) 与审批结论。"""

    __tablename__ = "flow_approvals"
    __table_args__ = (
        Index("ix_flow_approvals_run_node", "run_id", "node_id"),
        Index("ix_flow_approvals_approver", "approver_user_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(
        String(36), ForeignKey("flow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id = Column(String(64), nullable=False)
    enterprise_id = Column(String(36), nullable=False, index=True)
    approver_user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    approver_email = Column(String(255), nullable=False)
    approver_role = Column(String(32), nullable=False)
    # granted / rejected
    decision = Column(String(16), nullable=False)
    comment = Column(Text, nullable=True)
    # 审批后运行态的版本号，便于核对"这次审批推进了哪个版本"
    applied_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    run = relationship("FlowRun", back_populates="approvals")

    def __repr__(self) -> str:
        return (
            f"<FlowApproval {self.id} run={self.run_id} node={self.node_id} "
            f"by={self.approver_email} decision={self.decision}>"
        )
