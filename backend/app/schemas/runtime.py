"""Enterprise Runtime Schema（WT2）。

本文件定义两类 schema：

1. **§10.2 契约本地副本**：``RuntimeCompileResult`` 及其全部子结构，严格遵循
   ``spec.md §10.2``（WT1 → WT2 契约）。WT1 在 ``schemas/compiler.py`` 中定义同一组
   结构；WT1 合并后应统一到 WT1 的定义，本副本用于 WT2 独立开发与测试。
   见 ``重构方案_v3.md §5.5``：WT1 尚未合并时 WT2 按契约定义本地副本。
2. **响应/请求 schema**：``api/runtime.py`` 使用的列表、diff、回滚等结构，遵循
   ``spec.md §10.7`` 的 WT2 API 契约。

所有 Pydantic 模型使用 ``model_config = ConfigDict(from_attributes=True)``（项目硬约束）。
"""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# §10.2 契约本地副本：RuntimeCompileResult 及子结构
# ============================================================


class ShortTermMemoryConfig(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    max_turns: int = 20


class LongTermMemoryConfig(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    enabled: bool = True
    collection_prefix: str = "memory_lt"


class EntityMemoryConfig(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    enabled: bool = True
    linked_graph: bool = True


class MemoryConfig(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    short_term: ShortTermMemoryConfig = Field(default_factory=ShortTermMemoryConfig)
    long_term: LongTermMemoryConfig = Field(default_factory=LongTermMemoryConfig)
    entity_memory: EntityMemoryConfig = Field(default_factory=EntityMemoryConfig)


class SkillBinding(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    skill_id: str
    name: str
    enabled: bool = True


class ToolBinding(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tool_id: str
    name: Optional[str] = None
    tool_type: Literal["mcp", "script", "api"] = "api"
    permissions: list[str] = Field(default_factory=list)


class AgentConfigTemplate(BaseModel):
    """Agent 配置模板（运行态实例，PRD §4.6.2 核心实例）。"""

    model_config = ConfigDict(from_attributes=True)
    agent_id: str
    agent_name: str
    role_id: str
    department: Optional[str] = None
    level: Optional[str] = None
    system_prompt: str = ""
    skills: list[SkillBinding] = Field(default_factory=list)
    knowledge_bases: list[str] = Field(default_factory=list)
    tools: list[ToolBinding] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    memory_config: MemoryConfig = Field(default_factory=MemoryConfig)
    kpi_ids: list[str] = Field(default_factory=list)
    status: Literal["active", "paused", "shadow", "training"] = "active"


class DepartmentInstance(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    dept_id: str
    name: str
    parent_dept_id: Optional[str] = None
    head_employee_id: Optional[str] = None
    level: int = 0


class RuntimeOrganization(BaseModel):
    """组织运行时。"""

    model_config = ConfigDict(from_attributes=True)
    departments: list[DepartmentInstance] = Field(default_factory=list)
    reporting_tree: dict[str, Any] = Field(default_factory=dict)


class ProcessStep(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    step_id: str
    name: str
    order: int = 0
    approval_required: bool = False
    approver_role: Optional[str] = None
    condition: Optional[str] = None
    next_step_id: Optional[str] = None


class ProcessTrigger(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    trigger_type: Literal["event", "schedule", "manual"] = "manual"
    condition: str = ""


class EscalationRule(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    condition: str = ""
    escalate_to: str = ""
    timeout_hours: int = 48


class ProcessEngineInstance(BaseModel):
    """流程引擎实例（可执行）。"""

    model_config = ConfigDict(from_attributes=True)
    engine_id: str
    process_id: str
    # 流程唯一可读名称（用户界面展示用，避免全部退化为「业务流程 #N」）
    name: str = ""
    process_type: Literal["approval", "collaboration", "business"] = "business"
    steps: list[ProcessStep] = Field(default_factory=list)
    triggers: list[ProcessTrigger] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    escalation_rules: list[EscalationRule] = Field(default_factory=list)


class CollaborationEdge(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_id: str
    target_id: str
    relation: Literal[
        "collaborates_with", "reports_to", "approves_for", "hands_off_to"
    ] = "collaborates_with"
    context: Optional[str] = None


class CollaborationGraph(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[CollaborationEdge] = Field(default_factory=list)


class KnowledgeIndex(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    vector_store_ref: str = ""
    graph_store_ref: str = ""


class ToolRegistryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tool_id: str
    name: str
    tool_type: Literal["mcp", "script", "api"] = "api"
    installed: bool = False
    verified: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class RuntimeCompileResult(BaseModel):
    """WT1 → WT2 核心契约：编译产出的 Enterprise Runtime（spec §10.2）。

    WT2 的 ``runtime_store.save_runtime()`` 接收此结构并持久化。
    ``audit_trail`` 不在此结构中——由 WT2 在持久化时写入 ``runtime_versions`` 审计表。
    """

    model_config = ConfigDict(from_attributes=True)
    version: str
    # spec §10.2 契约：model_version: str（非空，对应的运行模型版本）
    model_version: str
    compiled_at: datetime
    completeness: float = 0.0
    organization: RuntimeOrganization = Field(default_factory=RuntimeOrganization)
    agents: list[AgentConfigTemplate] = Field(default_factory=list)
    process_engines: list[ProcessEngineInstance] = Field(default_factory=list)
    collaboration_graph: CollaborationGraph = Field(default_factory=CollaborationGraph)
    knowledge_index: KnowledgeIndex = Field(default_factory=KnowledgeIndex)
    tool_registry: list[ToolRegistryEntry] = Field(default_factory=list)


# ============================================================
# 响应/请求 schema（§10.7 WT2 API 契约）
# ============================================================


class RuntimeVersionItem(BaseModel):
    """版本列表条目（不含敏感的完整 Runtime 数据）。"""

    model_config = ConfigDict(from_attributes=True)
    version: str
    compiled_at: Optional[datetime] = None
    completeness: float = 0.0
    is_active: bool = False
    model_version: Optional[str] = None
    runtime_id: Optional[str] = None


class RuntimeVersionListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    items: list[RuntimeVersionItem]
    total: int
    limit: int
    offset: int


class RuntimeRollbackRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    target_version: str


class RuntimeRollbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    new_active_version: str
    status: str
    runtime_id: Optional[str] = None


class RuntimeDiffChange(BaseModel):
    """单个差异项。"""

    model_config = ConfigDict(from_attributes=True)
    section: str  # organization / agents / process_engines / ...
    change_type: Literal["added", "removed", "modified"]
    key: str  # 标识符（agent_id / engine_id / tool_id 等）
    detail: Optional[str] = None


class RuntimeDiffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version_a: str
    version_b: str
    changes: list[RuntimeDiffChange]
    summary: str
    a_compiled_at: Optional[datetime] = None
    b_compiled_at: Optional[datetime] = None


class AgentTemplatesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    agents: list[AgentConfigTemplate]


class ProcessEnginesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    processes: list[ProcessEngineInstance]
