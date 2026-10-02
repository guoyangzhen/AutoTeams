"""编译器 schema —— WT 间接口契约（spec.md §10.2/§10.3/§10.4）+ API 端点 schema。

本文件定义了 WT1 向其他 WorkTree 输出的核心数据契约，其他 WT 依赖这些 schema：
- WT1 → WT2（spec.md §10.2）：RuntimeCompileResult —— runtime_compiler.py 的输出
- WT1 → WT3（spec.md §10.3）：CapabilityMatrix —— capability_compiler.py 的输出
- WT1 → WT4（spec.md §10.4）：CompilationGaps —— completeness.py 的输出
- WT1 → WT5（spec.md §10.7）：API 端点请求/响应 schema

注意：所有 schema 严格遵循 spec.md §10 的字段定义，变更需走 §10.9 契约变更流程。
"""
from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# §10.2 WT1 → WT2：RuntimeCompileResult 契约
# ============================================================================

class ShortTermMemoryConfig(BaseModel):
    """短期记忆配置。"""
    model_config = ConfigDict(from_attributes=True)
    max_turns: int = 20


class LongTermMemoryConfig(BaseModel):
    """长期记忆配置。"""
    model_config = ConfigDict(from_attributes=True)
    enabled: bool = True
    collection_prefix: str = "memory_lt"


class EntityMemoryConfig(BaseModel):
    """实体记忆配置。"""
    model_config = ConfigDict(from_attributes=True)
    enabled: bool = True
    linked_graph: bool = True


class MemoryConfig(BaseModel):
    """Agent 记忆配置（PRD §5.12 三层记忆架构）。"""
    model_config = ConfigDict(from_attributes=True)
    short_term: ShortTermMemoryConfig = Field(default_factory=ShortTermMemoryConfig)
    long_term: LongTermMemoryConfig = Field(default_factory=LongTermMemoryConfig)
    entity_memory: EntityMemoryConfig = Field(default_factory=EntityMemoryConfig)


class SkillBinding(BaseModel):
    """技能绑定。"""
    model_config = ConfigDict(from_attributes=True)
    skill_id: str
    name: str
    enabled: bool = True


class ToolBinding(BaseModel):
    """工具绑定。"""
    model_config = ConfigDict(from_attributes=True)
    tool_id: str
    name: str
    tool_type: str = "api"  # mcp / script / api
    permissions: list[str] = Field(default_factory=list)


class AgentConfigTemplate(BaseModel):
    """Agent 配置模板（运行态实例，PRD §4.6.2）。

    由 Runtime Compiler 生成，WT2 持久化，WT3 Workforce 生成器消费。
    """
    model_config = ConfigDict(from_attributes=True)
    agent_id: str
    agent_name: str
    role_id: str
    department: str = ""
    level: str = ""  # L1/L2/L3
    system_prompt: str = ""
    skills: list[SkillBinding] = Field(default_factory=list)
    knowledge_bases: list[str] = Field(default_factory=list)
    tools: list[ToolBinding] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    memory_config: MemoryConfig = Field(default_factory=MemoryConfig)
    kpi_ids: list[str] = Field(default_factory=list)
    status: str = "training"  # active/paused/shadow/training


class DepartmentInstance(BaseModel):
    """部门实例。"""
    model_config = ConfigDict(from_attributes=True)
    dept_id: str
    name: str
    parent_dept_id: Optional[str] = None
    head_employee_id: Optional[str] = None
    level: int = 1


class RuntimeOrganization(BaseModel):
    """组织运行时。"""
    model_config = ConfigDict(from_attributes=True)
    departments: list[DepartmentInstance] = Field(default_factory=list)
    reporting_tree: dict = Field(default_factory=dict)


class ProcessStep(BaseModel):
    """流程步骤。"""
    model_config = ConfigDict(from_attributes=True)
    step_id: str
    name: str
    order: int
    approval_required: bool = False
    approver_role: Optional[str] = None
    condition: Optional[str] = None
    next_step_id: Optional[str] = None


class ProcessTrigger(BaseModel):
    """流程触发条件。"""
    model_config = ConfigDict(from_attributes=True)
    trigger_type: str = "manual"  # event/schedule/manual
    condition: str = ""


class EscalationRule(BaseModel):
    """升级规则。"""
    model_config = ConfigDict(from_attributes=True)
    condition: str
    escalate_to: str
    timeout_hours: int = 48


class ProcessEngineInstance(BaseModel):
    """流程引擎实例（可执行）。"""
    model_config = ConfigDict(from_attributes=True)
    engine_id: str
    process_id: str
    process_type: str = "business"  # approval/collaboration/business
    steps: list[ProcessStep] = Field(default_factory=list)
    triggers: list[ProcessTrigger] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    escalation_rules: list[EscalationRule] = Field(default_factory=list)


class CollaborationEdge(BaseModel):
    """协作关系图的边。"""
    model_config = ConfigDict(from_attributes=True)
    source_id: str
    target_id: str
    relation: str = "collaborates_with"  # collaborates_with/reports_to/approves_for/hands_off_to
    context: Optional[str] = None


class CollaborationGraph(BaseModel):
    """协作关系图。"""
    model_config = ConfigDict(from_attributes=True)
    nodes: list[dict] = Field(default_factory=list)
    edges: list[CollaborationEdge] = Field(default_factory=list)


class KnowledgeIndex(BaseModel):
    """知识索引。"""
    model_config = ConfigDict(from_attributes=True)
    vector_store_ref: str = ""
    graph_store_ref: str = ""


class ToolRegistryEntry(BaseModel):
    """工具注册表条目。"""
    model_config = ConfigDict(from_attributes=True)
    tool_id: str
    name: str
    tool_type: str = "api"  # mcp/script/api
    installed: bool = False
    verified: bool = False
    config: dict = Field(default_factory=dict)


class RuntimeCompileResult(BaseModel):
    """WT1 → WT2 核心契约：编译产出的 Enterprise Runtime（spec.md §10.2）。

    WT1 的 runtime_compiler.py 产出此结构，WT2 的 runtime_store.save_runtime() 持久化。
    audit_trail 由 WT2 在持久化时追加。
    """
    model_config = ConfigDict(from_attributes=True)
    version: str = "v1.0.0"
    model_version: str = "v1.0.0"
    compiled_at: datetime
    completeness: float = 0.0
    organization: RuntimeOrganization = Field(default_factory=RuntimeOrganization)
    agents: list[AgentConfigTemplate] = Field(default_factory=list)
    process_engines: list[ProcessEngineInstance] = Field(default_factory=list)
    collaboration_graph: CollaborationGraph = Field(default_factory=CollaborationGraph)
    knowledge_index: KnowledgeIndex = Field(default_factory=KnowledgeIndex)
    tool_registry: list[ToolRegistryEntry] = Field(default_factory=list)


# ============================================================================
# §10.3 WT1 → WT3：CapabilityMatrix 契约
# ============================================================================

class SkillRequirement(BaseModel):
    """技能需求。"""
    model_config = ConfigDict(from_attributes=True)
    skill_name: str
    skill_type: str = ""
    proficiency_level: str = "basic"  # basic/intermediate/advanced
    source: str = "sop"  # sop/kpi/inferred


class KnowledgeRequirement(BaseModel):
    """知识需求。"""
    model_config = ConfigDict(from_attributes=True)
    knowledge_domain: str
    knowledge_base_id: Optional[str] = None
    coverage: float = 0.0


class ToolRequirement(BaseModel):
    """工具需求。"""
    model_config = ConfigDict(from_attributes=True)
    tool_name: str
    tool_type: str = "api"  # mcp/script/api
    required_permissions: list[str] = Field(default_factory=list)


class PositionCapability(BaseModel):
    """单个岗位的能力矩阵。"""
    model_config = ConfigDict(from_attributes=True)
    position_id: str
    position_name: str
    department: str = ""
    level: str = ""  # L1/L2/L3
    required_skills: list[SkillRequirement] = Field(default_factory=list)
    required_knowledge: list[KnowledgeRequirement] = Field(default_factory=list)
    required_tools: list[ToolRequirement] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    kpi_ids: list[str] = Field(default_factory=list)
    main_processes: list[str] = Field(default_factory=list)
    priority: str = "P1"  # P0/P1/P2


class CapabilityMatrix(BaseModel):
    """WT1 → WT3 契约：岗位能力矩阵（spec.md §10.3）。

    WT1 的 capability_compiler.py 产出此结构，
    WT3 的 workforce.generator.generate() 消费。
    """
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    positions: list[PositionCapability] = Field(default_factory=list)
    compiled_at: datetime
    confidence: float = 0.0


# ============================================================================
# §10.4 WT1 → WT4：CompilationGaps 契约
# ============================================================================

class CompilationGap(BaseModel):
    """单个缺失项。"""
    model_config = ConfigDict(from_attributes=True)
    gap_type: Literal["data", "process", "role", "knowledge", "tool"] = "data"
    description: str
    affected_positions: list[str] = Field(default_factory=list)
    impact_on_completeness: float = 0.0
    suggestion: str = ""


class CompilationGaps(BaseModel):
    """WT1 → WT4 契约：编译缺失项（spec.md §10.4）。

    WT1 的 completeness.py 产出此结构，
    WT4 的 evolution.advisor.generate_suggestions() 消费。
    """
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    overall_completeness: float = 0.0
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    gaps: list[CompilationGap] = Field(default_factory=list)
    compiled_at: datetime


# ============================================================================
# 编译器内部 schema（五级编译器输入/输出）
# ============================================================================

class InformationEntry(BaseModel):
    """Information Compiler 产出的单条结构化信息条目。"""
    model_config = ConfigDict(from_attributes=True)
    entry_id: str
    entry_type: str  # department/role/employee/product/customer/process/kpi/permission/system/knowledge
    name: str
    attributes: dict = Field(default_factory=dict)
    source_file: str = ""
    file_type: str = ""  # csv/markdown/text
    confidence: float = 0.0


class InformationCompileOutput(BaseModel):
    """Information Compiler 输出。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    entries: list[InformationEntry] = Field(default_factory=list)
    total_files: int = 0
    confidence: float = 0.0


# P2 字段对齐: 知识图谱 8 类关系（与 RelationType 枚举值 + 前端 GraphRelationType 对齐）
GraphRelationTypeLiteral = Literal[
    "belongs_to", "reports_to", "executes", "owes",
    "has_permission", "uses", "produces", "serves",
]


class GraphNode(BaseModel):
    """知识图谱节点。"""
    model_config = ConfigDict(from_attributes=True)
    node_id: str
    node_type: str  # 13 类实体之一
    name: str
    attributes: dict = Field(default_factory=dict)
    confidence: float = 0.0


class GraphEdge(BaseModel):
    """知识图谱边。"""
    model_config = ConfigDict(from_attributes=True)
    source_id: str
    target_id: str
    relation: GraphRelationTypeLiteral
    attributes: dict = Field(default_factory=dict)


class KnowledgeCompileOutput(BaseModel):
    """Knowledge Compiler 输出。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    vector_collection: str = ""
    confidence: float = 0.0


class ProcessDefinition(BaseModel):
    """流程定义（Process Compiler 输出的单条流程）。"""
    model_config = ConfigDict(from_attributes=True)
    process_id: str
    name: str
    process_type: str = "sop"  # sop/approval/collaboration
    steps: list[dict] = Field(default_factory=list)
    owner_role_id: str = ""
    participants: list[str] = Field(default_factory=list)
    trigger_event: str = ""
    system_ids: list[str] = Field(default_factory=list)
    kpi_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ProcessCompileOutput(BaseModel):
    """Process Compiler 输出。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    processes: list[ProcessDefinition] = Field(default_factory=list)
    confidence: float = 0.0


class CapabilityCompileOutput(BaseModel):
    """Capability Compiler 输出（即 CapabilityMatrix 的内部表示）。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    capability_matrix: CapabilityMatrix


class RuntimeCompileOutput(BaseModel):
    """Runtime Compiler 输出（即 RuntimeCompileResult）。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    runtime: RuntimeCompileResult


class CompletenessDimension(BaseModel):
    """完成度单维度得分。"""
    model_config = ConfigDict(from_attributes=True)
    dimension: str
    score: float
    weight: float
    description: str = ""


class CompletenessResult(BaseModel):
    """完成度评估结果。"""
    model_config = ConfigDict(from_attributes=True)
    overall: float = 0.0
    dimensions: dict[str, float] = Field(default_factory=dict)
    level: str = "incomplete"  # runnable/basic/incomplete
    gaps: list[CompilationGap] = Field(default_factory=list)


# ============================================================================
# §10.7 API 端点 schema
# ============================================================================

class CompileRequest(BaseModel):
    """触发五级编译请求。"""
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    trigger_source: Literal["manual", "interview", "evolution", "data_change"] = "manual"


class CompileResponse(BaseModel):
    """触发编译响应。"""
    model_config = ConfigDict(from_attributes=True)
    job_id: str
    status: str


class RecompileRequest(BaseModel):
    """增量重编译请求。

    ``folder_path`` 是可选输入快照；省略时服务端只复用该企业最近一次已完成编译的
    已持久化目录，不再创建无法由 Worker 执行的 pending 任务。
    """
    model_config = ConfigDict(from_attributes=True)
    enterprise_id: str
    trigger_source: str = "file_change"
    affected_stages: Optional[list[str]] = None
    folder_path: Optional[str] = Field(default=None, max_length=4096)


class CompilationJobResponse(BaseModel):
    """编译任务详情。"""
    job_id: str
    enterprise_id: str
    stage: Literal["information", "knowledge", "process", "capability", "runtime"]
    # 兼容历史 pending，并覆盖耐久队列的 queued / cancelled 生命周期状态。
    status: Literal["pending", "queued", "running", "paused", "completed", "failed", "cancelled"]

    progress: float = 0.0
    confidence: float = 0.0
    completeness: float = 0.0
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CompilationJobListResponse(BaseModel):
    """编译任务列表。"""
    model_config = ConfigDict(from_attributes=True)
    items: list[CompilationJobResponse]
    total: int


class CompletenessResponse(BaseModel):
    """完成度查询响应。"""
    model_config = ConfigDict(from_attributes=True)
    overall: float
    dimensions: dict[str, float]
    level: str
    gaps: list[CompilationGap]


class AnimationStage(BaseModel):
    """编译动画单级数据。"""
    model_config = ConfigDict(from_attributes=True)
    name: str
    # pending/running/completed/failed —— failed 用于区分「编译失败的阶段」
    # 与「尚未开始的阶段」（此前二者都渲染为灰圈，视觉无法区分）
    status: str = "pending"
    discovered: str = ""
    # 未完成阶段无置信度，用 None 而非 0.0，避免前端把「未知」误显示为「0%」
    confidence: Optional[float] = None
    # 该级实际耗时（毫秒），供回放模式按真实时序播放
    duration_ms: Optional[int] = None


class AnimationResponse(BaseModel):
    """编译动画数据响应。"""
    model_config = ConfigDict(from_attributes=True)
    stages: list[AnimationStage]
