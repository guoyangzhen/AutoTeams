"""双盲反事实影子评估 schema —— 接口契约（AutoTeams 5.0 战役 4）。

端点前缀 ``/api/v1/shadow/counterfactual``。
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

DiffDimensionLiteral = Literal["semantics", "latency", "cost", "risk"]
DiffAssessmentLiteral = Literal["better", "worse", "parity"]


class CreateCounterfactualSessionRequest(BaseModel):
    """创建一次反事实推演（同一场景下的真人处置 vs 数字员工提案）。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    employee_badge: str = Field(..., min_length=1, max_length=64)
    scenario: str = Field(..., min_length=1, max_length=10000)
    human_action_snapshot: str = Field(..., min_length=1, max_length=20000)
    agent_proposal_snapshot: str = Field(..., min_length=1, max_length=20000)
    human_duration_seconds: float = Field(0.0, ge=0.0, le=86400.0)
    agent_duration_seconds: float = Field(0.0, ge=0.0, le=86400.0)
    human_cost_yuan: float = Field(0.0, ge=0.0, le=1000000.0)
    agent_cost_yuan: float = Field(0.0, ge=0.0, le=1000000.0)
    guardrail_breach_count: int = Field(0, ge=0, le=1000)
    # 免干预转正所需的连续达标笔数（默认 50；仅管理员可调低用于演练）
    required_streak: Optional[int] = Field(default=None, ge=1, le=1000)
    hourly_labor_rate_yuan: float = Field(180.0, gt=0.0, le=100000.0)


class CounterfactualDiffView(BaseModel):
    """差分明细视图。"""
    model_config = ConfigDict(from_attributes=True)
    diff_id: str
    session_id: str
    dimension: DiffDimensionLiteral
    human_value: str = ""
    agent_value: str = ""
    delta_score: float = 0.0
    assessment: DiffAssessmentLiteral = "parity"
    note: str = ""
    created_at: datetime


class CounterfactualVerdictView(BaseModel):
    """单笔反事实裁决视图。"""
    model_config = ConfigDict(from_attributes=True)
    is_qualified: bool
    semantic_alignment_score: float
    time_saving_seconds: float
    cost_delta_yuan: float
    expected_net_benefit_yuan: float
    guardrail_breach_count: int
    reasons: list[str] = Field(default_factory=list)


class PromotionDecisionView(BaseModel):
    """免干预转正裁决视图。"""
    model_config = ConfigDict(from_attributes=True)
    is_auto_promoted: bool
    consecutive_pass_streak: int
    required_streak: int
    remaining_to_promotion: int
    hit_threshold: bool


class CounterfactualSessionView(BaseModel):
    """反事实评估会话视图。"""
    model_config = ConfigDict(from_attributes=True)
    session_id: str
    enterprise_id: str
    employee_badge: str
    scenario: str
    human_action_snapshot: str
    agent_proposal_snapshot: str
    semantic_alignment_score: float
    time_saving_seconds: float
    cost_delta_yuan: float
    expected_net_benefit_yuan: float
    human_duration_seconds: float = 0.0
    agent_duration_seconds: float = 0.0
    human_cost_yuan: float = 0.0
    agent_cost_yuan: float = 0.0
    guardrail_breach_count: int
    is_qualified: bool
    consecutive_pass_streak: int
    is_auto_promoted: bool
    promoted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    diffs: list[CounterfactualDiffView] = Field(default_factory=list)
    verdict: Optional[CounterfactualVerdictView] = None
    promotion: Optional[PromotionDecisionView] = None


class CounterfactualSessionListResponse(BaseModel):
    """反事实评估会话列表（分页）。"""
    model_config = ConfigDict(from_attributes=True)
    items: list[CounterfactualSessionView] = Field(default_factory=list)
    total: int = 0


class PromotionGateView(BaseModel):
    """免干预转正准入看板。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    employee_badge: str
    consecutive_pass_streak: int
    required_streak: int
    remaining_to_promotion: int
    is_auto_promoted: bool
    total_sessions: int
    qualified_sessions: int
    semantic_alignment_threshold: float
    max_cost_delta_yuan: float
    max_guardrail_breaches: int
