"""耐久 LangGraph Agent 构建任务模型。"""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base
from app.models.base import TimestampMixin
from app.models.cognition import JSON


class AgentBuildTask(Base, TimestampMixin):
    """保存 Agent 构建输入、执行租约、HITL 审批和可查询结果。

    模型调用、文件扫描和向量化不再占用 API 请求；Worker 领取 queued 任务后执行
    LangGraph。HITL 暂停后状态为 paused，审批接口写入 approval 并重新排队。
    """

    __tablename__ = "agent_build_tasks"

    enterprise_id = Column(String(36), nullable=False, index=True)
    owner_user_id = Column(String(36), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    folder_path = Column(Text, nullable=False)
    require_approval = Column(Boolean, nullable=False, default=False, server_default="false")
    # queued/running/paused/completed/failed/cancelled
    status = Column(String(16), nullable=False, default="queued", index=True)
    idempotency_key = Column(String(128), nullable=True, index=True)
    thread_id = Column(String(64), nullable=True, index=True)
    current_step = Column(String(64), nullable=True)
    result = Column(JSON, nullable=True)
    approval = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    lease_owner = Column(String(128), nullable=True, index=True)
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    attempt = Column(Integer, nullable=False, default=0, server_default="0")
    cancel_requested = Column(Boolean, nullable=False, default=False, server_default="false")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
