"""WT4 进化层 schema —— 接口契约（spec.md §10.7 WT4 部分）+ API 端点 schema。

定义：
- AI Advisor 建议（4 类：knowledge/process/capability/organization）+ 一键应用
- AI Org Analytics 指标（5 类 + L1-L5 成熟度评级，MVP 目标 L2）
"""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# 建议类型（PRD §5.4）
SuggestionTypeLiteral = Literal["knowledge", "process", "capability", "organization"]
SuggestionStatusLiteral = Literal["pending", "applied", "rejected"]
# 指标类型（PRD §5.5）
MetricTypeLiteral = Literal[
    "agent_workload", "process_efficiency", "tool_usage", "business_impact", "maturity"
]
# 成熟度等级（PRD §5.5，MVP 目标 L2）
MaturityLevelLiteral = Literal["L1", "L2", "L3", "L4", "L5"]


# ============================================================================
# AI Advisor 建议 schema
# ============================================================================

class AdvisorSuggestionBase(BaseModel):
    """建议基础字段。"""
    model_config = ConfigDict(from_attributes=True)
    type: SuggestionTypeLiteral
    title: str = Field(..., max_length=256)
    description: str
    impact: Optional[str] = None


class AdvisorSuggestion(AdvisorSuggestionBase):
    """建议完整视图（响应用）。"""
    id: str
    enterprise_id: str
    status: SuggestionStatusLiteral = "pending"
    applied_at: Optional[datetime] = None
    created_at: datetime


class ApplySuggestionResponse(BaseModel):
    """应用建议响应。"""
    model_config = ConfigDict(from_attributes=True)
    applied: bool
    suggestion_id: str
    affected_agents: list[str] = Field(default_factory=list)
    message: str = ""


class RejectSuggestionRequest(BaseModel):
    """拒绝建议请求。"""
    model_config = ConfigDict(from_attributes=True)
    reason: Optional[str] = None


class RejectSuggestionResponse(BaseModel):
    """拒绝建议响应。"""
    model_config = ConfigDict(from_attributes=True)
    rejected: bool
    suggestion_id: str


class SuggestionListResponse(BaseModel):
    """建议列表响应（分页）。"""
    model_config = ConfigDict(from_attributes=True)
    items: list[AdvisorSuggestion] = Field(default_factory=list)
    total: int = 0


# ============================================================================
# AI Org Analytics 指标 schema
# ============================================================================

class MetricValue(BaseModel):
    """单个指标值（JSON 结构）。"""
    model_config = ConfigDict(from_attributes=True)
    value: float = 0.0
    unit: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class OrgMetricsResponse(BaseModel):
    """组织分析指标响应（spec.md §10.7 GET /evolution/{enterprise_id}/metrics）。

    5 类指标 + 成熟度评级，对齐 spec 契约：
    {agent_workload, process_efficiency, tool_usage, business_impact, maturity_level}
    """
    model_config = ConfigDict(from_attributes=True)
    agent_workload: dict[str, Any] = Field(default_factory=dict)
    process_efficiency: dict[str, Any] = Field(default_factory=dict)
    tool_usage: dict[str, Any] = Field(default_factory=dict)
    business_impact: dict[str, Any] = Field(default_factory=dict)
    maturity_level: str = "L1"
    period: Optional[str] = None


class MaturityRating(BaseModel):
    """成熟度评级详情（L1-L5）。"""
    model_config = ConfigDict(from_attributes=True)
    level: MaturityLevelLiteral
    name: str
    description: str
    # 达标与否（MVP 目标 L2）
    achieved: bool = False
    # 各维度达成情况（用于评级推导）
    dimensions: dict[str, float] = Field(default_factory=dict)


# ============================================================================
# Evolution Timeline schema
# ============================================================================

class TimelineEntry(BaseModel):
    """Evolution Timeline 单条记录。"""
    model_config = ConfigDict(from_attributes=True)
    timestamp: datetime
    event_type: str  # suggestion_generated / suggestion_applied / suggestion_rejected / metric_recorded / optimization_applied
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class TimelineResponse(BaseModel):
    """Evolution Timeline 响应（分页）。"""
    model_config = ConfigDict(from_attributes=True)
    items: list[TimelineEntry] = Field(default_factory=list)
    total: int = 0
