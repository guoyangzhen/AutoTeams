/**
 * 类型组织约定（3.5.6 评估结论）：
 * - 本文件存放跨多个页面/组件共享的领域类型（User/Agent/Skill/Conversation 等）。
 * - API 响应类型若仅被单一页面消费，与对应 api/*.ts 客户端共存（如 api/files.ts 的
 *   FileRecord/ConfidentialAccess 仅被 KnowledgePage 使用）。这是有意的"按消费范围聚合"，
 *   避免单个 types 文件膨胀、降低跨文件追溯成本。
 * - 页面专用 UI 类型（如 Settings.tsx 的 SectionId、KnowledgePage.tsx 的 FileType）
 *   保留在页面文件内本地定义。
 * - 判定标准：若一个类型被 ≥2 个页面消费，或属于核心领域模型，则应迁入本文件。
 */
export interface User {
  id: string
  email: string
  name: string
  role: 'admin' | 'member'
  enterprise_id: string | null
  created_at: string
}

export interface Enterprise {
  id: string
  name: string
  invite_token: string | null
  invite_expires_at: string | null
  created_at: string
}

export interface Agent {
  id: string
  name: string
  description: string | null
  enterprise_id: string
  system_prompt: string | null
  file_count: number
  knowledge_count: number
  status: 'processing' | 'ready' | 'error'
  // P1-FE: Agent 配置（知识库参数等）
  config?: Record<string, unknown> | null
  created_at: string
}

export interface Skill {
  id: string
  agent_id: string
  name: string
  description: string | null
  skill_type: string
  input_type: string | null
  output_type: string | null
  config: Record<string, unknown> | null
  // P1-SKILL: 自主 Skill 生态（对齐后端 SkillResponse）
  source?: 'manual' | 'generated' | 'imported'
  status?: 'draft' | 'pending' | 'approved' | 'rejected'
  created_at: string
  updated_at: string
}

export interface Conversation {
  id: string
  agent_id: string
  user_id: string
  title: string | null
  created_at: string
  updated_at: string
  messages?: Message[]
}

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  sources?: Source[] | null
  satisfaction?: string | null
  created_at: string
}

export interface Source {
  content: string
  source: string
  file_type?: string
  distance?: number | null
}

export interface FileItem {
  name: string
  path: string
  size: number
  file_type: string
  extension: string
}

export interface FolderScanResult {
  files: FileItem[]
  total_count: number
  total_size: number
  type_summary: Record<string, number>
}

export type ProcessingStatus =
  | 'pending'
  | 'scanning'
  | 'classifying'
  | 'cleaning'
  | 'extracting'
  | 'vectorizing'
  | 'building'
  | 'completed'
  | 'failed'

export interface ProcessingStep {
  name: string
  status: ProcessingStatus
  progress: number
  started_at?: string
  completed_at?: string
  error?: string
}

export interface ProcessingTask {
  task_id: string
  status: string
  progress: number
  message: string
  total_files: number
  processed_files: number
  failed_files: number
  knowledge_count: number
  processing_time_seconds: number
  folder_path?: string
  agent_id?: string | null
  agent_name?: string | null
  agent_description?: string | null
  started_at?: string | null
  completed_at?: string | null
  error_log?: Array<{
    file?: string
    error?: string
    error_code?: string
    error_type?: string
    timestamp?: string
  }>
  created_at?: string | null
}

export interface SetupSession {
  session_id: string
  message: string
  folder_info?: string
}

export interface SetupMessage {
  session_id: string
  message: string
}

export interface SetupPlan {
  session_id: string
  agent_name?: string
  agent_description?: string
  knowledge_structure?: Record<string, unknown>
  processing_config?: Record<string, unknown>
  estimated_time?: number
  suggested_questions?: string[]
  [key: string]: unknown
}

export interface ApiResponse<T> {
  success: boolean
  data: T
  message?: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  name: string
  email: string
  password: string
}

export interface AuthResponse {
  user: User
  // P1-1 + BE-SEC-01: token 改为 HttpOnly Cookie 下发，响应体中不再包含任何 token
}

// ============================================================================
// WT5 v3 类型定义
//
// 以下类型与 spec.md §10（WT 间接口契约）严格对齐，覆盖 Runtime / Workforce /
// Compilation / Interview / Evolution / Collaboration / Cognition 七大 PRD 概念域。
// 设计原则：
// - 字段名与后端 Pydantic schema 一致（snake_case 保持与 API 契约对齐）
// - 字面量联合类型对应后端 Literal 约束
// - 分页响应统一为 { items: T[]; total: number }
// ============================================================================

// ----------------------------------------------------------------------------
// 通用分页响应（spec.md §10.7 列表端点统一形态）
// ----------------------------------------------------------------------------
export interface PaginatedResponse<T> {
  items: T[]
  total: number
}

// ----------------------------------------------------------------------------
// 一、编译器（Compiler）类型 — 对应 spec.md §10.2 / §10.4 + WT1 API 端点
// ----------------------------------------------------------------------------

/** 五级编译器阶段名称（PRD §4.3） */
export type CompilerStageName =
  | 'information' // 第 1 级：信息编译器
  | 'knowledge' // 第 2 级：知识编译器
  | 'process' // 第 3 级：流程编译器
  | 'capability' // 第 4 级：能力编译器
  | 'runtime' // 第 5 级：运行时编译器

/** 编译任务状态 */
export type CompilationJobStatus =
  /** 历史任务兼容状态；新任务使用 queued。 */
  | 'pending'
  /** 已持久化，等待 Worker 领取。 */
  | 'queued'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled'

/** 编译阶段状态（动画数据，对应 GET /compiler/animation/{job_id}） */
export interface CompilerStage {
  name: CompilerStageName
  status: CompilationJobStatus
  /** 本级"发现了什么"的简要描述 */
  discovered: string
  /**
   * 本级置信度 0-1。
   * 未完成阶段为 null（UI v4 起）—— 区分「未知」与「0%」，
   * 避免把尚未开始的阶段显示成置信度 0。
   */
  confidence: number | null
  /**
   * 本级实际耗时（毫秒），UI v4 新增。
   * 供编译回放按真实时序加速播放五级管道动画。
   */
  duration_ms?: number | null
}

/** 触发编译请求（POST /compiler/compile） */
export interface CompileRequest {
  enterprise_id: string
  trigger_source: 'manual' | 'interview' | 'evolution' | 'data_change'
}

/** 触发编译响应（后端 compile 端点同步返回，附加运行时统计字段） */
export interface CompileResponse {
  job_id: string
  status: CompilationJobStatus
  /** 后端端点附加字段（compile 同步返回时存在） */
  completeness?: number
  level?: string  // 'runnable' / 'basic' / 'incomplete'
  runtime_version?: string
  agent_count?: number
  process_count?: number
}

/** 编译任务详情（GET /compiler/jobs/{job_id}） */
export interface CompilationJob {
  job_id: string
  enterprise_id: string
  stage: CompilerStageName
  status: CompilationJobStatus
  /**
   * 真实编译进度，**0-1 归一化**（UI v4 起由后端 Pipeline 逐级写入）。
   * 此前该字段在后端无对应列恒为 0，前端只能用「已完成阶段/5」估算。
   * 展示时统一走 utils/format 的 formatRatio / toPercentValue，勿自行 ×100。
   */
  progress: number
  confidence: number
  completeness: number
  result?: Record<string, unknown> | null
  error_message?: string | null
  started_at?: string
  completed_at?: string
  error?: string | null
}

/** 编译动画数据（GET /compiler/animation/{job_id}） */
export interface CompilationAnimation {
  stages: CompilerStage[]
}

/** 完成度维度得分（PRD §4.3 完成度评估框架） */
export interface CompletenessDimensions {
  /** 数据覆盖度 30% */
  data_coverage: number
  /** 流程覆盖度 25% */
  process_coverage: number
  /** 角色覆盖度 20% */
  role_coverage: number
  /** 置信度 15% */
  confidence: number
  /** 访谈完成度 10% */
  interview_completion: number
}

/** 完成度评估结果（GET /compiler/completeness/{enterprise_id}） */
export interface CompletenessResult {
  overall: number // 0-100 加权完成度
  dimensions: CompletenessDimensions
  gaps: CompilationGap[]
}

/** 完成度等级（PRD §4.3 结果映射） */
export type CompletenessLevel = 'runnable' | 'basic' | 'incomplete'

/** 增量重编译请求（POST /compiler/recompile） */
export interface RecompileRequest {
  enterprise_id: string
  trigger_source: 'manual' | 'interview' | 'evolution' | 'data_change'
  affected_stages?: CompilerStageName[]
}

// ----------------------------------------------------------------------------
// 二、Runtime 类型 — 对应 spec.md §10.2 + WT2 API 端点
// ----------------------------------------------------------------------------

/** Agent 状态（PRD §4.6.2）— 对齐后端 AgentConfigTemplate.status Literal */
export type AgentStatus = 'active' | 'paused' | 'shadow' | 'training'

/** 技能绑定 */
export interface SkillBinding {
  skill_id: string
  name: string
  enabled: boolean
}

/** 工具绑定 */
export interface ToolBinding {
  tool_id: string
  name: string
  tool_type: 'mcp' | 'script' | 'api'
  permissions: string[]
}

/** 记忆配置（PRD §5.12） */
export interface ShortTermMemoryConfig {
  max_turns: number
}

export interface LongTermMemoryConfig {
  enabled: boolean
  collection_prefix: string
}

export interface EntityMemoryConfig {
  enabled: boolean
  linked_graph: boolean
}

export interface MemoryConfig {
  short_term: ShortTermMemoryConfig
  long_term: LongTermMemoryConfig
  entity_memory: EntityMemoryConfig
}

/** 部门实例 */
export interface DepartmentInstance {
  dept_id: string
  name: string
  parent_dept_id: string | null
  head_employee_id: string | null
  level: number
}

/** 组织运行时 */
export interface RuntimeOrganization {
  departments: DepartmentInstance[]
  reporting_tree: Record<string, unknown>
}

/** Agent 配置模板（运行态实例，spec.md §10.2） */
export interface AgentConfigTemplate {
  agent_id: string
  agent_name: string
  role_id: string
  department: string
  level: string // L1/L2/L3/L4
  system_prompt: string
  skills: SkillBinding[]
  knowledge_bases: string[]
  tools: ToolBinding[]
  permissions: string[]
  memory_config: MemoryConfig
  kpi_ids: string[]
  status: AgentStatus
}

/** 流程触发器 */
export interface ProcessTrigger {
  trigger_type: 'event' | 'schedule' | 'manual'
  condition: string
}

/** 流程升级规则 */
export interface EscalationRule {
  condition: string
  escalate_to: string
  timeout_hours: number
}

/** 流程步骤 */
export interface ProcessStep {
  step_id: string
  name: string
  order: number
  approval_required: boolean
  approver_role: string | null
  condition: string | null
  next_step_id: string | null
}

/** 流程引擎实例 */
export interface ProcessEngineInstance {
  engine_id: string
  process_id: string
  /** 流程唯一可读名称（用户界面展示用） */
  name?: string
  process_type: 'approval' | 'collaboration' | 'business'
  steps: ProcessStep[]
  triggers: ProcessTrigger[]
  participants: string[]
  escalation_rules: EscalationRule[]
}

/** 协作关系图的边 */
export interface CollaborationEdge {
  source_id: string
  target_id: string
  relation: 'collaborates_with' | 'reports_to' | 'approves_for' | 'hands_off_to'
  context: string | null
}

/** 协作关系图 */
export interface CollaborationGraph {
  nodes: Record<string, unknown>[]
  edges: CollaborationEdge[]
}

/** 知识索引引用 */
export interface KnowledgeIndex {
  vector_store_ref: string
  graph_store_ref: string
}

/** 工具注册表条目 */
export interface ToolRegistryEntry {
  tool_id: string
  name: string
  tool_type: 'mcp' | 'script' | 'api'
  installed: boolean
  verified: boolean
  config: Record<string, unknown>
}

/** Enterprise Runtime 编译结果（spec.md §10.2 核心契约） */
export interface RuntimeCompileResult {
  version: string
  model_version: string
  compiled_at: string
  completeness: number
  organization: RuntimeOrganization
  agents: AgentConfigTemplate[]
  process_engines: ProcessEngineInstance[]
  collaboration_graph: CollaborationGraph
  knowledge_index: KnowledgeIndex
  tool_registry: ToolRegistryEntry[]
}

/** Runtime 版本摘要（GET /runtime/{enterprise_id}/versions 列表项）
 *  对齐后端 RuntimeVersionItem：含 model_version / runtime_id 附加字段 */
export interface RuntimeVersionSummary {
  version: string
  compiled_at: string
  completeness: number
  is_active: boolean
  /** 后端附加字段 */
  model_version?: string
  runtime_id?: string
}

/** Runtime 版本 diff 变更项（对齐后端 RuntimeDiffChange：section/key/detail） */
export interface RuntimeDiffChange {
  /** 变更所属区段：organization / agents / process_engines / ... */
  section: string
  /** 变更类型 */
  change_type: 'added' | 'removed' | 'modified'
  /** 标识符（agent_id / engine_id / tool_id 等） */
  key: string
  /** 变更说明 */
  detail?: string | null
}

/** Runtime 版本 diff 结果（GET /runtime/{enterprise_id}/diff） */
export interface RuntimeDiff {
  changes: RuntimeDiffChange[]
  summary: string
  /** 后端附加字段 */
  version_a?: string
  version_b?: string
  a_compiled_at?: string
  b_compiled_at?: string
}

/** 回滚请求（POST /runtime/{enterprise_id}/rollback） */
export interface RollbackRequest {
  target_version: string
}

/** 回滚响应（POST /runtime/{enterprise_id}/rollback）
 *  后端 status 返回 'rolled_back' 表示成功；'failed' 表示失败 */
export interface RollbackResponse {
  new_active_version: string
  status: 'rolled_back' | 'failed'
  /** 后端附加：runtime_id */
  runtime_id?: string
}

// ----------------------------------------------------------------------------
// 三、Workforce 类型 — 对应 spec.md §10.3 / §10.6 + WT3 API 端点
// ----------------------------------------------------------------------------

/** AI 员工生命周期阶段（PRD §5.6）— 对齐后端 LifecycleStageLiteral（7 值） */
export type LifecycleStage =
  | 'recruit'
  | 'training'
  | 'production'
  | 'evaluation'
  | 'continuous_learning'
  | 'promotion'
  | 'retired'

/** 技能需求 */
export interface SkillRequirement {
  skill_name: string
  skill_type: string
  proficiency_level: 'basic' | 'intermediate' | 'advanced'
  source: 'sop' | 'kpi' | 'inferred'
}

/** 知识需求 */
export interface KnowledgeRequirement {
  knowledge_domain: string
  knowledge_base_id: string | null
  coverage: number
}

/** 工具需求 */
export interface ToolRequirement {
  tool_name: string
  tool_type: 'mcp' | 'script' | 'api'
  required_permissions: string[]
}

/** 岗位能力（spec.md §10.3） */
export interface PositionCapability {
  position_id: string
  position_name: string
  department: string
  level: string
  required_skills: SkillRequirement[]
  required_knowledge: KnowledgeRequirement[]
  required_tools: ToolRequirement[]
  required_permissions: string[]
  kpi_ids: string[]
  main_processes: string[]
  priority: 'P0' | 'P1' | 'P2'
}

/** 岗位能力矩阵（WT1 → WT3 契约） */
export interface CapabilityMatrix {
  enterprise_id: string
  positions: PositionCapability[]
  compiled_at: string
  confidence: number
}

/** Workforce 生成请求（POST /workforce/generate） */
export interface WorkforceGenerateRequest {
  enterprise_id: string
}

/** Workforce 生成响应（返回推荐列表） */
export interface WorkforceGenerateResponse {
  recommendations: PositionCapability[]
  total: number
}

/** Workforce 确认请求（POST /workforce/confirm） */
export interface WorkforceConfirmRequest {
  enterprise_id: string
  confirmed_position_ids: string[]
  adjustments?: Record<string, unknown>
}

/** 已创建 Agent 信息（对齐后端 CreatedAgentInfo） */
export interface CreatedAgentInfo {
  agent_id: string
  position_id: string
  position_name: string
  lifecycle_stage: LifecycleStage
}

/** 创建失败信息（对齐后端 FailedAgentInfo） */
export interface FailedAgentInfo {
  position_id: string
  position_name: string
  error: string
}

/** Workforce 确认响应（对齐后端 ConfirmResponse） */
export interface WorkforceConfirmResponse {
  created_agents: CreatedAgentInfo[]
  failed: FailedAgentInfo[]
}

/** 单个 Agent 运行指标（spec.md §10.6） */
export interface AgentRunMetrics {
  agent_id: string
  agent_name: string
  position: string
  lifecycle_stage: LifecycleStage
  tasks_total: number
  tasks_completed: number
  tasks_failed: number
  avg_response_time_ms: number
  avg_satisfaction: number
  kpi_performance: Record<string, number>
  tool_usage: Record<string, number>
  last_active_at: string
}

/** Workforce 运行数据（spec.md §10.6） */
export interface WorkforceRunData {
  enterprise_id: string
  period_start: string
  period_end: string
  agents: AgentRunMetrics[]
  collaboration_events: number
  approval_gates: number
  error_count: number
}

/** 生命周期历史记录项（对齐后端 LifecycleHistoryEntry：transition_reason） */
export interface LifecycleHistoryEntry {
  stage: LifecycleStage
  stage_entered_at: string
  transition_reason: string | null
}

/** 生命周期状态（GET /workforce/{agent_id}/lifecycle） */
export interface LifecycleState {
  stage: LifecycleStage
  stage_entered_at: string
  history: LifecycleHistoryEntry[]
}

/** 阶段转换请求（POST /workforce/{agent_id}/transition） */
export interface LifecycleTransitionRequest {
  target_stage: LifecycleStage
  reason?: string
}

/** 阶段转换响应 */
export interface LifecycleTransitionResponse {
  new_stage: LifecycleStage
  status: 'success' | 'failed'
}

/** Agent 记忆查询结果（GET /workforce/{agent_id}/memory/{conversation_id}） */
export interface AgentMemory {
  short_term: Array<Record<string, unknown>>
  entity: Array<Record<string, unknown>>
  long_term: Array<Record<string, unknown>>
}

// ----------------------------------------------------------------------------
// 四、Evolution 类型 — 对应 WT4 API 端点（/api/v1/evolution/*）
// ----------------------------------------------------------------------------

/** AI 成熟度等级（PRD §5.5） */
export type MaturityLevel = 'L1' | 'L2' | 'L3' | 'L4' | 'L5'

/** Advisor 建议类型（PRD §5.4） */
export type SuggestionType =
  | 'knowledge'
  | 'process'
  | 'capability'
  | 'organization'

/** Advisor 建议状态 */
export type SuggestionStatus = 'pending' | 'applied' | 'rejected'

/** 授权级别（产品完善方案 P1-2「持续演进授权闭环」）
 *  auto —— 自动执行（低风险，系统可直接应用）
 *  authorized —— 需授权（需管理员授权后执行）
 *  approval —— 需审批（高风险变更，需审批后执行） */
export type AuthorizationLevel = 'auto' | 'authorized' | 'approval'

/** Advisor 建议（GET /evolution/{enterprise_id}/suggestions）  *  对齐后端 AdvisorSuggestion：description / impact（后端无 problem_analysis / suggestion / priority） */
export interface AdvisorSuggestion {
  id: string
  enterprise_id: string
  type: SuggestionType
  title: string
  /** 建议详情（对齐后端 description，前端旧字段 suggestion 已弃用） */
  description: string
  /** 预期效果（对齐后端 impact，后端 Optional） */
  impact?: string | null
  status: SuggestionStatus
  /** 后端附加 */
  applied_at?: string
  created_at: string
  /** 授权级别（P1-2 授权闭环）。后端 continuous_optimizer 暂未下发，前端按 type 推断，缺失默认 authorized */
  authorization_level?: AuthorizationLevel
}

/** 应用建议响应（POST /evolution/suggestions/{id}/apply） */
export interface ApplySuggestionResponse {
  applied: boolean
  affected_agents: string[]
  /** 后端附加 */
  suggestion_id?: string
  message?: string
}

/** 拒绝建议请求（POST /evolution/suggestions/{id}/reject） */
export interface RejectSuggestionRequest {
  reason?: string
}

/** 拒绝建议响应 */
export interface RejectSuggestionResponse {
  rejected: boolean
  /** 后端附加 */
  suggestion_id?: string
}

/** 组织分析指标（GET /evolution/{enterprise_id}/metrics）
 *  对齐后端 OrgMetricsResponse + org_analytics 实测键（契约校验 §4.5.2） */
export interface OrgMetrics {
  /** Agent 工作量（对齐后端 _compute_agent_workload：总数/阶段分布/在岗率，非按 Agent 分组） */
  agent_workload: {
    total_agents: number
    stage_distribution: Record<string, number>
    production_rate: number
    unit: string
  }
  process_efficiency: {
    total_events: number
    processed_count: number
    automation_rate: number
    event_type_distribution: Record<string, number>
    unit: string
  }
  /**
   * 工具使用指标（对齐后端 _compute_tool_usage 聚合契约）：
   * total_tools=工具总数 / installed=已安装 / verified=已验证 / install_rate=安装率
   */
  tool_usage: {
    total_tools: number
    installed: number
    verified: number
    install_rate: number
    unit: string
  }
  business_impact: {
    output_value: number
    hours_replaced: number
    cost_saved: number
    /** 自动化投入（透明模型推导：事件数 × 单事件 AI 算力成本） */
    ai_cost?: number
    /** 综合 ROI（后端推导：成本节约 ÷ 自动化投入；避免营收/成本口径错配失真） */
    roi?: number
    /** 后端附加：成交统计（org_analytics 透明推导模型） */
    deal_count?: number
    total_value?: number
    currency?: string
    avg_deal_value?: number
    unit?: string
  }
  maturity_level: MaturityLevel
  /** 指标周期（后端附加） */
  period?: string | null
}

/** Evolution Timeline 事件项（GET /evolution/{enterprise_id}/timeline）
 *  对齐后端 TimelineEntry：timestamp / event_type / summary / details */
export interface EvolutionTimelineItem {
  timestamp: string
  /** 事件类型（对齐后端实际值域；后端为 str，可能新增值，前端按需扩展） */
  event_type:
    | 'suggestion_generated'
    | 'suggestion_applied'
    | 'suggestion_rejected'
    | 'metric_recorded'
    | 'optimization_applied'
    | 'optimization_generated'
    | string
  /** 事件摘要（对齐后端 summary） */
  summary: string
  /** 事件详情（后端 details dict） */
  details?: Record<string, unknown>
  /** 以下字段后端不直接返回，可从 details 提取或由 Mock 提供用于富展示 */
  id?: string
  title?: string
  description?: string
  version?: string
  affected_agent_ids?: string[]
}

// ----------------------------------------------------------------------------
// 五、Evolution Hub 补充类型 —— 对齐 backend/app/schemas/evolution.py
// ----------------------------------------------------------------------------

/** 成熟度评级（GET /evolution/{enterprise_id}/maturity）
 *  level L1..L5 / name 辅助·协作·自动·自治·进化 / description / achieved / dimensions */
export interface MaturityRating {
  level: MaturityLevel
  name: string
  description: string
  /** 是否已达到 MVP 目标等级（L2 协作） */
  achieved: boolean
  /** 评级推导维度：agent_count / automation_rate / human_intervention /
   *  collaboration_events / approval_gates */
  dimensions: Record<string, number>
}

/** 优化历史条目（GET /evolution/{enterprise_id}/optimizations）
 *  type 取值域对齐 continuous_optimizer.OPT_TYPE_*：
 *  feedback / gap / optimization / auto_rollback */
export interface OptimizationHistoryItem {
  id: string
  agent_id: string
  type: string
  applied: boolean
  applied_at?: string | null
  created_at?: string | null
  output_data?: Record<string, unknown> | null
}

/** 优化项应用结果（POST /evolution/{enterprise_id}/optimizations/{agent_id}/apply）
 *  委托 loop_engine.apply_optimization */
export interface ApplyOptimizationResponse {
  applied: boolean
  optimization_id?: string
  version_snapshot_id?: string
  agent_version?: string
  summary?: string
}

/** 回滚检查结果（POST /evolution/{enterprise_id}/rollback-check/{agent_id}）
 *  委托 loop_engine.auto_rollback_if_degraded */
export interface RollbackCheckResult {
  triggered: boolean
  reason?: string
  avg_scores?: Record<string, number>
  rollback_version_id?: string | null
}

// ----------------------------------------------------------------------------
// 六、Interview 类型 — 对应 WT4 API 端点（/api/v1/interview/*）

/** 访谈问题分类（PRD §5.7 七大类）— 对齐后端 CategoryLiteral */
export type InterviewCategory =
  | 'sales' // 销售流程
  | 'customer_service' // 客户服务
  | 'procurement' // 采购与供应链
  | 'finance' // 财务与费用
  | 'hr' // 人事与组织
  | 'data' // 数据与权限（对齐后端 "data"）
  | 'kpi' // KPI 与目标

/** 访谈问题优先级 */
export type InterviewPriority = 'P0' | 'P1' | 'P2'

/** 访谈会话状态 — 对齐后端 SessionStatusLiteral（无 'abandoned'） */
export type InterviewSessionStatus = 'active' | 'completed'

/** 访谈会话（POST /interview/sessions 响应 + GET /interview/sessions/{id} 状态）
 *  StartSessionResponse 仅含 session_id/status/total_count；SessionStatusResponse 含 answered_count/completeness */
export interface InterviewSession {
  session_id: string
  status: InterviewSessionStatus
  total_count: number
  /** 仅 SessionStatusResponse 返回；StartSessionResponse 不含 */
  answered_count?: number
  completeness?: number // 0-100
  /** 前端从请求带入，后端不返回 */
  enterprise_id?: string
  created_at?: string
}

/** 访谈问题（GET /interview/sessions/{id}/next-question） */
export interface InterviewQuestion {
  question_id: string
  category: InterviewCategory
  question: string
  priority: InterviewPriority
  /** 后端附加 */
  affected_field?: string
}

/** 提交访谈回答请求（POST /interview/sessions/{id}/answers） */
export interface InterviewAnswerRequest {
  question_id: string
  answer: string
}

/** 提交访谈回答响应 */
export interface InterviewAnswerResponse {
  updated_completeness: number
  next_question?: InterviewQuestion | null
  /** 后端附加 */
  recompile_triggered?: boolean
}

// ----------------------------------------------------------------------------
// 六、Collaboration 类型 — 对应 WT4 API 端点（/api/v1/collaboration/*）
// ----------------------------------------------------------------------------

/** 协作事件类型（与后端 CollaborationEventTypeLiteral 统一） */
export type CollaborationEventType =
  | 'inquiry_received' // 收到询盘
  | 'product_query' // 产品参数查询
  | 'quotation_generated' // 销售报价
  | 'approval_submitted' // 财务审核
  | 'approval_approved' // 审批通过
  | 'order_synced' // 客服同步
  | 'after_sales' // 售后接管
  | 'handoff' // 转交
  | 'escalation' // 升级
  | 'error' // 错误
  // 后端 demo/seed 扩展事件类型
  | 'opportunity_created' // 商机创建
  | 'approval_flow_created' // 审批流创建
  | 'deal_closed' // 成交

/** 协作事件（GET /collaboration/events）— event_id 对齐后端 CollaborationEvent.event_id */
export interface CollaborationEvent {
  event_id: string
  enterprise_id: string
  event_type: CollaborationEventType
  payload: Record<string, unknown>
  source_agent_id: string | null
  target_agent_id: string | null
  /** 后端附加：事件状态 */
  status?: 'pending' | 'processed' | 'failed'
  created_at: string
}

/** 发布事件请求（POST /collaboration/events）— enterprise_id 后端必填 */
export interface PublishEventRequest {
  /** 企业 ID（后端必填） */
  enterprise_id: string
  event_type: CollaborationEventType
  payload: Record<string, unknown>
  source_agent_id?: string
  /** 后端附加 */
  target_agent_id?: string
}

/** 发布事件响应 — status 对齐后端 EventStatusLiteral（'processed'，非 'published'） */
export interface PublishEventResponse {
  event_id: string
  status: 'processed' | 'pending' | 'failed'
}

/** 审批请求（前端 UI 派生模型 + 后端 ApprovalGateView 字段）
 *  前端 getPendingApprovals 从事件流构造 UI 派生字段（title/description/requester_* 等）；
 *  后端 GET /collaboration/approvals/{id} 返回 ApprovalGateView（process_id/node_id/agent_id/approver_id/decided_at）。 */
export interface ApprovalRequest {
  id: string
  enterprise_id: string
  /** 审批标题（前端 UI 派生） */
  title: string
  /** 审批描述（前端 UI 派生） */
  description: string
  /** 申请人（Agent ID 或人类）（前端 UI 派生） */
  requester_id: string
  requester_name: string
  /** 审批类型（前端 UI 派生） */
  approval_type: 'quotation' | 'expense' | 'process' | 'other'
  /** 金额（如适用）（前端 UI 派生） */
  amount?: number
  /** 业务上下文（前端 UI 派生） */
  context: Record<string, unknown>
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  /** 后端 ApprovalGateView 字段（GET /collaboration/approvals/{id} 直接返回时存在） */
  process_id?: string
  node_id?: string
  agent_id?: string | null
  approver_id?: string | null
  decided_at?: string | null
  /** 后端 list_approvals 附加的用户友好字段 */
  agent_name?: string
  node_label?: string
}

/** 审批通过响应（POST /collaboration/approvals/{id}/approve） */
export interface ApprovalApproveResponse {
  approved: boolean
  process_resumed: boolean
}

/** 审批拒绝请求（POST /collaboration/approvals/{id}/reject） */
export interface ApprovalRejectRequest {
  reason: string
}

/** 审批拒绝响应 */
export interface ApprovalRejectResponse {
  rejected: boolean
}

/** 回滚请求（POST /collaboration/rollback） */
export interface CollaborationRollbackRequest {
  snapshot_id: string
}

/** 回滚响应（POST /collaboration/rollback）— 对齐后端 RollbackResponse */
export interface CollaborationRollbackResponse {
  rolled_back: boolean
  agent_id: string
  /** 后端附加 */
  operation?: string
  snapshot_id?: string
}

// ----------------------------------------------------------------------------
// 影子模式类型 — 对应 WT1/WT4 影子模式端点（/api/v1/shadow/*）
// ----------------------------------------------------------------------------

/** 影子任务状态（状态机：shadowing → evaluating → qualified → autonomous） */
export type ShadowStatus = 'shadowing' | 'evaluating' | 'qualified' | 'autonomous'

/** 影子任务评估结果 */
export type ShadowEvalResult = 'pending' | 'match' | 'mismatch'

/** 影子任务视图（对齐后端 ShadowTaskView） */
export interface ShadowTask {
  id: string
  enterprise_id: string
  agent_id: string | null
  task_type: string
  question: string
  human_answer: string | null
  ai_answer: string | null
  confidence: number | null
  status: ShadowStatus
  eval_result: ShadowEvalResult
  promoted_at: string | null
  created_at: string
  updated_at: string
}

/** 影子模式阶段汇总（对齐后端 ShadowStageSummary） */
export interface ShadowStageSummary {
  status: ShadowStatus
  count: number
  match_count: number
  mismatch_count: number
  avg_confidence: number | null
}

/** 影子模式汇总（对齐后端 ShadowSummaryResponse） */
export interface ShadowSummary {
  stages: ShadowStageSummary[]
  autonomous_count: number
  total_count: number
}

/** 创建影子任务请求（POST /shadow/tasks） */
export interface CreateShadowTaskRequest {
  enterprise_id: string
  agent_id?: string | null
  task_type: string
  question: string
  human_answer?: string | null
}

/** 记录 AI 回答请求（POST /shadow/tasks/{id}/record-ai） */
export interface RecordAiAnswerRequest {
  ai_answer: string
  confidence?: number | null
}

// ----------------------------------------------------------------------------
// 双盲反事实影子评估类型 — 对应 /api/v1/shadow/counterfactual/*（战役 4）
// ----------------------------------------------------------------------------

/** 差分维度（瀑布纵轴顺序） */
export type DiffDimension = 'semantics' | 'latency' | 'cost' | 'risk'

/** 差分结论：better = 数字员工占优；parity = 持平；worse = 数字员工落后 */
export type DiffAssessment = 'better' | 'parity' | 'worse'

/** 反事实差分明细（对齐后端 CounterfactualDiffView） */
export interface CounterfactualDiff {
  diff_id: string
  session_id: string
  dimension: DiffDimension
  human_value: string
  agent_value: string
  /** 归一化差值：正数表示数字员工占优 */
  delta_score: number
  assessment: DiffAssessment
  note: string
  created_at: string
}

/** 单笔反事实裁决（对齐后端 CounterfactualVerdictView） */
export interface CounterfactualVerdict {
  is_qualified: boolean
  semantic_alignment_score: number
  time_saving_seconds: number
  cost_delta_yuan: number
  expected_net_benefit_yuan: number
  guardrail_breach_count: number
  reasons: string[]
}

/** 免干预转正裁决（对齐后端 PromotionDecisionView） */
export interface PromotionDecision {
  is_auto_promoted: boolean
  consecutive_pass_streak: number
  required_streak: number
  remaining_to_promotion: number
  hit_threshold: boolean
}

/** 反事实评估会话（对齐后端 CounterfactualSessionView） */
export interface CounterfactualSession {
  session_id: string
  enterprise_id: string
  employee_badge: string
  scenario: string
  human_action_snapshot: string
  agent_proposal_snapshot: string
  semantic_alignment_score: number
  time_saving_seconds: number
  cost_delta_yuan: number
  expected_net_benefit_yuan: number
  human_duration_seconds: number
  agent_duration_seconds: number
  human_cost_yuan: number
  agent_cost_yuan: number
  guardrail_breach_count: number
  is_qualified: boolean
  consecutive_pass_streak: number
  is_auto_promoted: boolean
  promoted_at: string | null
  created_at: string
  updated_at: string
  diffs: CounterfactualDiff[]
  verdict: CounterfactualVerdict | null
  promotion: PromotionDecision | null
}

/** 免干预转正准入看板（对齐后端 PromotionGateView） */
export interface PromotionGate {
  enterprise_id: string
  employee_badge: string
  consecutive_pass_streak: number
  required_streak: number
  remaining_to_promotion: number
  is_auto_promoted: boolean
  total_sessions: number
  qualified_sessions: number
  semantic_alignment_threshold: number
  max_cost_delta_yuan: number
  max_guardrail_breaches: number
}

/** 提交反事实推演请求（POST /shadow/counterfactual/sessions） */
export interface CreateCounterfactualSessionRequest {
  enterprise_id: string
  employee_badge: string
  scenario: string
  human_action_snapshot: string
  agent_proposal_snapshot: string
  human_duration_seconds: number
  agent_duration_seconds: number
  human_cost_yuan: number
  agent_cost_yuan: number
  guardrail_breach_count?: number
  required_streak?: number | null
  hourly_labor_rate_yuan?: number
}

/** 重放裁决响应（POST /shadow/counterfactual/sessions/{id}/replay） */
export interface ReplayResult {
  verdict: CounterfactualVerdict
  diffs: Array<Omit<CounterfactualDiff, 'diff_id' | 'session_id' | 'created_at'>>
}

// ----------------------------------------------------------------------------
// 七、Cognition 类型 — 对应 WT1 API 端点（/api/v1/cognition/*）
// ----------------------------------------------------------------------------

/** 知识图谱实体类型（13 类，PRD §4.2 / §4.3） */
export type GraphEntityType =
  | 'department' // 部门
  | 'role' // 岗位
  | 'employee' // 员工
  | 'product' // 产品
  | 'customer' // 客户
  | 'opportunity' // 商机
  | 'order' // 订单
  | 'sop' // SOP 流程
  | 'approval_flow' // 审批流
  | 'kpi' // KPI
  | 'permission' // 权限
  | 'tool' // 工具
  | 'knowledge_base' // 知识库

/** 知识图谱关系类型（8 类，与后端 RelationType 枚举值 + PRD §4.2 对齐） */
export type GraphRelationType =
  | 'belongs_to' // 属于（Role → Department）
  | 'reports_to' // 汇报给（Employee → Employee）
  | 'executes' // 执行（Role → Process）
  | 'owes' // 承担（Role → KPI）
  | 'has_permission' // 拥有权限（Role → Permission）
  | 'uses' // 使用（Process → System）
  | 'produces' // 产出（Process → Knowledge）
  | 'serves' // 服务（Customer → Product）

/** 知识图谱节点（对齐后端 schemas/compiler.py:GraphNode：node_id/name/node_type/attributes） */
export interface GraphNode {
  /** 节点 ID（对齐后端 node_id） */
  node_id: string
  /** 显示名（对齐后端 name） */
  name: string
  /** 实体类型（对齐后端 node_type；后端为 str，前端用字面量约束） */
  node_type: GraphEntityType
  /** 附加属性（对齐后端 attributes） */
  attributes: Record<string, unknown>
  /** 置信度 0-1 */
  confidence: number
}

/** 知识图谱边（对齐后端 schemas/compiler.py:GraphEdge：source_id/target_id/relation/attributes）
 *  后端无 id 字段；前端渲染时使用复合键 `${source_id}-${target_id}-${relation}`。 */
export interface GraphEdge {
  /** 起点 ID（对齐后端 source_id） */
  source_id: string
  /** 终点 ID（对齐后端 target_id） */
  target_id: string
  relation: GraphRelationType
  /** 附加属性（对齐后端 attributes） */
  attributes: Record<string, unknown>
}

/** 知识图谱（GET /cognition/knowledge-graph/{enterprise_id}）
 *  对齐后端 KnowledgeGraphResponse：enterprise_id / nodes / edges / version */
export interface KnowledgeGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** 后端附加字段 */
  enterprise_id?: string
  version?: string
}

// ----------------------------------------------------------------------------
// 企业画像子结构（对齐后端 schemas/cognition.py:EnterpriseProfileData 嵌套结构）
// ----------------------------------------------------------------------------

/** 企业基本信息（对齐后端 EnterpriseBasic） */
export interface EnterpriseBasic {
  name: string
  industry: string
  scale: string
  revenue: string
  location: string
  founded: string
}

/** 组织概览（对齐后端 EnterpriseOrgSummary） */
export interface EnterpriseOrgSummary {
  department_count: number
  headcount: number
  key_roles: string[]
}

/** AI 成熟度评级（对齐后端 EnterpriseMaturity） */
export interface EnterpriseMaturity {
  level: string  // L1-L5
  automation_coverage: number
  ai_workforce_count: number
}

/** 业务概览（对齐后端 EnterpriseBusiness） */
export interface EnterpriseBusiness {
  main_products: string[]
  target_industries: string[]
  core_processes: string[]
}

/** 企业画像完整数据（对齐后端 EnterpriseProfileData） */
export interface EnterpriseProfileData {
  basic: EnterpriseBasic
  tags: string[]
  org_summary: EnterpriseOrgSummary
  maturity: EnterpriseMaturity
  business: EnterpriseBusiness
  gaps: string[]
  version: string
  updated_at?: string
  completeness_score: number
}

/** 企业画像（GET /cognition/profile/{enterprise_id}）
 *  对齐后端 EnterpriseProfileResponse：enterprise_id + profile（嵌套结构） */
export interface EnterpriseProfile {
  enterprise_id: string
  profile: EnterpriseProfileData
}

// ----------------------------------------------------------------------------
// 运行模型子结构（对齐后端 schemas/cognition.py:EnterpriseOperatingModelData 强结构）
// ----------------------------------------------------------------------------

/** 岗位定义（对齐后端 RoleDefinition） */
export interface RoleDefinition {
  id: string
  title: string
  department: string
  level: string
  responsibilities: string[]
  required_skills: string[]
  kpi_ids: string[]
  permission_ids: string[]
}

/** 流程定义（对齐后端 ProcessDefinitionModel） */
export interface ProcessDefinitionModel {
  id: string
  name: string
  type: string
  steps: Record<string, unknown>[]
  owner_role_id: string
  participants: string[]
  trigger_event: string
  system_ids: string[]
}

/** 岗位能力项（对齐后端 CapabilityItem） */
export interface CapabilityItem {
  role_id: string
  required_capabilities: string[]
  knowledge_sources: string[]
  tools: string[]
}

/** 运行规则（对齐后端 RuntimeRules） */
export interface RuntimeRules {
  collaboration_rules: Record<string, unknown>[]
  data_flow_rules: Record<string, unknown>[]
  escalation_rules: Record<string, unknown>[]
}

/** 知识空白项（对齐后端 GapItem） */
export interface GapItem {
  area: string
  severity: string
  suggestion: string
}

/** 企业运行模型完整数据（对齐后端 EnterpriseOperatingModelData） */
export interface EnterpriseOperatingModelData {
  version: string
  completeness: number
  organization: Record<string, unknown>
  roles: RoleDefinition[]
  processes: ProcessDefinitionModel[]
  capabilities: CapabilityItem[]
  runtime_rules: RuntimeRules
  gaps: GapItem[]
}

/** 运行模型（GET /cognition/operating-model/{enterprise_id}）
 *  对齐后端 OperatingModelResponse：enterprise_id + model（强结构）+ version */
export interface OperatingModel {
  enterprise_id: string
  model: EnterpriseOperatingModelData
  version: string
}

// ----------------------------------------------------------------------------
// 八、编译缺失项（spec.md §10.4）— WT1 → WT4 契约
// ----------------------------------------------------------------------------

/** 编译缺失项类型 */
export type CompilationGapType = 'data' | 'process' | 'role' | 'knowledge' | 'tool'

/** 单个缺失项（spec.md §10.4） */
export interface CompilationGap {
  gap_type: CompilationGapType
  description: string
  affected_positions: string[]
  impact_on_completeness: number
  suggestion: string
}

/** 编译缺失项清单（spec.md §10.4） */
export interface CompilationGaps {
  enterprise_id: string
  overall_completeness: number
  dimension_scores: Record<string, number>
  gaps: CompilationGap[]
  compiled_at: string
}
