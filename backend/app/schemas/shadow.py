"""影子模式 schema —— 接口契约（愿景蓝图 §4.1.1 + 产品完善方案_v3.2 补1）。

影子模式状态机：shadowing → evaluating → qualified → autonomous。
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# 影子任务状态
ShadowStatusLiteral = Literal["shadowing", "evaluating", "qualified", "autonomous"]
# 评估结果
ShadowEvalLiteral = Literal["pending", "match", "mismatch"]


class CreateShadowTaskRequest(BaseModel):
    """创建影子任务（记录输入问题 + 可选真人基线）。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    agent_id: Optional[str] = None
    task_type: str = Field(..., min_length=1, max_length=64)
    question: str = Field(..., min_length=1, max_length=10000)
    human_answer: Optional[str] = Field(default=None, max_length=20000)


class RecordAiAnswerRequest(BaseModel):
    """记录 AI 回答（shadowing → evaluating）。"""
    model_config = ConfigDict(from_attributes=True)
    ai_answer: str = Field(..., min_length=1, max_length=20000)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class EvaluateTaskRequest(BaseModel):
    """评估 AI 回答与真人基线（evaluating → qualified / 保持 evaluating）。"""
    model_config = ConfigDict(from_attributes=True)
    match: bool = Field(...)
    # 达到阈值后是否自动晋升为 qualified（默认 false：需人工确认）
    auto_qualify: bool = False


class ShadowTaskView(BaseModel):
    """影子任务视图。"""
    model_config = ConfigDict(from_attributes=True)
    id: str
    enterprise_id: str
    agent_id: Optional[str] = None
    task_type: str
    question: str
    human_answer: Optional[str] = None
    ai_answer: Optional[str] = None
    confidence: Optional[float] = None
    status: ShadowStatusLiteral = "shadowing"
    eval_result: ShadowEvalLiteral = "pending"
    promoted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ShadowTaskListResponse(BaseModel):
    """影子任务列表（分页）。"""
    model_config = ConfigDict(from_attributes=True)
    items: list[ShadowTaskView] = Field(default_factory=list)
    total: int = 0


class ShadowStageSummary(BaseModel):
    """单个阶段汇总。"""
    model_config = ConfigDict(from_attributes=True)
    status: ShadowStatusLiteral
    count: int = 0
    match_count: int = 0
    mismatch_count: int = 0
    # 平均置信度（0~1）
    avg_confidence: Optional[float] = None


class ShadowSummaryResponse(BaseModel):
    """影子模式汇总（按阶段统计 + 信任度）。"""
    model_config = ConfigDict(from_attributes=True)
    stages: list[ShadowStageSummary] = Field(default_factory=list)
    # 已晋升为 autonomous 的任务数（信任度达成佐证）
    autonomous_count: int = 0
    total_count: int = 0
