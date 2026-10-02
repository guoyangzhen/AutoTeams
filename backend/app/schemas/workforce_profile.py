"""AutoTeams 4.0 数字员工档案与名牌 Schema 定义。"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class DutyBoundaries(BaseModel):
    allowed: List[str] = Field(default_factory=list, description="明确授权履约的职责与业务事项")
    forbidden: List[str] = Field(default_factory=list, description="严禁越权履约或需要强审批的事项")


class WorkforceProfileBase(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=64, description="数字员工姓名")
    job_title: str = Field(..., min_length=1, max_length=64, description="岗位与职务名称")
    department: str = Field(default="通用业务部", max_length=64, description="所属部门")
    duty_boundaries: DutyBoundaries = Field(default_factory=DutyBoundaries, description="岗位职责边界")
    tone_style: str = Field(default="professional", max_length=64, description="服务与回复语调风格")
    authorized_flows: List[str] = Field(default_factory=list, description="已授权的 Flow-Core SOP ID 清单")
    accessible_knowledge_buckets: List[str] = Field(default_factory=list, description="允许检索的知识桶/分类清单")
    authorized_tools: List[str] = Field(default_factory=list, description="允许调用的工具与 MCP 清单")
    employment_status: str = Field(default="shadow", description="聘用状态：shadow(实习考核) / active(正式在职) / suspended / retired")
    performance_score: float = Field(default=100.0, ge=0.0, le=100.0, description="综合履约考评积分")
    avatar_url: Optional[str] = Field(default=None, description="员工头像 URL")


class WorkforceProfileCreate(WorkforceProfileBase):
    agent_id: Optional[str] = Field(default=None, description="关联的底层 Agent ID")


class WorkforceProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    duty_boundaries: Optional[DutyBoundaries] = None
    tone_style: Optional[str] = None
    authorized_flows: Optional[List[str]] = None
    accessible_knowledge_buckets: Optional[List[str]] = None
    authorized_tools: Optional[List[str]] = None
    employment_status: Optional[str] = None
    performance_score: Optional[float] = None
    avatar_url: Optional[str] = None


class WorkforceProfileResponse(WorkforceProfileBase):
    id: str
    enterprise_id: str
    agent_id: Optional[str] = None
    employee_badge: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DutyBoundaryCheckRequest(BaseModel):
    action_intent: str = Field(..., description="拟执行的业务动作或用户意图")


class DutyBoundaryCheckResponse(BaseModel):
    allowed: bool
    reason: str
    matched_boundary: Optional[str] = None
