from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Dict, Any, Literal
from datetime import datetime


class SkillResponse(BaseModel):
    id: str
    agent_id: str
    name: str
    description: Optional[str] = None
    skill_type: str
    input_type: Optional[str] = None
    output_type: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    # P1-SKILL: 自主 Skill 生态字段
    source: Literal["manual", "generated", "imported"] = "manual"
    status: Literal["draft", "pending", "approved", "rejected"] = "approved"
    generation_context: Optional[Dict[str, Any]] = None
    review_result: Optional[Dict[str, Any]] = None
    permissions: Optional[list[str]] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SkillExecuteRequest(BaseModel):
    input_data: Dict[str, Any]
    conversation_id: Optional[str] = None


class SkillExecuteResponse(BaseModel):
    skill_id: str
    output_data: Dict[str, Any]
    execution_time_ms: int


class SkillCreate(BaseModel):
    """创建技能的请求体。"""
    agent_id: str
    name: str
    description: Optional[str] = None
    skill_type: str
    input_type: Optional[str] = None
    output_type: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    # 手动创建时通常不需要显式传入，接口会默认 manual/approved
    source: Optional[Literal["manual", "generated", "imported"]] = "manual"
    status: Optional[Literal["draft", "pending", "approved", "rejected"]] = "approved"
    permissions: Optional[list[str]] = None


class SkillUpdate(BaseModel):
    """更新技能的请求体（所有字段可选）。"""
    name: Optional[str] = None
    description: Optional[str] = None
    skill_type: Optional[str] = None
    input_type: Optional[str] = None
    output_type: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    generation_context: Optional[Dict[str, Any]] = None
    review_result: Optional[Dict[str, Any]] = None
    permissions: Optional[list[str]] = None
    # 仅管理员/审批接口可修改状态，普通更新不允许改 status


class SkillExecutionResponse(BaseModel):
    """技能执行历史记录响应。"""
    id: str
    skill_id: str
    user_id: str
    agent_id: Optional[str] = None
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    execution_time_ms: int
    status: str
    error_message: Optional[str] = None
    is_review_run: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# P1-SKILL: 导入外部 Skill
class SkillImportRequest(BaseModel):
    agent_id: str
    package: Dict[str, Any]


# P1-SKILL: 生成技能请求
class SkillGenerateRequest(BaseModel):
    agent_id: str
    # 可选：指定要生成的技能数量上限
    max_skills: int = Field(default=3, ge=1, le=10)


# P1-SKILL: 审批请求
class SkillReviewRequest(BaseModel):
    reason: Optional[str] = None


class AgentListResponse(BaseModel):
    agents: list
    total: int
