"""WT4 协作模型 —— 事件总线 + 审批门 + 操作快照（回滚支持）。

依据：重构方案_v3.md §7.7 + PRD §5.8/§5.9/§5.10。
表结构严格匹配 WT6 已创建的迁移：
  2026_07_29_0318-a0b1c2d3f6f0_add_collaboration_tables.py
"""
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, JSON

from app.database import Base
from app.utils.time import utcnow


# 事件状态
EventStatusLiteral = ("pending", "processed", "failed")
# 审批状态
ApprovalStatusLiteral = ("pending", "approved", "rejected")


class CollaborationEvent(Base):
    """事件驱动协作：事件总线（PRD §5.8）。

    7 步演示案例事件类型：
    new_inquiry / product_query / quotation / financial_review /
    approval / deal_closed / customer_sync / after_sales
    """
    __tablename__ = "collaboration_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type = Column(String(64), nullable=False)
    # 事件负载（JSON：{customer_id, product, amount, ...}）
    payload = Column(JSON, nullable=False, default=dict)
    source_agent_id = Column(
        String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    target_agent_id = Column(
        String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    # 事件状态：pending / processed / failed
    status = Column(String(16), nullable=False, server_default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self) -> str:
        return f"<CollaborationEvent type={self.event_type} status={self.status}>"


class ApprovalGate(Base):
    """审批门：人机协作（PRD §5.9 MVP 模式 1 人类审批介入 + 模式 2 AI 提议人类确认）。

    process_id + node_id 定位流程中的审批节点。
    approver_id 为人类审批者（users.id）。
    """
    __tablename__ = "approval_gates"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    enterprise_id = Column(
        String(36), ForeignKey("enterprises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    process_id = Column(String(64), nullable=False)
    node_id = Column(String(64), nullable=False)
    agent_id = Column(
        String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    # 审批状态：pending / approved / rejected
    status = Column(String(16), nullable=False, server_default="pending")
    approver_id = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self) -> str:
        return f"<ApprovalGate process={self.process_id} node={self.node_id} status={self.status}>"


class OperationSnapshot(Base):
    """操作前置快照：回滚机制（PRD §5.10）。

    每个 Agent 操作前创建快照，操作失败可回滚到上一个稳定状态。
    snapshot 为 JSON：操作前的状态镜像（config / memory_config / kpi_ids / ...）。
    """
    __tablename__ = "operation_snapshots"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation = Column(String(128), nullable=False)
    snapshot = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self) -> str:
        return f"<OperationSnapshot agent={self.agent_id} op={self.operation}>"
