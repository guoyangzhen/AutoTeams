"""WT4 交互式企业访谈 schema —— 接口契约（spec.md §10.7 WT4 部分）+ API 端点 schema。

7 大类问题库：sales / customer_service / procurement / finance / hr / data / kpi
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# 7 大类问题分类
CategoryLiteral = Literal[
    "sales", "customer_service", "procurement", "finance", "hr", "data", "kpi"
]
SessionStatusLiteral = Literal["active", "completed"]
QuestionPriorityLiteral = Literal["P0", "P1", "P2"]


# ============================================================================
# 访谈会话 schema
# ============================================================================

class StartSessionRequest(BaseModel):
    """启动访谈会话请求。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str


class StartSessionResponse(BaseModel):
    """启动访谈会话响应。"""
    model_config = ConfigDict(from_attributes=True)
    session_id: str
    status: SessionStatusLiteral = "active"
    total_count: int = 0


class SessionStatusResponse(BaseModel):
    """会话状态响应（spec.md §10.7 GET /interview/sessions/{id}）。

    含已回答/总数/完成度。
    """
    model_config = ConfigDict(from_attributes=True)
    session_id: str
    status: SessionStatusLiteral
    answered_count: int
    total_count: int
    completeness: float = 0.0


# ============================================================================
# 访谈问题 schema
# ============================================================================

class NextQuestionResponse(BaseModel):
    """下一问响应（spec.md §10.7 GET /interview/sessions/{id}/next-question）。

    无下一问时 question_id 为 None。
    """
    model_config = ConfigDict(from_attributes=True)
    question_id: Optional[str] = None
    category: Optional[CategoryLiteral] = None
    question: Optional[str] = None
    priority: Optional[QuestionPriorityLiteral] = None
    affected_field: Optional[str] = None


class SubmitAnswerRequest(BaseModel):
    """提交回答请求。"""
    model_config = ConfigDict(from_attributes=True)
    question_id: str
    answer: str = Field(..., min_length=1, max_length=10000)


class SubmitAnswerResponse(BaseModel):
    """提交回答响应（spec.md §10.7 POST /interview/sessions/{id}/answers）。

    返回更新后的完成度 + 下一问（可选）。
    """
    model_config = ConfigDict(from_attributes=True)
    updated_completeness: float
    recompile_triggered: bool = False
    next_question: Optional[NextQuestionResponse] = None
