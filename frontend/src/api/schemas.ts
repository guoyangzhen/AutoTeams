/**
 * 运行时契约校验（Zod）— docs/autoteams_restructuring_plan_latest.md §4.5.2。
 *
 * 设计要点：
 * - 每个 Schema 逐字段镜像后端 Pydantic 视图（backend/app/schemas/*.py），仅覆盖
 *   API 层「真实路径」的响应（response.data.data）；USE_MOCK 分支不经过校验
 *   （刻意为之：Mock 形状允许脱离后端契约做演示）。
 * - fail-visible：校验失败抛 ContractViolationError（携带端点上下文与前几条 issue），
 *   由页面级 .catch() 降级为错误/空态 —— 在数据入口显式暴露契约漂移，
 *   而不是把 undefined 带进渲染层静默降级。
 * - datetime 字段经 model_dump(mode="json") 序列化为 ISO 字符串，用 isoDatetime 校验。
 * - 后端 dict[str, Any] 的开放结构（details / agent_workload 等）用 record(unknown)
 *   做结构校验，不猜测内部键；实测形状写在注释里，供排障时对照。
 *
 * 覆盖端点：
 * - /evolution/*：suggestions 列表与 generate / apply / reject、metrics、timeline、
 *   maturity、optimizations
 * - /shadow/tasks：列表 + create / record-ai / generate-ai / evaluate / promote / demote
 *   （7 个端点均返回 ShadowTaskView）
 * - /shadow/counterfactual/*：sessions 列表、promotion-gate
 * - /workforce/{enterprise_id}：Workforce 分页列表
 */
import { z } from 'zod'

// ============================================================================
// 基础工具
// ============================================================================

/** ISO 时间字符串（Pydantic datetime → mode="json" → ISO8601 文本） */
const isoDatetime = z
  .string()
  .refine((s) => !Number.isNaN(Date.parse(s)), { message: '非法时间字符串' })

/** 分页列表统一形态（spec.md §10.7：{ items, total }） */
function paginated<T extends z.ZodTypeAny>(itemSchema: T) {
  return z.object({
    items: z.array(itemSchema),
    total: z.number().int().min(0),
  })
}

/** 契约校验失败 —— 页面层 catch 后渲染错误/空态，而非带着脏数据继续渲染 */
export class ContractViolationError extends Error {
  readonly endpoint: string

  constructor(endpoint: string, detail: string) {
    super(`[契约校验失败] ${endpoint}: ${detail}`)
    this.name = 'ContractViolationError'
    this.endpoint = endpoint
  }
}

/**
 * 运行时契约校验入口：用 Zod schema 解析后端响应体（ApiResponse.data）。
 *
 * 失败时（DEV 下额外打印完整 payload）抛出带端点上下文的 ContractViolationError。
 */
export function parseContract<S extends z.ZodTypeAny>(
  schema: S,
  data: unknown,
  endpoint: string,
): z.infer<S> {
  const result = schema.safeParse(data)
  if (!result.success) {
    const issues = result.error.issues
    const head = issues
      .slice(0, 3)
      .map((i) => `${i.path.join('.') || '<root>'}: ${i.message}`)
      .join('；')
    const detail = `共 ${issues.length} 处不符 —— ${head}`
    if (import.meta.env.DEV) {
      // 仅开发模式输出契约校验明细。no-console 未在 eslint:recommended 中启用，
      // 因此这里不需要（也不允许）多余的 eslint-disable 指令——CI 用
      // --report-unused-disable-directives 检查这类无效抑制。
      console.error(`[契约校验失败] ${endpoint} ${detail}`, data)
    }
    throw new ContractViolationError(endpoint, detail)
  }
  return result.data
}

// ============================================================================
// Evolution（backend/app/schemas/evolution.py）
// ============================================================================

/** AdvisorSuggestion —— 对齐 AdvisorSuggestion（type/title≤256/description/impact 可空） */
export const AdvisorSuggestionSchema = z
  .object({
    id: z.string().min(1),
    enterprise_id: z.string().min(1),
    type: z.enum(['knowledge', 'process', 'capability', 'organization']),
    title: z.string().max(256),
    description: z.string(),
    impact: z.string().nullable(),
    status: z.enum(['pending', 'applied', 'rejected']),
    applied_at: isoDatetime
      .nullish()
      .transform((v) => v ?? undefined),
    created_at: isoDatetime,
  })
  // 后端视图不含 authorization_level（前端按 type 推断）；passthrough 保留未来后端新增字段
  .passthrough()

/** 建议列表（GET /evolution/{enterprise_id}/suggestions → SuggestionListResponse） */
export const AdvisorSuggestionListSchema = paginated(AdvisorSuggestionSchema)

/** 应用建议响应（POST /evolution/suggestions/{id}/apply → ApplySuggestionResponse） */
export const ApplySuggestionResponseSchema = z.object({
  applied: z.boolean(),
  suggestion_id: z.string().min(1),
  affected_agents: z.array(z.string()),
  message: z.string(),
})

/** 拒绝建议响应（POST /evolution/suggestions/{id}/reject → RejectSuggestionResponse） */
export const RejectSuggestionResponseSchema = z.object({
  rejected: z.boolean(),
  suggestion_id: z.string().min(1),
})

/**
 * 组织分析指标（GET /evolution/{enterprise_id}/metrics → OrgMetricsResponse）。
 *
 * 4 类指标在后端均为 dict[str, Any]，此处按 org_analytics 实测键做结构校验：
 * - agent_workload：{ total_agents, stage_distribution, production_rate, unit }
 *   （注意：并非前端旧类型误标的「按 Agent 分组」结构，无页面消费）
 * - process_efficiency / tool_usage / business_impact：键固定，见下。
 * Pydantic 服务端已兜底所有键（model_dump 必含），故客户端不放 default，缺键即报错。
 */
export const OrgMetricsSchema = z.object({
  agent_workload: z.object({
    total_agents: z.number().int().min(0),
    stage_distribution: z.record(z.string(), z.number().int().min(0)),
    production_rate: z.number().min(0).max(1),
    unit: z.string(),
  }),
  process_efficiency: z.object({
    total_events: z.number().min(0),
    event_type_distribution: z.record(z.string(), z.number()),
    processed_count: z.number().min(0),
    automation_rate: z.number().min(0).max(1),
    unit: z.string(),
  }),
  tool_usage: z.object({
    total_tools: z.number().min(0),
    installed: z.number().min(0),
    verified: z.number().min(0),
    install_rate: z.number().min(0).max(1),
    unit: z.string(),
  }),
  business_impact: z.object({
    deal_count: z.number().min(0),
    total_value: z.number(),
    currency: z.string(),
    avg_deal_value: z.number(),
    unit: z.string(),
    output_value: z.number(),
    cost_saved: z.number(),
    hours_replaced: z.number(),
    ai_cost: z.number(),
    roi: z.number(),
  }),
  maturity_level: z.enum(['L1', 'L2', 'L3', 'L4', 'L5']),
  period: z.string().nullable(),
})

/** 成熟度评级（GET /evolution/{enterprise_id}/maturity → MaturityRating） */
export const MaturityRatingSchema = z.object({
  level: z.enum(['L1', 'L2', 'L3', 'L4', 'L5']),
  name: z.string().min(1),
  description: z.string(),
  achieved: z.boolean(),
  dimensions: z.record(z.string(), z.number()),
})

/** Timeline 条目（TimelineEntry：timestamp / event_type / summary / details） */
export const TimelineEntrySchema = z.object({
  timestamp: isoDatetime,
  // 后端为 str（新增事件类型不破坏旧前端），前端按需扩展展示
  event_type: z.string().min(1),
  summary: z.string(),
  details: z.record(z.string(), z.unknown()),
})

/** Timeline 列表（GET /evolution/{enterprise_id}/timeline → TimelineResponse） */
export const TimelineListSchema = paginated(TimelineEntrySchema)

/** 优化历史条目（GET /evolution/{enterprise_id}/optimizations → continuous_optimizer 联表投影） */
export const OptimizationHistoryItemSchema = z.object({
  id: z.string().min(1),
  agent_id: z.string().min(1),
  type: z.string().min(1),
  applied: z.boolean(),
  applied_at: isoDatetime.nullable(),
  created_at: isoDatetime.nullable(),
  output_data: z.record(z.string(), z.unknown()).nullable(),
})

/** 优化历史列表 */
export const OptimizationListSchema = paginated(OptimizationHistoryItemSchema)

/** 触发生成建议响应（POST /evolution/{enterprise_id}/suggestions/generate） */
export const GenerateSuggestionsResponseSchema = z.object({
  suggestions: z.array(AdvisorSuggestionSchema),
  count: z.number().int().min(0),
})

// ============================================================================
// 影子模式（backend/app/schemas/shadow.py → ShadowTaskView）
// ============================================================================

/** 影子任务视图（confidence 由后端钳制在 0-1；状态机 4 态） */
export const ShadowTaskSchema = z.object({
  id: z.string().min(1),
  enterprise_id: z.string().min(1),
  agent_id: z.string().min(1).nullable(),
  task_type: z.string().min(1),
  question: z.string(),
  human_answer: z.string().nullable(),
  ai_answer: z.string().nullable(),
  confidence: z.number().min(0).max(1).nullable(),
  status: z.enum(['shadowing', 'evaluating', 'qualified', 'autonomous']),
  eval_result: z.enum(['pending', 'match', 'mismatch']),
  promoted_at: isoDatetime.nullable(),
  created_at: isoDatetime,
  updated_at: isoDatetime,
})

/** 影子任务列表（GET /shadow/tasks → ShadowTaskListResponse） */
export const ShadowTaskListSchema = paginated(ShadowTaskSchema)

/** 影子模式阶段汇总（ShadowStageSummary） */
export const ShadowStageSummarySchema = z.object({
  status: z.enum(['shadowing', 'evaluating', 'qualified', 'autonomous']),
  count: z.number().int().min(0),
  match_count: z.number().int().min(0),
  mismatch_count: z.number().int().min(0),
  avg_confidence: z.number().min(0).max(1).nullable(),
})

/** 影子模式汇总（GET /shadow/tasks/summary → ShadowSummaryResponse） */
export const ShadowSummarySchema = z.object({
  stages: z.array(ShadowStageSummarySchema),
  autonomous_count: z.number().int().min(0),
  total_count: z.number().int().min(0),
})

// ============================================================================
// 双盲反事实影子评估（backend/app/schemas/counterfactual_shadow.py）
// ============================================================================

/** 差分明细（CounterfactualDiffView） */
export const CounterfactualDiffSchema = z.object({
  diff_id: z.string().min(1),
  session_id: z.string().min(1),
  dimension: z.enum(['semantics', 'latency', 'cost', 'risk']),
  human_value: z.string(),
  agent_value: z.string(),
  delta_score: z.number(),
  assessment: z.enum(['better', 'worse', 'parity']),
  note: z.string(),
  created_at: isoDatetime,
})

/** 单笔裁决（CounterfactualVerdictView） */
export const CounterfactualVerdictSchema = z.object({
  is_qualified: z.boolean(),
  semantic_alignment_score: z.number(),
  time_saving_seconds: z.number(),
  cost_delta_yuan: z.number(),
  expected_net_benefit_yuan: z.number(),
  guardrail_breach_count: z.number().int().min(0),
  reasons: z.array(z.string()),
})

/** 免干预转正裁决（PromotionDecisionView；remaining/required 为推导值，不做范围假设） */
export const PromotionDecisionSchema = z.object({
  is_auto_promoted: z.boolean(),
  consecutive_pass_streak: z.number().int(),
  required_streak: z.number().int(),
  remaining_to_promotion: z.number().int(),
  hit_threshold: z.boolean(),
})

/** 反事实会话视图（CounterfactualSessionView） */
export const CounterfactualSessionSchema = z.object({
  session_id: z.string().min(1),
  enterprise_id: z.string().min(1),
  employee_badge: z.string().min(1),
  scenario: z.string(),
  human_action_snapshot: z.string(),
  agent_proposal_snapshot: z.string(),
  semantic_alignment_score: z.number(),
  time_saving_seconds: z.number(),
  cost_delta_yuan: z.number(),
  expected_net_benefit_yuan: z.number(),
  human_duration_seconds: z.number(),
  agent_duration_seconds: z.number(),
  human_cost_yuan: z.number(),
  agent_cost_yuan: z.number(),
  guardrail_breach_count: z.number().int().min(0),
  is_qualified: z.boolean(),
  consecutive_pass_streak: z.number().int(),
  is_auto_promoted: z.boolean(),
  promoted_at: isoDatetime.nullable(),
  created_at: isoDatetime,
  updated_at: isoDatetime,
  diffs: z.array(CounterfactualDiffSchema),
  verdict: CounterfactualVerdictSchema.nullable(),
  promotion: PromotionDecisionSchema.nullable(),
})

/** 反事实会话列表（GET /shadow/counterfactual/sessions） */
export const CounterfactualSessionListSchema = paginated(CounterfactualSessionSchema)

/** 免干预转正准入看板（GET /shadow/counterfactual/promotion-gate → PromotionGateView） */
export const PromotionGateSchema = z.object({
  enterprise_id: z.string().min(1),
  employee_badge: z.string().min(1),
  consecutive_pass_streak: z.number().int(),
  required_streak: z.number().int(),
  remaining_to_promotion: z.number().int(),
  is_auto_promoted: z.boolean(),
  total_sessions: z.number().int().min(0),
  qualified_sessions: z.number().int().min(0),
  semantic_alignment_threshold: z.number(),
  max_cost_delta_yuan: z.number(),
  max_guardrail_breaches: z.number().int().min(0),
})

// ============================================================================
// Workforce（backend/app/schemas/workforce.py → AgentRunMetrics）
// ============================================================================

/** 单个 Agent 运行指标（7 态生命周期；kpi/tool_usage 为服务端真实聚合） */
export const AgentRunMetricsSchema = z.object({
  agent_id: z.string().min(1),
  agent_name: z.string(),
  position: z.string(),
  lifecycle_stage: z.enum([
    'recruit',
    'training',
    'production',
    'evaluation',
    'continuous_learning',
    'promotion',
    'retired',
  ]),
  tasks_total: z.number().int().min(0),
  tasks_completed: z.number().int().min(0),
  tasks_failed: z.number().int().min(0),
  avg_response_time_ms: z.number().min(0),
  avg_satisfaction: z.number().min(0),
  kpi_performance: z.record(z.string(), z.number()),
  tool_usage: z.record(z.string(), z.number().int()),
  last_active_at: isoDatetime,
})

/** Workforce 列表（GET /workforce/{enterprise_id} → { items, total, limit, offset }） */
export const WorkforceListSchema = paginated(AgentRunMetricsSchema)
