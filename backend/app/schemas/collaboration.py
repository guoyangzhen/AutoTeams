"""WT4 协作 schema —— 接口契约（spec.md §10.7 WT4 部分）+ API 端点 schema。

涵盖：事件驱动协作（7 步演示案例）+ 审批门（人机协作）+ 回滚。
"""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# 事件状态
EventStatusLiteral = Literal["pending", "processed", "failed"]
# 审批状态
ApprovalStatusLiteral = Literal["pending", "approved", "rejected"]
# 协作模式（PRD §5.9，MVP 2 种）
CollaborationModeLiteral = Literal["advise_confirm", "human_approval_gate"]
# P2 字段对齐: 协作事件类型（与前端 CollaborationEventType 统一 + 后端 demo 扩展）
CollaborationEventTypeLiteral = Literal[
    "inquiry_received",
    "product_query",
    "quotation_generated",
    "approval_submitted",
    "approval_approved",
    "order_synced",
    "after_sales",
    "handoff",
    "escalation",
    "error",
    # 后端 demo/seed 扩展事件类型（业务需要，前端后续同步）
    "opportunity_created",
    "approval_flow_created",
    "deal_closed",
]


# ============================================================================
# 事件驱动协作 schema
# ============================================================================

class PublishEventRequest(BaseModel):
    """发布事件请求。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    event_type: CollaborationEventTypeLiteral = Field(...)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_agent_id: Optional[str] = None
    target_agent_id: Optional[str] = None


class PublishEventResponse(BaseModel):
    """发布事件响应。"""
    model_config = ConfigDict(from_attributes=True)
    event_id: str
    status: EventStatusLiteral = "pending"


class CollaborationEvent(BaseModel):
    """协作事件视图。"""
    model_config = ConfigDict(from_attributes=True)
    event_id: str
    enterprise_id: str
    event_type: CollaborationEventTypeLiteral
    payload: dict[str, Any] = Field(default_factory=dict)
    source_agent_id: Optional[str] = None
    target_agent_id: Optional[str] = None
    status: EventStatusLiteral = "pending"
    created_at: datetime


class EventListResponse(BaseModel):
    """事件列表响应（分页）。"""
    model_config = ConfigDict(from_attributes=True)
    items: list[CollaborationEvent] = Field(default_factory=list)
    total: int = 0


# ============================================================================
# 审批门（人机协作）schema
# ============================================================================

class ApprovalGateView(BaseModel):
    """审批门视图。"""
    model_config = ConfigDict(from_attributes=True)
    id: str
    enterprise_id: str
    process_id: str
    node_id: str
    agent_id: Optional[str] = None
    status: ApprovalStatusLiteral = "pending"
    approver_id: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime


class CreateApprovalGateRequest(BaseModel):
    """创建审批门请求（内部触发，人机协作模式）。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    process_id: str
    node_id: str
    agent_id: Optional[str] = None
    mode: CollaborationModeLiteral = "human_approval_gate"


class ApproveGateRequest(BaseModel):
    """审批通过请求。"""
    model_config = ConfigDict(from_attributes=True)
    comment: Optional[str] = None


class ApproveGateResponse(BaseModel):
    """审批通过响应。"""
    model_config = ConfigDict(from_attributes=True)
    approved: bool
    process_resumed: bool = True


class RejectGateRequest(BaseModel):
    """审批拒绝请求。"""
    model_config = ConfigDict(from_attributes=True)
    reason: str = Field(..., min_length=1, max_length=1000)


class RejectGateResponse(BaseModel):
    """审批拒绝响应。"""
    model_config = ConfigDict(from_attributes=True)
    rejected: bool


# ============================================================================
# 回滚 schema
# ============================================================================

class RollbackRequest(BaseModel):
    """触发回滚请求。"""
    model_config = ConfigDict(from_attributes=True)
    snapshot_id: str


class RollbackResponse(BaseModel):
    """回滚响应。"""
    model_config = ConfigDict(from_attributes=True)
    rolled_back: bool
    agent_id: str
    operation: str = ""
    snapshot_id: str


# ============================================================================
# 错误处理 schema（供 service 返回结构化结果）
# ============================================================================

class ErrorRecoveryResult(BaseModel):
    """错误处理与三级恢复结果（PRD §5.10）。

    error_type 包含 MVP 2 类（tool_failure / process_exception）+
    P1 3 类（knowledge_gap / capability_insufficiency / data_conflict，
    P1 阶段仅预留接口，MVP 返回占位结果）。
    """
    model_config = ConfigDict(from_attributes=True)
    error_type: Literal[
        "tool_failure",
        "process_exception",
        # P1 错误类型（预留接口，MVP 阶段仅记录）
        "knowledge_gap",
        "capability_insufficiency",
        "data_conflict",
    ]
    severity: Literal["minor", "moderate", "severe"]
    # 三级恢复：minor→自动重试 / moderate→降级 / severe→暂停+人工
    recovery_action: Literal["retry", "fallback", "pause_and_escalate", "rollback"]
    resolved: bool = False
    attempts: int = 0
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
