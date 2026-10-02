"""产品行为事件的隐私优先 API 契约。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ProductEventName = Literal[
    "company_brief_viewed",
    "company_brief_action_clicked",
    "company_tour_started",
    "company_tour_step_viewed",
    "company_tour_skipped",
    "company_tour_completed",
    "company_tour_restarted",
]

CompanyJourneyState = Literal[
    "needs_compile",
    "needs_staff",
    "needs_approval",
    "needs_attention",
    "needs_first_run",
    "creating_value",
]

CompanyBriefAction = Literal[
    "compile",
    "staff",
    "start_demo",
    "review_approvals",
    "inspect_execution",
    "view_impact",
]


class ProductEventRequest(BaseModel):
    """浏览器可上报的最小产品事件。

    properties 仅用于低基数产品分组，不接受文本、对象、数组、用户输入、资源 ID 或路径。
    """

    event_name: ProductEventName
    session_id: str = Field(..., min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    surface: Literal["company_overview"] = "company_overview"
    journey_state: CompanyJourneyState | None = None
    action: CompanyBriefAction | None = None
    tour_step: int | None = Field(default=None, ge=1, le=4)
    reduced_motion: bool | None = None


class ProductEventAccepted(BaseModel):
    accepted: bool = True


class ProductEventSummary(BaseModel):
    """仅返回聚合值，供企业管理员观察产品路径转化，不返回个人行为明细。"""

    model_config = ConfigDict(from_attributes=True)

    window_days: int
    total_events: int
    unique_sessions: int
    brief_views: int
    brief_action_clicks: int
    tour_started: int
    tour_completed: int
    tour_skipped: int
    action_clicks: dict[str, int]
    journey_states: dict[str, int]
    generated_at: datetime
