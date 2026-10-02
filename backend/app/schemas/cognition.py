"""认知层 schema —— 对应 PRD §4.2 企业认知层数据结构。

定义知识图谱、企业画像、企业运行模型的 Pydantic schema，
供认知层 service 和 API 使用。
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.compiler import GraphNode, GraphEdge


# ============================================================================
# 知识图谱 schema
# ============================================================================

class KnowledgeGraphResponse(BaseModel):
    """知识图谱查询响应。"""
    enterprise_id: str
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    version: str = "v1.0.0"

    model_config = ConfigDict(from_attributes=True)


class KnowledgeGraphCreate(BaseModel):
    """知识图谱创建/更新请求。"""
    model_config = ConfigDict(from_attributes=True)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


# ============================================================================
# 企业画像 schema（PRD §4.2 数据结构定义 2）
# ============================================================================

class EnterpriseBasic(BaseModel):
    """企业基本信息。"""
    model_config = ConfigDict(from_attributes=True)
    name: str = ""
    industry: str = ""
    scale: str = ""
    revenue: str = ""
    location: str = ""
    founded: str = ""


class EnterpriseOrgSummary(BaseModel):
    """组织概览。"""
    model_config = ConfigDict(from_attributes=True)
    department_count: int = 0
    headcount: int = 0
    key_roles: list[str] = Field(default_factory=list)


class EnterpriseMaturity(BaseModel):
    """AI 成熟度评级。"""
    model_config = ConfigDict(from_attributes=True)
    level: str = "L1"  # L1-L5
    automation_coverage: float = 0.0
    ai_workforce_count: int = 0


class EnterpriseBusiness(BaseModel):
    """业务概览。"""
    model_config = ConfigDict(from_attributes=True)
    main_products: list[str] = Field(default_factory=list)
    target_industries: list[str] = Field(default_factory=list)
    core_processes: list[str] = Field(default_factory=list)


class EnterpriseProfileData(BaseModel):
    """企业画像完整数据。"""
    model_config = ConfigDict(from_attributes=True)
    basic: EnterpriseBasic = Field(default_factory=EnterpriseBasic)
    tags: list[str] = Field(default_factory=list)
    org_summary: EnterpriseOrgSummary = Field(default_factory=EnterpriseOrgSummary)
    maturity: EnterpriseMaturity = Field(default_factory=EnterpriseMaturity)
    business: EnterpriseBusiness = Field(default_factory=EnterpriseBusiness)
    gaps: list[str] = Field(default_factory=list)
    version: str = "v1.0.0"
    updated_at: Optional[datetime] = None
    completeness_score: float = 0.0


class EnterpriseProfileResponse(BaseModel):
    """企业画像查询响应。"""
    enterprise_id: str
    profile: EnterpriseProfileData

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# 企业运行模型 schema（PRD §4.2 数据结构定义 3）
# ============================================================================

class RoleDefinition(BaseModel):
    """岗位定义。"""
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    department: str = ""
    level: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    kpi_ids: list[str] = Field(default_factory=list)
    permission_ids: list[str] = Field(default_factory=list)


class ProcessDefinitionModel(BaseModel):
    """流程定义。"""
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    type: str = "sop"
    steps: list[dict] = Field(default_factory=list)
    owner_role_id: str = ""
    participants: list[str] = Field(default_factory=list)
    trigger_event: str = ""
    system_ids: list[str] = Field(default_factory=list)


class CapabilityItem(BaseModel):
    """岗位能力项。"""
    model_config = ConfigDict(from_attributes=True)
    role_id: str
    required_capabilities: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class RuntimeRules(BaseModel):
    """运行规则。"""
    model_config = ConfigDict(from_attributes=True)
    collaboration_rules: list[dict] = Field(default_factory=list)
    data_flow_rules: list[dict] = Field(default_factory=list)
    escalation_rules: list[dict] = Field(default_factory=list)


class GapItem(BaseModel):
    """知识空白项。"""
    model_config = ConfigDict(from_attributes=True)
    area: str
    severity: str = "medium"
    suggestion: str = ""


class EnterpriseOperatingModelData(BaseModel):
    """企业运行模型完整数据（设计态）。"""
    model_config = ConfigDict(from_attributes=True)
    version: str = "v1.0.0"
    completeness: float = 0.0
    organization: dict = Field(default_factory=dict)
    roles: list[RoleDefinition] = Field(default_factory=list)
    processes: list[ProcessDefinitionModel] = Field(default_factory=list)
    capabilities: list[CapabilityItem] = Field(default_factory=list)
    runtime_rules: RuntimeRules = Field(default_factory=RuntimeRules)
    gaps: list[GapItem] = Field(default_factory=list)


class OperatingModelResponse(BaseModel):
    """运行模型查询响应。"""
    enterprise_id: str
    model: EnterpriseOperatingModelData
    version: str = "v1.0.0"

    model_config = ConfigDict(from_attributes=True)


class OperatingModelUpdate(BaseModel):
    """运行模型更新请求。"""
    model_config = ConfigDict(from_attributes=True)
    model: EnterpriseOperatingModelData
