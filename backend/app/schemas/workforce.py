"""WT3 Workforce schema —— 接口契约（spec.md §10.6/§10.7）+ API 端点 schema。

本文件定义：
- §10.6 WT3 → WT4：WorkforceRunData / AgentRunMetrics（供 WT4 进化层消费）
- §10.7 WT3 API 端点请求/响应 schema

注意：PositionCapability 复用 WT1 在 schemas/compiler.py 中的定义（§10.3 契约）。
"""
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# spec.md §10.6 定义的 7 个生命周期阶段字面量
LifecycleStageLiteral = Literal[
    "recruit",
    "training",
    "production",
    "evaluation",
    "continuous_learning",
    "promotion",
    "retired",
]


# ============================================================================
# 生命周期阶段枚举
# ============================================================================

class LifecycleStage(str, Enum):
    """AI 数字员工生命周期阶段（PRD §5.6 / spec.md §10.6）。

    MVP 3 阶段：RECRUIT → TRAINING → PRODUCTION
    完整 7 阶段（P1）：含 EVALUATION / CONTINUOUS_LEARNING / PROMOTION / RETIRED
    """
    RECRUIT = "recruit"
    TRAINING = "training"
    PRODUCTION = "production"
    EVALUATION = "evaluation"            # P1
    CONTINUOUS_LEARNING = "continuous_learning"  # P1
    PROMOTION = "promotion"              # P1
    RETIRED = "retired"                  # P1


# MVP 支持的阶段集合
MVP_STAGES = {LifecycleStage.RECRUIT, LifecycleStage.TRAINING, LifecycleStage.PRODUCTION}

# 合法阶段转换映射（MVP）
MVP_TRANSITIONS = {
    LifecycleStage.RECRUIT: {LifecycleStage.TRAINING},
    LifecycleStage.TRAINING: {LifecycleStage.PRODUCTION},
    LifecycleStage.PRODUCTION: set(),  # MVP 中 production 是终态
}


# ============================================================================
# §10.6 WT3 → WT4：WorkforceRunData 契约
# ============================================================================

class AgentRunMetrics(BaseModel):
    """单个 Agent 的运行指标（spec.md §10.6）。

    所有字段必填（无默认值），严格遵循 spec 契约。
    """
    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    agent_name: str
    position: str
    lifecycle_stage: LifecycleStageLiteral
    tasks_total: int
    tasks_completed: int
    tasks_failed: int
    avg_response_time_ms: float
    avg_satisfaction: float
    kpi_performance: dict[str, float]
    tool_usage: dict[str, int]
    last_active_at: datetime


class WorkforceRunData(BaseModel):
    """WT3 → WT4 契约：Workforce 运行数据（spec.md §10.6）。

    WT4 的 evolution.org_analytics.get_metrics() 读取此结构。
    所有字段必填（无默认值），严格遵循 spec 契约。
    """
    model_config = ConfigDict(from_attributes=True)

    enterprise_id: str
    period_start: datetime
    period_end: datetime
    agents: list[AgentRunMetrics]
    collaboration_events: int
    approval_gates: int
    error_count: int


# ============================================================================
# §10.7 WT3 API 端点 schema
# ============================================================================

# --- POST /api/v1/workforce/generate ---

class GenerateRequest(BaseModel):
    """触发生成请求。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str


class GenerateResponse(BaseModel):
    """生成响应：返回推荐岗位列表。

    spec §10.7 定义 recommendations 为 list[PositionCapability]，
    实现扩展为含 6 维度匹配评分的 dict（加性扩展，不破坏前端契约）。
    """
    model_config = ConfigDict(from_attributes=True)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0


# --- POST /api/v1/workforce/confirm ---

class ConfirmRequest(BaseModel):
    """确认推荐请求。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    confirmed_position_ids: list[str] = Field(default_factory=list)
    adjustments: Optional[dict[str, Any]] = None


class CreatedAgentInfo(BaseModel):
    """已创建 Agent 信息。"""
    model_config = ConfigDict(from_attributes=True)
    agent_id: str
    position_id: str
    position_name: str
    lifecycle_stage: str = LifecycleStage.RECRUIT.value


class FailedAgentInfo(BaseModel):
    """创建失败信息。"""
    model_config = ConfigDict(from_attributes=True)
    position_id: str
    position_name: str
    error: str


class ConfirmResponse(BaseModel):
    """确认响应。"""
    model_config = ConfigDict(from_attributes=True)
    created_agents: list[CreatedAgentInfo] = Field(default_factory=list)
    failed: list[FailedAgentInfo] = Field(default_factory=list)


# --- GET /api/v1/workforce/{enterprise_id} ---

class WorkforceListResponse(BaseModel):
    """Workforce 列表响应。"""
    model_config = ConfigDict(from_attributes=True)
    items: list[AgentRunMetrics] = Field(default_factory=list)
    total: int = 0


# --- GET /api/v1/workforce/{agent_id}/lifecycle ---

class LifecycleHistoryEntry(BaseModel):
    """生命周期历史条目。"""
    model_config = ConfigDict(from_attributes=True)
    stage: LifecycleStageLiteral
    stage_entered_at: datetime
    transition_reason: Optional[str] = None


class LifecycleResponse(BaseModel):
    """生命周期状态响应。"""
    model_config = ConfigDict(from_attributes=True)
    stage: LifecycleStageLiteral
    stage_entered_at: datetime
    history: list[LifecycleHistoryEntry] = Field(default_factory=list)


# --- POST /api/v1/workforce/{agent_id}/transition ---

class TransitionRequest(BaseModel):
    """阶段转换请求。"""
    model_config = ConfigDict(from_attributes=True)
    target_stage: str
    reason: Optional[str] = None


class TransitionResponse(BaseModel):
    """阶段转换响应。"""
    model_config = ConfigDict(from_attributes=True)
    new_stage: LifecycleStageLiteral
    status: Literal["success", "failed"]
