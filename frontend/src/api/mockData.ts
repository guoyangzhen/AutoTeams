/**
 * WT5 共享 Mock 数据 — 基于 PRD §9 示例企业"智链物联"。
 *
 * 所有 v3 API 客户端在 VITE_USE_MOCK=true 时引用本文件，确保前端独立于后端开发。
 * 数据与 spec.md §10 契约字段对齐，覆盖 7 步演示案例与 SOP v1→v2 进化场景。
 *
 * 注意：本文件仅用于前端独立开发，后端 API 就绪后由各客户端切换为真实调用。
 */

import type {
  RuntimeCompileResult,
  RuntimeVersionSummary,
  RuntimeDiff,
  AgentRunMetrics,
  AdvisorSuggestion,
  OrgMetrics,
  EvolutionTimelineItem,
  InterviewQuestion,
  InterviewSession,
  CollaborationEvent,
  ApprovalRequest,
  KnowledgeGraph,
  EnterpriseProfile,
  OperatingModel,
  CompletenessResult,
  CompilationJob,
  CompilationAnimation,
  WorkforceGenerateResponse,
  WorkforceConfirmResponse,
  LifecycleStage,
  LifecycleState,
  AgentMemory,
} from '@/types'

/** Mock 模拟网络延迟，使加载态可被观察到 */
export function mockDelay<T>(data: T, ms = 300): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(data), ms)
  })
}

export const MOCK_ENTERPRISE_ID = 'ent-zhilian-001'

// ----------------------------------------------------------------------------
// 编译器 Mock 数据
// ----------------------------------------------------------------------------

export const mockCompilationAnimation: CompilationAnimation = {
  stages: [
    {
      name: 'information',
      status: 'completed',
      discovered: '已识别 30 个文件，分类为组织架构、销售 SOP、产品规格、CRM 数据、KPI 矩阵、权限矩阵 6 大类',
      confidence: 0.85,
    },
    {
      name: 'knowledge',
      status: 'completed',
      discovered: '构建知识图谱：识别 13 类实体（部门/岗位/产品/客户/商机等）与 8 类关系，建立语义索引',
      confidence: 0.8,
    },
    {
      name: 'process',
      status: 'completed',
      discovered: '提取 4 条核心业务流程：销售 SOP、报价审批流、客服升级流、售后跟进流，建模审批节点',
      confidence: 0.78,
    },
    {
      name: 'capability',
      status: 'completed',
      discovered: '生成岗位能力矩阵：24 个岗位已建模，5 个核心岗位（销售/售前/财务/客服/售后）能力完整',
      confidence: 0.9,
    },
    {
      name: 'runtime',
      status: 'completed',
      discovered: '编译 Enterprise Runtime v1.0.0：5 个 Agent 配置模板、3 个流程引擎实例、完整权限令牌集',
      confidence: 0.82,
    },
  ],
}

export const mockCompleteness: CompletenessResult = {
  overall: 0.8,
  dimensions: {
    data_coverage: 0.85,
    process_coverage: 0.75,
    role_coverage: 0.9,
    confidence: 0.8,
    interview_completion: 0.7,
  },
  gaps: [
    {
      gap_type: 'data',
      description: '缺少完整财务制度文档',
      affected_positions: ['role-finance-manager'],
      impact_on_completeness: 5,
      suggestion: '上传财务报销制度与付款流程文档',
    },
    {
      gap_type: 'process',
      description: '采购流程未识别',
      affected_positions: ['role-procurement-specialist'],
      impact_on_completeness: 8,
      suggestion: '补充采购审批 SOP 与供应商准入流程',
    },
    {
      gap_type: 'knowledge',
      description: '产品规格书覆盖不全（缺 2 个新品型号）',
      affected_positions: ['role-presales-tech'],
      impact_on_completeness: 3,
      suggestion: '上传 SL-T200 与 SL-H300 产品规格书',
    },
  ],
}

export const mockCompilationJob: CompilationJob = {
  job_id: 'job-mock-001',
  enterprise_id: MOCK_ENTERPRISE_ID,
  stage: 'runtime',
  status: 'completed',
  progress: 100,
  confidence: 0.85,
  completeness: 85,
  started_at: '2026-07-29T09:50:00Z',
  completed_at: '2026-07-29T10:00:00Z',
  error: null,
}

// ----------------------------------------------------------------------------
// Runtime Mock 数据
// ----------------------------------------------------------------------------

export const mockRuntime: RuntimeCompileResult = {
  version: 'v1.1.0',
  model_version: 'v1.2.0',
  compiled_at: '2026-07-30T10:00:00Z',
  completeness: 85,
  organization: {
    departments: [
      { dept_id: 'dept-sales', name: '销售部', parent_dept_id: null, head_employee_id: 'agent-sales-001', level: 1 },
      { dept_id: 'dept-presales', name: '售前技术部', parent_dept_id: null, head_employee_id: 'agent-presales-001', level: 1 },
      { dept_id: 'dept-finance', name: '财务部', parent_dept_id: null, head_employee_id: 'agent-finance-001', level: 1 },
      { dept_id: 'dept-service', name: '客户服务部', parent_dept_id: null, head_employee_id: 'agent-service-001', level: 1 },
      { dept_id: 'dept-aftersales', name: '售后服务部', parent_dept_id: null, head_employee_id: 'agent-aftersales-001', level: 1 },
    ],
    reporting_tree: { 'CEO': ['dept-sales', 'dept-presales', 'dept-finance', 'dept-service', 'dept-aftersales'] },
  },
  agents: [
    {
      agent_id: 'agent-sales-001',
      agent_name: '陈思远（AI）',
      role_id: 'role-sales-rep',
      department: '销售部',
      level: 'L4-专员',
      system_prompt: '你是智链物联的销售代表，负责客户开发与商机跟进。遵循销售SOP v2，询盘4小时内响应，遇到产品参数问题转售前技术支持。',
      skills: [
        { skill_id: 'skill-product-query', name: '产品参数查询', enabled: true },
        { skill_id: 'skill-quotation', name: '报价生成', enabled: true },
        { skill_id: 'skill-crm', name: 'CRM操作', enabled: true },
      ],
      knowledge_bases: ['kb-sales-sop-v2', 'kb-product-catalog', 'kb-faq-sales'],
      tools: [
        { tool_id: 'tool-crm', name: 'CRM系统', tool_type: 'api', permissions: ['customer.read.self', 'opportunity.write.self'] },
        { tool_id: 'tool-quotation-template', name: '报价单模板', tool_type: 'script', permissions: [] },
      ],
      permissions: ['customer.read.self', 'opportunity.write.self', 'quotation.submit'],
      memory_config: {
        short_term: { max_turns: 20 },
        long_term: { enabled: true, collection_prefix: 'memory_lt' },
        entity_memory: { enabled: true, linked_graph: true },
      },
      kpi_ids: ['kpi-sales-1', 'kpi-sales-2'],
      status: 'active',
    },
    {
      agent_id: 'agent-presales-001',
      agent_name: '林晓彤（AI）',
      role_id: 'role-presales-tech',
      department: '售前技术部',
      level: 'L4-专员',
      system_prompt: '你是智链物联的售前技术支持，负责产品参数查询与技术咨询。协助销售回复客户技术问题。',
      skills: [
        { skill_id: 'skill-product-spec', name: '产品规格查询', enabled: true },
        { skill_id: 'skill-tech-consult', name: '技术咨询', enabled: true },
      ],
      knowledge_bases: ['kb-product-catalog', 'kb-tech-specs'],
      tools: [
        { tool_id: 'tool-product-db', name: '产品数据库', tool_type: 'api', permissions: ['product.read'] },
      ],
      permissions: ['product.read', 'tech.advice'],
      memory_config: {
        short_term: { max_turns: 20 },
        long_term: { enabled: true, collection_prefix: 'memory_lt' },
        entity_memory: { enabled: true, linked_graph: true },
      },
      kpi_ids: ['kpi-presales-1'],
      status: 'active',
    },
    {
      agent_id: 'agent-finance-001',
      agent_name: '王志强（AI）',
      role_id: 'role-finance-manager',
      department: '财务部',
      level: 'L5-经理',
      system_prompt: '你是智链物联的财务经理，负责报价审核与费用审批。按审批流程 v2 校验金额分级：<5万经理 / 5-20万总监 / >20万CEO。',
      skills: [
        { skill_id: 'skill-quotation-review', name: '报价审核', enabled: true },
        { skill_id: 'skill-expense-audit', name: '费用审计', enabled: true },
      ],
      knowledge_bases: ['kb-finance-policy', 'kb-approval-flow'],
      tools: [
        { tool_id: 'tool-finance-system', name: '财务系统', tool_type: 'api', permissions: ['finance.read', 'finance.approve.l1'] },
      ],
      permissions: ['finance.read', 'finance.approve.l1', 'quotation.review'],
      memory_config: {
        short_term: { max_turns: 20 },
        long_term: { enabled: true, collection_prefix: 'memory_lt' },
        entity_memory: { enabled: true, linked_graph: true },
      },
      kpi_ids: ['kpi-finance-1'],
      status: 'active',
    },
    {
      agent_id: 'agent-service-001',
      agent_name: '赵敏（AI）',
      role_id: 'role-cs-specialist',
      department: '客户服务部',
      level: 'L3-专员',
      system_prompt: '你是智链物联的客服专员，负责订单同步与客户档案管理。成交后创建客户档案并发送交付通知。',
      skills: [
        { skill_id: 'skill-order-sync', name: '订单同步', enabled: true },
        { skill_id: 'skill-customer-profile', name: '客户档案管理', enabled: true },
      ],
      knowledge_bases: ['kb-customer-base', 'kb-service-sop'],
      tools: [
        { tool_id: 'tool-crm', name: 'CRM系统', tool_type: 'api', permissions: ['customer.write', 'order.write'] },
      ],
      permissions: ['customer.write', 'order.write', 'notification.send'],
      memory_config: {
        short_term: { max_turns: 20 },
        long_term: { enabled: true, collection_prefix: 'memory_lt' },
        entity_memory: { enabled: true, linked_graph: true },
      },
      kpi_ids: ['kpi-service-1'],
      status: 'active',
    },
    {
      agent_id: 'agent-aftersales-001',
      agent_name: '孙磊（AI）',
      role_id: 'role-aftersales-specialist',
      department: '售后服务部',
      level: 'L3-专员',
      system_prompt: '你是智链物联的售后服务专员，负责售后跟进与客户反馈记录。按客服分级标准处理售后工单。',
      skills: [
        { skill_id: 'skill-aftersales-followup', name: '售后跟进', enabled: true },
        { skill_id: 'skill-feedback-record', name: '反馈记录', enabled: true },
      ],
      knowledge_bases: ['kb-aftersales-sop', 'kb-feedback-base'],
      tools: [
        { tool_id: 'tool-ticket-system', name: '工单系统', tool_type: 'api', permissions: ['ticket.write'] },
      ],
      permissions: ['ticket.write', 'feedback.record'],
      memory_config: {
        short_term: { max_turns: 20 },
        long_term: { enabled: true, collection_prefix: 'memory_lt' },
        entity_memory: { enabled: true, linked_graph: true },
      },
      kpi_ids: ['kpi-aftersales-1'],
      status: 'active',
    },
  ],
  process_engines: [
    {
      engine_id: 'engine-quotation-approval',
      process_id: 'proc-quotation-approval',
      process_type: 'approval',
      steps: [
        { step_id: 's1', name: '销售提交报价', order: 1, approval_required: false, approver_role: null, condition: null, next_step_id: 's2' },
        { step_id: 's2', name: '财务审核', order: 2, approval_required: true, approver_role: 'role-finance-manager', condition: 'amount > 0', next_step_id: 's3' },
        { step_id: 's3', name: '总监审批', order: 3, approval_required: true, approver_role: 'role-sales-director', condition: 'amount >= 50000 && amount <= 200000', next_step_id: 's4' },
        { step_id: 's4', name: '成交确认', order: 4, approval_required: false, approver_role: null, condition: null, next_step_id: null },
      ],
      triggers: [{ trigger_type: 'event', condition: 'quotation.submitted' }],
      participants: ['agent-sales-001', 'agent-finance-001'],
      escalation_rules: [{ condition: 'approval_timeout > 48h', escalate_to: 'role-ceo', timeout_hours: 48 }],
    },
    {
      engine_id: 'engine-sales-flow',
      process_id: 'proc-sales-flow',
      process_type: 'business',
      steps: [
        { step_id: 'sf1', name: '收到询盘', order: 1, approval_required: false, approver_role: null, condition: null, next_step_id: 'sf2' },
        { step_id: 'sf2', name: '产品参数查询', order: 2, approval_required: false, approver_role: null, condition: 'need_product_spec', next_step_id: 'sf3' },
        { step_id: 'sf3', name: '生成报价', order: 3, approval_required: false, approver_role: null, condition: null, next_step_id: 'sf4' },
        { step_id: 'sf4', name: '财务审核', order: 4, approval_required: true, approver_role: 'role-finance-manager', condition: null, next_step_id: 'sf5' },
        { step_id: 'sf5', name: '客服同步', order: 5, approval_required: false, approver_role: null, condition: 'deal_closed', next_step_id: 'sf6' },
        { step_id: 'sf6', name: '售后接管', order: 6, approval_required: false, approver_role: null, condition: null, next_step_id: null },
      ],
      triggers: [{ trigger_type: 'event', condition: 'inquiry.received' }],
      participants: ['agent-sales-001', 'agent-presales-001', 'agent-finance-001', 'agent-service-001', 'agent-aftersales-001'],
      escalation_rules: [],
    },
  ],
  collaboration_graph: {
    nodes: [
      { id: 'agent-sales-001', label: '陈思远（销售）', type: 'agent' },
      { id: 'agent-presales-001', label: '林晓彤（售前）', type: 'agent' },
      { id: 'agent-finance-001', label: '王志强（财务）', type: 'agent' },
      { id: 'agent-service-001', label: '赵敏（客服）', type: 'agent' },
      { id: 'agent-aftersales-001', label: '孙磊（售后）', type: 'agent' },
      { id: 'human-director', label: '销售总监（人类）', type: 'human' },
    ],
    edges: [
      { source_id: 'agent-sales-001', target_id: 'agent-presales-001', relation: 'collaborates_with', context: '产品参数协助' },
      { source_id: 'agent-sales-001', target_id: 'agent-finance-001', relation: 'approves_for', context: '报价审核' },
      { source_id: 'agent-finance-001', target_id: 'human-director', relation: 'approves_for', context: '5-20万总监审批' },
      { source_id: 'agent-sales-001', target_id: 'agent-service-001', relation: 'hands_off_to', context: '成交后同步' },
      { source_id: 'agent-service-001', target_id: 'agent-aftersales-001', relation: 'hands_off_to', context: '售后接管' },
    ],
  },
  knowledge_index: {
    vector_store_ref: 'kb-zhilian-vectors',
    graph_store_ref: 'graph-zhilian-001',
  },
  tool_registry: [
    { tool_id: 'tool-crm', name: 'CRM系统', tool_type: 'api', installed: true, verified: true, config: { endpoint: '/api/crm' } },
    { tool_id: 'tool-quotation-template', name: '报价单模板', tool_type: 'script', installed: true, verified: true, config: {} },
    { tool_id: 'tool-product-db', name: '产品数据库', tool_type: 'api', installed: true, verified: true, config: { endpoint: '/api/products' } },
    { tool_id: 'tool-finance-system', name: '财务系统', tool_type: 'api', installed: true, verified: true, config: {} },
    { tool_id: 'tool-ticket-system', name: '工单系统', tool_type: 'api', installed: true, verified: false, config: {} },
  ],
}

export const mockRuntimeVersions: RuntimeVersionSummary[] = [
  { version: 'v1.1.0', compiled_at: '2026-07-30T10:00:00Z', completeness: 85, is_active: true },
  { version: 'v1.0.0', compiled_at: '2026-07-29T10:00:00Z', completeness: 80, is_active: false },
  { version: 'v0.9.0', compiled_at: '2026-07-28T15:00:00Z', completeness: 65, is_active: false },
]

export const mockRuntimeDiff: RuntimeDiff = {
  changes: [
    {
      // 对齐后端 RuntimeDiffChange：section / key / detail（非 path / old_value / new_value / description）
      section: 'agents',
      change_type: 'modified',
      key: 'agent-sales-001',
      detail: '销售 Agent 系统提示词适配 SOP v2，新增产品参数转接逻辑',
    },
    {
      section: 'process_engines',
      change_type: 'modified',
      key: 'proc-quotation-approval',
      detail: '报价审批改为金额分级：<5万经理 / 5-20万总监 / >20万CEO',
    },
    {
      section: 'agents',
      change_type: 'added',
      key: 'agent-presales-001',
      detail: '新增售前 Agent 承接产品参数查询',
    },
  ],
  summary: 'v1.1.0 相比 v1.0.0：销售 SOP 升级为 v2，新增售前 Agent，报价审批改为金额分级。完成度从 80% 提升至 85%。',
  version_a: 'v1.0.0',
  version_b: 'v1.1.0',
}

// ----------------------------------------------------------------------------
// Workforce Mock 数据
// ----------------------------------------------------------------------------

export const mockWorkforceGenerate: WorkforceGenerateResponse = {
  recommendations: [
    {
      position_id: 'role-sales-rep',
      position_name: '销售代表',
      department: '销售部',
      level: 'L4',
      required_skills: [
        { skill_name: '产品参数查询', skill_type: 'knowledge', proficiency_level: 'intermediate', source: 'sop' },
        { skill_name: '报价生成', skill_type: 'action', proficiency_level: 'advanced', source: 'kpi' },
        { skill_name: 'CRM操作', skill_type: 'tool', proficiency_level: 'intermediate', source: 'inferred' },
      ],
      required_knowledge: [
        { knowledge_domain: '销售SOP', knowledge_base_id: 'kb-sales-sop-v2', coverage: 0.9 },
        { knowledge_domain: '产品规格', knowledge_base_id: 'kb-product-catalog', coverage: 0.85 },
      ],
      required_tools: [
        { tool_name: 'CRM系统', tool_type: 'api', required_permissions: ['customer.read.self', 'opportunity.write.self'] },
      ],
      required_permissions: ['customer.read.self', 'opportunity.write.self', 'quotation.submit'],
      kpi_ids: ['kpi-sales-1', 'kpi-sales-2'],
      main_processes: ['proc-sales-flow', 'proc-quotation-approval'],
      priority: 'P0',
    },
    {
      position_id: 'role-presales-tech',
      position_name: '售前技术支持',
      department: '售前技术部',
      level: 'L4',
      required_skills: [
        { skill_name: '产品规格查询', skill_type: 'knowledge', proficiency_level: 'advanced', source: 'sop' },
        { skill_name: '技术咨询', skill_type: 'communication', proficiency_level: 'intermediate', source: 'inferred' },
      ],
      required_knowledge: [
        { knowledge_domain: '产品规格', knowledge_base_id: 'kb-product-catalog', coverage: 0.95 },
        { knowledge_domain: '技术文档', knowledge_base_id: 'kb-tech-specs', coverage: 0.8 },
      ],
      required_tools: [
        { tool_name: '产品数据库', tool_type: 'api', required_permissions: ['product.read'] },
      ],
      required_permissions: ['product.read', 'tech.advice'],
      kpi_ids: ['kpi-presales-1'],
      main_processes: ['proc-sales-flow'],
      priority: 'P0',
    },
    {
      position_id: 'role-finance-manager',
      position_name: '财务经理',
      department: '财务部',
      level: 'L5',
      required_skills: [
        { skill_name: '报价审核', skill_type: 'action', proficiency_level: 'advanced', source: 'sop' },
        { skill_name: '费用审计', skill_type: 'action', proficiency_level: 'advanced', source: 'kpi' },
      ],
      required_knowledge: [
        { knowledge_domain: '财务制度', knowledge_base_id: 'kb-finance-policy', coverage: 0.7 },
        { knowledge_domain: '审批流程', knowledge_base_id: 'kb-approval-flow', coverage: 0.9 },
      ],
      required_tools: [
        { tool_name: '财务系统', tool_type: 'api', required_permissions: ['finance.read', 'finance.approve.l1'] },
      ],
      required_permissions: ['finance.read', 'finance.approve.l1', 'quotation.review'],
      kpi_ids: ['kpi-finance-1'],
      main_processes: ['proc-quotation-approval'],
      priority: 'P0',
    },
    {
      position_id: 'role-cs-specialist',
      position_name: '客服专员',
      department: '客户服务部',
      level: 'L3',
      required_skills: [
        { skill_name: '订单同步', skill_type: 'action', proficiency_level: 'intermediate', source: 'sop' },
        { skill_name: '客户档案管理', skill_type: 'action', proficiency_level: 'intermediate', source: 'sop' },
      ],
      required_knowledge: [
        { knowledge_domain: '客户基础', knowledge_base_id: 'kb-customer-base', coverage: 0.85 },
        { knowledge_domain: '服务SOP', knowledge_base_id: 'kb-service-sop', coverage: 0.9 },
      ],
      required_tools: [
        { tool_name: 'CRM系统', tool_type: 'api', required_permissions: ['customer.write', 'order.write'] },
      ],
      required_permissions: ['customer.write', 'order.write', 'notification.send'],
      kpi_ids: ['kpi-service-1'],
      main_processes: ['proc-sales-flow'],
      priority: 'P1',
    },
    {
      position_id: 'role-aftersales-specialist',
      position_name: '售后服务专员',
      department: '售后服务部',
      level: 'L3',
      required_skills: [
        { skill_name: '售后跟进', skill_type: 'action', proficiency_level: 'intermediate', source: 'sop' },
        { skill_name: '反馈记录', skill_type: 'action', proficiency_level: 'basic', source: 'inferred' },
      ],
      required_knowledge: [
        { knowledge_domain: '售后SOP', knowledge_base_id: 'kb-aftersales-sop', coverage: 0.8 },
      ],
      required_tools: [
        { tool_name: '工单系统', tool_type: 'api', required_permissions: ['ticket.write'] },
      ],
      required_permissions: ['ticket.write', 'feedback.record'],
      kpi_ids: ['kpi-aftersales-1'],
      main_processes: ['proc-sales-flow'],
      priority: 'P1',
    },
  ],
  total: 5,
}

export const mockWorkforceConfirm: WorkforceConfirmResponse = {
  // 对齐后端 ConfirmResponse.created_agents：CreatedAgentInfo（4 字段），非完整 AgentConfigTemplate
  created_agents: mockRuntime.agents.map((a) => ({
    agent_id: a.agent_id,
    position_id: a.role_id,
    position_name: a.agent_name,
    lifecycle_stage: 'production' as LifecycleStage,
  })),
  failed: [],
}

export const mockAgentRunMetrics: AgentRunMetrics[] = [
  {
    agent_id: 'agent-sales-001',
    agent_name: '陈思远（AI）',
    position: '销售代表',
    lifecycle_stage: 'production',
    tasks_total: 48,
    tasks_completed: 45,
    tasks_failed: 3,
    avg_response_time_ms: 3200,
    avg_satisfaction: 4.4,
    kpi_performance: { 'kpi-sales-1': 0.92, 'kpi-sales-2': 0.85 },
    tool_usage: { 'tool-crm': 120, 'tool-quotation-template': 45 },
    last_active_at: '2026-07-30T09:30:00Z',
  },
  {
    agent_id: 'agent-presales-001',
    agent_name: '林晓彤（AI）',
    position: '售前技术支持',
    lifecycle_stage: 'production',
    tasks_total: 22,
    tasks_completed: 21,
    tasks_failed: 1,
    avg_response_time_ms: 1800,
    avg_satisfaction: 4.7,
    kpi_performance: { 'kpi-presales-1': 0.95 },
    tool_usage: { 'tool-product-db': 56 },
    last_active_at: '2026-07-30T09:15:00Z',
  },
  {
    agent_id: 'agent-finance-001',
    agent_name: '王志强（AI）',
    position: '财务经理',
    lifecycle_stage: 'production',
    tasks_total: 18,
    tasks_completed: 18,
    tasks_failed: 0,
    avg_response_time_ms: 2400,
    avg_satisfaction: 4.55,
    kpi_performance: { 'kpi-finance-1': 1.0 },
    tool_usage: { 'tool-finance-system': 35 },
    last_active_at: '2026-07-30T08:45:00Z',
  },
  {
    agent_id: 'agent-service-001',
    agent_name: '赵敏（AI）',
    position: '客服专员',
    lifecycle_stage: 'production',
    tasks_total: 35,
    tasks_completed: 34,
    tasks_failed: 1,
    avg_response_time_ms: 1500,
    avg_satisfaction: 4.5,
    kpi_performance: { 'kpi-service-1': 0.97 },
    tool_usage: { 'tool-crm': 80 },
    last_active_at: '2026-07-30T09:20:00Z',
  },
  {
    agent_id: 'agent-aftersales-001',
    agent_name: '孙磊（AI）',
    position: '售后服务专员',
    lifecycle_stage: 'production',
    tasks_total: 15,
    tasks_completed: 14,
    tasks_failed: 1,
    avg_response_time_ms: 2800,
    avg_satisfaction: 4.35,
    kpi_performance: { 'kpi-aftersales-1': 0.93 },
    tool_usage: { 'tool-ticket-system': 28 },
    last_active_at: '2026-07-30T08:30:00Z',
  },
]

export const mockLifecycleState: LifecycleState = {
  stage: 'production',
  stage_entered_at: '2026-07-29T11:00:00Z',
  history: [
    // 对齐后端 LifecycleHistoryEntry：transition_reason（非 reason）
    { stage: 'recruit', stage_entered_at: '2026-07-29T10:00:00Z', transition_reason: 'Runtime Compiler 生成配置模板' },
    { stage: 'training', stage_entered_at: '2026-07-29T10:15:00Z', transition_reason: '注入知识库与技能配置' },
    { stage: 'production', stage_entered_at: '2026-07-29T11:00:00Z', transition_reason: 'MVP 跳过认证与影子模式，直接上线标注试用' },
  ],
}

export const mockAgentMemory: AgentMemory = {
  short_term: [
    { role: 'user', content: '客户 C-001 询问 SL-T100 测温范围', timestamp: '2026-07-30T09:25:00Z' },
    { role: 'assistant', content: '已转售前 Agent 查询，回复 -40~125℃', timestamp: '2026-07-30T09:26:00Z' },
  ],
  entity: [
    { entity_id: 'C-001', entity_type: 'customer', preferences: { focus: '测温精度', budget: 200000, decision_cycle_days: 14 } },
  ],
  long_term: [
    { summary: '2026-07-29 与华智制造沟通温湿度传感器，客户关注精度，已发报价 18 万', timestamp: '2026-07-29T16:00:00Z' },
  ],
}

// ----------------------------------------------------------------------------
// Evolution Mock 数据
// ----------------------------------------------------------------------------

export const mockSuggestions: AdvisorSuggestion[] = [
  {
    // 对齐后端 AdvisorSuggestion：description（必填）/ impact（可选）
    id: 'sug-001',
    enterprise_id: MOCK_ENTERPRISE_ID,
    type: 'knowledge',
    title: '补充产品规格书',
    description: '上传 SL-T200 与 SL-H300 产品规格书，扩展 kb-product-catalog 知识库。',
    impact: '预计销售 Agent 参数查询成功率从 70% 提升至 95%，完成度 +3%。',
    status: 'pending',
    created_at: '2026-07-30T08:00:00Z',
    authorization_level: 'approval',
  },
  {
    id: 'sug-002',
    enterprise_id: MOCK_ENTERPRISE_ID,
    type: 'process',
    title: '优化报价审批流程',
    description: '调整审批流：金额<5万经理自动通过 / 5-20万总监 / >20万CEO。',
    impact: '审批效率提升 60%，5 万以下报价自动通过。',
    status: 'pending',
    created_at: '2026-07-30T08:30:00Z',
    authorization_level: 'authorized',
  },
  {
    id: 'sug-003',
    enterprise_id: MOCK_ENTERPRISE_ID,
    type: 'capability',
    title: '新增售后工单自动分流能力',
    description: '为售后 Agent 启用 skill-ticket-auto-triage 技能，L1 工单自动处理。',
    impact: '售后 Agent 负载降低 40%，L1 问题自动处理。',
    status: 'pending',
    created_at: '2026-07-30T09:00:00Z',
    authorization_level: 'auto',
  },
]

export const mockOrgMetrics: OrgMetrics = {
  // 对齐后端 _compute_agent_workload：总数/阶段分布/在岗率（非按 Agent 分组）
  agent_workload: {
    total_agents: 5,
    stage_distribution: { production: 4, evaluation: 1 },
    production_rate: 0.8,
    unit: 'count',
  },
  process_efficiency: {
    total_events: 12,
    processed_count: 9,
    automation_rate: 0.75,
    event_type_distribution: {
      inquiry_received: 3,
      quotation_generated: 4,
      approval_submitted: 2,
      approval_approved: 2,
      deal_closed: 1,
    },
    unit: 'count',
  },
  tool_usage: {
    total_tools: 5,
    installed: 5,
    verified: 4,
    install_rate: 1.0,
    unit: 'count',
  },
  business_impact: {
    output_value: 540000,
    hours_replaced: 320,
    cost_saved: 48000,
  },
  maturity_level: 'L2',
}

export const mockEvolutionTimeline: EvolutionTimelineItem[] = [
  // 对齐后端 TimelineEntry：timestamp / event_type / summary / details
  // id / title / description / version / affected_agent_ids 为前端富展示可选字段
  {
    timestamp: '2026-07-30T10:00:00Z',
    event_type: 'optimization_applied',
    summary: 'Runtime 升级至 v1.1.0：销售 SOP 从 v1 升级为 v2，新增售前 Agent，报价审批改为金额分级',
    details: { version: 'v1.1.0', affected_agent_ids: ['agent-sales-001', 'agent-presales-001', 'agent-finance-001'] },
    id: 'evt-001',
    title: 'Runtime 升级至 v1.1.0',
    description: '销售 SOP 从 v1 升级为 v2，新增售前 Agent，报价审批改为金额分级',
    version: 'v1.1.0',
    affected_agent_ids: ['agent-sales-001', 'agent-presales-001', 'agent-finance-001'],
  },
  {
    timestamp: '2026-07-30T09:00:00Z',
    event_type: 'suggestion_applied',
    summary: '应用知识补充建议：补充产品规格书，扩展 kb-product-catalog',
    details: { affected_agent_ids: ['agent-sales-001', 'agent-presales-001'] },
    id: 'evt-002',
    title: '应用知识补充建议',
    description: '补充产品规格书，扩展 kb-product-catalog',
    affected_agent_ids: ['agent-sales-001', 'agent-presales-001'],
  },
  {
    timestamp: '2026-07-29T10:00:00Z',
    event_type: 'optimization_applied',
    summary: 'Runtime 首次编译 v1.0.0：基于导入的 30 个文件完成五级编译，生成 5 个 Agent 配置模板',
    details: { version: 'v1.0.0' },
    id: 'evt-003',
    title: 'Runtime 首次编译 v1.0.0',
    description: '基于导入的 30 个文件完成五级编译，生成 5 个 Agent 配置模板',
    version: 'v1.0.0',
  },
  {
    timestamp: '2026-07-29T11:00:00Z',
    event_type: 'metric_recorded',
    summary: '5 个 AI 员工上线：销售/售前/财务/客服/售后 Agent 全部进入 Production 阶段',
    details: { affected_agent_ids: ['agent-sales-001', 'agent-presales-001', 'agent-finance-001', 'agent-service-001', 'agent-aftersales-001'] },
    id: 'evt-004',
    title: '5 个 AI 员工上线',
    description: '销售/售前/财务/客服/售后 Agent 全部进入 Production 阶段',
    affected_agent_ids: ['agent-sales-001', 'agent-presales-001', 'agent-finance-001', 'agent-service-001', 'agent-aftersales-001'],
  },
]

// ----------------------------------------------------------------------------
// Interview Mock 数据
// ----------------------------------------------------------------------------

export const mockInterviewSession: InterviewSession = {
  session_id: 'interview-mock-001',
  enterprise_id: MOCK_ENTERPRISE_ID,
  status: 'active',
  answered_count: 7,
  total_count: 10,
  completeness: 70,
  created_at: '2026-07-29T09:00:00Z',
}

export const mockInterviewQuestions: InterviewQuestion[] = [
  { question_id: 'q1', category: 'sales', question: '销售成功后，订单交给谁处理？', priority: 'P0' },
  { question_id: 'q2', category: 'sales', question: '报价审批的权限分级是怎样的？谁有权批准多大金额？', priority: 'P0' },
  { question_id: 'q3', category: 'sales', question: '客户线索从哪里来？分配规则是什么？', priority: 'P1' },
  { question_id: 'q4', category: 'customer_service', question: '客服满意度目标要达到百分之多少？', priority: 'P0' },
  { question_id: 'q5', category: 'customer_service', question: '客户投诉的升级流程是什么？多久内必须响应？', priority: 'P0' },
  { question_id: 'q6', category: 'procurement', question: '采购审批权限是怎么分级的？', priority: 'P1' },
  { question_id: 'q7', category: 'finance', question: '费用报销的审批链是怎样的？各层级审批额度是多少？', priority: 'P0' },
  { question_id: 'q8', category: 'hr', question: '新员工入职流程包含哪些步骤？需要开通哪些系统权限？', priority: 'P1' },
  { question_id: 'q9', category: 'data', question: 'CRM 中哪些字段是必填的？各自代表什么业务含义？', priority: 'P1' },
  { question_id: 'q10', category: 'kpi', question: '各部门的核心 KPI 是什么？目标值是多少？', priority: 'P0' },
]

// ----------------------------------------------------------------------------
// Collaboration Mock 数据
// ----------------------------------------------------------------------------

export const mockCollaborationEvents: CollaborationEvent[] = [
  {
    event_id: 'evt-collab-001',
    enterprise_id: MOCK_ENTERPRISE_ID,
    event_type: 'inquiry_received',
    payload: { customer: 'C-001 华智制造', opportunity: 'OPP-001', product: 'SL-T100 温湿度传感器' },
    source_agent_id: 'agent-sales-001',
    target_agent_id: null,
    created_at: '2026-07-30T09:00:00Z',
  },
  {
    event_id: 'evt-collab-002',
    enterprise_id: MOCK_ENTERPRISE_ID,
    event_type: 'product_query',
    payload: { product: 'SL-T100', question: '测温范围', answer: '-40~125℃' },
    source_agent_id: 'agent-sales-001',
    target_agent_id: 'agent-presales-001',
    created_at: '2026-07-30T09:05:00Z',
  },
  {
    event_id: 'evt-collab-003',
    enterprise_id: MOCK_ENTERPRISE_ID,
    event_type: 'quotation_generated',
    payload: { quotation_id: 'QUO-001', amount: 180000, customer: 'C-001' },
    source_agent_id: 'agent-sales-001',
    target_agent_id: null,
    created_at: '2026-07-30T09:20:00Z',
  },
  {
    event_id: 'evt-collab-004',
    enterprise_id: MOCK_ENTERPRISE_ID,
    event_type: 'approval_submitted',
    payload: { quotation_id: 'QUO-001', amount: 180000, approver: '销售总监' },
    source_agent_id: 'agent-finance-001',
    target_agent_id: null,
    created_at: '2026-07-30T09:25:00Z',
  },
  {
    event_id: 'evt-collab-005',
    enterprise_id: MOCK_ENTERPRISE_ID,
    event_type: 'approval_approved',
    payload: { quotation_id: 'QUO-001', approved_by: '销售总监' },
    source_agent_id: null,
    target_agent_id: 'agent-sales-001',
    created_at: '2026-07-30T09:30:00Z',
  },
  {
    event_id: 'evt-collab-006',
    enterprise_id: MOCK_ENTERPRISE_ID,
    event_type: 'order_synced',
    payload: { order_id: 'ORD-2026-001', customer: 'C-001' },
    source_agent_id: 'agent-service-001',
    target_agent_id: null,
    created_at: '2026-07-30T09:35:00Z',
  },
  {
    event_id: 'evt-collab-007',
    enterprise_id: MOCK_ENTERPRISE_ID,
    event_type: 'after_sales',
    payload: { order_id: 'ORD-2026-001', ticket_id: 'TKT-001' },
    source_agent_id: 'agent-aftersales-001',
    target_agent_id: null,
    created_at: '2026-07-30T09:40:00Z',
  },
]

export const mockApprovalRequest: ApprovalRequest = {
  id: 'appr-001',
  enterprise_id: MOCK_ENTERPRISE_ID,
  // 前端 UI 派生字段（getPendingApprovals 从事件流构造）
  title: '报价单审批 — 华智制造 18 万',
  description: '销售 Agent 提交报价单 QUO-001，客户华智制造，金额 18 万，按金额分级需销售总监审批。',
  requester_id: 'agent-sales-001',
  requester_name: '陈思远（AI 销售代表）',
  approval_type: 'quotation',
  amount: 180000,
  context: {
    quotation_id: 'QUO-001',
    customer: 'C-001 华智制造',
    product: 'SL-T100 温湿度传感器',
    amount_tier: '5-20万（总监审批）',
  },
  status: 'pending',
  created_at: '2026-07-30T09:25:00Z',
  // 后端 ApprovalGateView 字段（GET /collaboration/approvals/{id} 直接返回时存在）
  process_id: 'proc-quotation-approval',
  node_id: 'node-director-approval',
  agent_id: 'agent-sales-001',
  approver_id: null,
  decided_at: null,
}

// ----------------------------------------------------------------------------
// Cognition Mock 数据
// ----------------------------------------------------------------------------

export const mockKnowledgeGraph: KnowledgeGraph = {
  // 节点字段对齐后端 GraphNode：node_id / name / node_type / attributes
  nodes: [
    { node_id: 'dept-sales', name: '销售部', node_type: 'department', attributes: { level: 1 }, confidence: 1.0 },
    { node_id: 'dept-presales', name: '售前技术部', node_type: 'department', attributes: { level: 1 }, confidence: 1.0 },
    { node_id: 'dept-finance', name: '财务部', node_type: 'department', attributes: { level: 1 }, confidence: 1.0 },
    { node_id: 'dept-service', name: '客户服务部', node_type: 'department', attributes: { level: 1 }, confidence: 1.0 },
    { node_id: 'dept-aftersales', name: '售后服务部', node_type: 'department', attributes: { level: 1 }, confidence: 1.0 },
    { node_id: 'role-sales-rep', name: '销售代表', node_type: 'role', attributes: { level: 'L4' }, confidence: 0.95 },
    { node_id: 'role-presales-tech', name: '售前技术支持', node_type: 'role', attributes: { level: 'L4' }, confidence: 0.95 },
    { node_id: 'role-finance-manager', name: '财务经理', node_type: 'role', attributes: { level: 'L5' }, confidence: 0.95 },
    { node_id: 'role-cs-specialist', name: '客服专员', node_type: 'role', attributes: { level: 'L3' }, confidence: 0.9 },
    { node_id: 'role-aftersales-specialist', name: '售后服务专员', node_type: 'role', attributes: { level: 'L3' }, confidence: 0.9 },
    { node_id: 'prod-sl-t100', name: 'SL-T100 温湿度传感器', node_type: 'product', attributes: { price: 1800 }, confidence: 0.9 },
    { node_id: 'cust-c001', name: '华智制造', node_type: 'customer', attributes: { industry: '制造' }, confidence: 1.0 },
    { node_id: 'opp-001', name: 'OPP-001 华智询盘', node_type: 'opportunity', attributes: { status: '报价中' }, confidence: 1.0 },
    { node_id: 'sop-sales-v2', name: '销售SOP v2', node_type: 'sop', attributes: { version: 'v2' }, confidence: 0.95 },
    { node_id: 'flow-quotation-approval', name: '报价审批流', node_type: 'approval_flow', attributes: { tiers: 3 }, confidence: 0.95 },
    { node_id: 'kpi-sales-1', name: '销售转化率', node_type: 'kpi', attributes: { target: 0.3 }, confidence: 0.9 },
    { node_id: 'tool-crm', name: 'CRM系统', node_type: 'tool', attributes: { type: 'api' }, confidence: 1.0 },
    { node_id: 'kb-product-catalog', name: '产品目录知识库', node_type: 'knowledge_base', attributes: {}, confidence: 1.0 },
    { node_id: 'perm-customer-read', name: 'customer.read.self', node_type: 'permission', attributes: {}, confidence: 1.0 },
  ],
  // 边字段对齐后端 GraphEdge：source_id / target_id / relation / attributes（后端无 id）
  edges: [
    { source_id: 'role-sales-rep', target_id: 'dept-sales', relation: 'belongs_to', attributes: {} },
    { source_id: 'role-presales-tech', target_id: 'dept-presales', relation: 'belongs_to', attributes: {} },
    { source_id: 'role-finance-manager', target_id: 'dept-finance', relation: 'belongs_to', attributes: {} },
    { source_id: 'role-cs-specialist', target_id: 'dept-service', relation: 'belongs_to', attributes: {} },
    { source_id: 'role-aftersales-specialist', target_id: 'dept-aftersales', relation: 'belongs_to', attributes: {} },
    { source_id: 'role-sales-rep', target_id: 'sop-sales-v2', relation: 'executes', attributes: {} },
    { source_id: 'role-sales-rep', target_id: 'role-presales-tech', relation: 'reports_to', attributes: { context: '产品参数协助' } },
    { source_id: 'role-sales-rep', target_id: 'tool-crm', relation: 'uses', attributes: {} },
    { source_id: 'role-sales-rep', target_id: 'kb-product-catalog', relation: 'produces', attributes: {} },
    { source_id: 'role-sales-rep', target_id: 'perm-customer-read', relation: 'has_permission', attributes: {} },
    { source_id: 'role-finance-manager', target_id: 'flow-quotation-approval', relation: 'executes', attributes: {} },
    { source_id: 'role-sales-rep', target_id: 'kpi-sales-1', relation: 'owes', attributes: {} },
    { source_id: 'opp-001', target_id: 'cust-c001', relation: 'belongs_to', attributes: {} },
    { source_id: 'opp-001', target_id: 'prod-sl-t100', relation: 'serves', attributes: {} },
  ],
  enterprise_id: MOCK_ENTERPRISE_ID,
  version: 'v1.0.0',
}

// 企业画像对齐后端 EnterpriseProfileResponse：enterprise_id + profile（嵌套 EnterpriseProfileData）
export const mockEnterpriseProfile: EnterpriseProfile = {
  enterprise_id: MOCK_ENTERPRISE_ID,
  profile: {
    basic: {
      name: '智链物联',
      industry: '物联网传感器制造',
      scale: '中型（150-300人）',
      revenue: '8000万',
      location: '深圳',
      founded: '2018',
    },
    tags: ['物联网', '传感器', 'B2B'],
    org_summary: {
      department_count: 5,
      headcount: 150,
      key_roles: ['销售代表', '售前技术支持', '财务经理', '客服专员', '售后服务专员'],
    },
    maturity: {
      level: 'L2',
      automation_coverage: 0.45,
      ai_workforce_count: 5,
    },
    business: {
      main_products: ['温湿度传感器研发', '工业物联网解决方案', '售后技术服务'],
      target_industries: ['制造', '物流', '能源'],
      core_processes: ['销售流程', '报价审批', '客户服务', '售后跟进'],
    },
    gaps: ['财务制度文档不全', '采购流程未数字化'],
    version: 'v1.0.0',
    updated_at: '2026-07-30T10:00:00Z',
    completeness_score: 0.8,
  },
}

// 运行模型对齐后端 OperatingModelResponse：enterprise_id + model（强结构 EnterpriseOperatingModelData）+ version
export const mockOperatingModel: OperatingModel = {
  enterprise_id: MOCK_ENTERPRISE_ID,
  model: {
    version: 'v1.2.0',
    completeness: 0.85,
    organization: {
      department_count: 5,
      headcount: 150,
    },
    roles: [
      {
        id: 'role-sales-rep',
        title: '销售代表',
        department: '销售部',
        level: 'L4',
        responsibilities: ['客户开发', '商机跟进', '报价生成'],
        required_skills: ['产品参数查询', '报价生成', 'CRM操作'],
        kpi_ids: ['kpi-sales-1', 'kpi-sales-2'],
        permission_ids: ['customer.read.self', 'opportunity.write.self', 'quotation.submit'],
      },
      {
        id: 'role-presales-tech',
        title: '售前技术支持',
        department: '售前技术部',
        level: 'L4',
        responsibilities: ['产品参数查询', '技术方案支持'],
        required_skills: ['产品规格查询', '方案撰写'],
        kpi_ids: ['kpi-presales-1'],
        permission_ids: ['product.read'],
      },
    ],
    processes: [
      {
        id: 'proc-sales-flow',
        name: '销售流程',
        type: 'sop',
        steps: [],
        owner_role_id: 'role-sales-rep',
        participants: ['role-presales-tech'],
        trigger_event: 'inquiry_received',
        system_ids: ['sys-crm'],
      },
      {
        id: 'proc-quotation-approval',
        name: '报价审批流',
        type: 'approval',
        steps: [],
        owner_role_id: 'role-sales-rep',
        participants: ['role-finance-manager'],
        trigger_event: 'quotation_submitted',
        system_ids: ['sys-crm'],
      },
    ],
    capabilities: [
      {
        role_id: 'role-sales-rep',
        required_capabilities: ['产品参数查询', '报价生成', 'CRM操作'],
        knowledge_sources: ['kb-sales-sop-v2', 'kb-product-catalog'],
        tools: ['CRM系统'],
      },
    ],
    runtime_rules: {
      collaboration_rules: [{ from: 'role-sales-rep', to: 'role-presales-tech', context: '产品参数协助' }],
      data_flow_rules: [{ source: 'CRM', target: '报价系统', field: 'amount' }],
      escalation_rules: [{ condition: 'amount >= 50000', escalate_to: 'role-finance-manager' }],
    },
    gaps: [
      { area: '财务', severity: 'medium', suggestion: '补充财务报销制度文档' },
      { area: '采购', severity: 'high', suggestion: '建立采购审批 SOP' },
    ],
  },
  version: 'v1.2.0',
}

/** 7 步演示案例数据（PRD §8.5） */
export const mockCollaborationSteps = [
  {
    step: 1,
    title: '收到新询盘',
    agent: '销售 Agent',
    agentId: 'agent-sales-001',
    trigger: '客户邮件 / CRM 新增商机（OPP-001，状态：报价中）',
    action: '提取客户需求（产品型号、数量、交付要求）',
    event_type: 'inquiry_received' as const,
  },
  {
    step: 2,
    title: '产品参数查询',
    agent: '产品专家 Agent',
    agentId: 'agent-presales-001',
    trigger: '销售 Agent 遇到不熟悉的产品参数（SL-T100 测温范围）',
    action: '从产品规格书补充参数，协助销售回复客户',
    event_type: 'product_query' as const,
  },
  {
    step: 3,
    title: '销售报价',
    agent: '销售 Agent',
    agentId: 'agent-sales-001',
    trigger: '参数确认后生成报价单',
    action: '调用价格表 + 报价单模板，生成报价（示例金额 18 万）',
    event_type: 'quotation_generated' as const,
  },
  {
    step: 4,
    title: '财务审核',
    agent: '财务 Agent',
    agentId: 'agent-finance-001',
    trigger: '报价单提交审核',
    action: '按审批流程 v2 校验报价（金额分级：18 万 → 销售总监审批）',
    event_type: 'approval_submitted' as const,
  },
  {
    step: 5,
    title: '总监审批',
    agent: '销售总监（人类）',
    agentId: 'human-director',
    trigger: '进入审批节点',
    action: '人类审批介入，审批通过，商机状态 → 已成交',
    event_type: 'approval_approved' as const,
  },
  {
    step: 6,
    title: '客服同步',
    agent: '客服 Agent',
    agentId: 'agent-service-001',
    trigger: '成交后',
    action: '同步订单信息，创建客户档案，发送交付通知',
    event_type: 'order_synced' as const,
  },
  {
    step: 7,
    title: '售后接管',
    agent: '售后 Agent',
    agentId: 'agent-aftersales-001',
    trigger: '交付后 / 客户反馈',
    action: '跟进售后服务，记录客户反馈',
    event_type: 'after_sales' as const,
  },
]
