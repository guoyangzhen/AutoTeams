/**
 * Flow-Core SOP 规程卡接口。
 *
 * 严格对齐后端 `backend/app/api/flow_core.py`（前缀 /flow-core）：
 * - GET  /flow-core/cards           → FlowCard[]（直接返回 flow_data，非 DB 记录）
 * - GET  /flow-core/cards/{flow_id} → FlowCard
 * - POST /flow-core/cards           → 请求体即 FlowCard 本身（upsert by flow_id）
 * - POST /flow-core/synthesize      → { text_sop, name?, flow_id? } → FlowCard
 * - POST /flow-core/execute/start   → { flow_id, initial_slots? } → FlowExecutionState
 * - POST /flow-core/execute/step    → { state, user_input?, approval_granted? } → FlowExecutionState
 */
import apiClient from './client'

export type FlowNodeType =
  | 'collect_info'
  | 'action_tool'
  | 'branch_condition'
  | 'approval_human'
  | 'sub_flow'

export interface FlowNode {
  node_id: string
  name: string
  node_type: FlowNodeType
  instruction?: string
  expected_slots?: string[]
  bound_tools?: string[]
  scoped_knowledge_buckets?: string[]
  timeout_seconds?: number
  assignee_role?: string | null
}

export interface FlowEdge {
  source_node_id: string
  target_node_id: string
  condition_expression?: string | null
  priority?: number
  label?: string | null
}

export interface FlowGuardrails {
  closed_loop_required: boolean
  adaptive_slot_filling: boolean
  high_risk_confirmation: boolean
}

export interface FlowCard {
  flow_id: string
  name: string
  version: string
  description?: string
  timeout_seconds?: number
  guardrails?: FlowGuardrails
  start_node_id: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  terminal_node_ids?: string[]
}

export type FlowRunStatus =
  | 'running'
  | 'waiting_user_input'
  | 'waiting_approval'
  | 'completed'
  | 'failed'

export interface FlowTraceEntry {
  timestamp?: string
  from_node?: string | null
  to_node?: string
  action?: string
}

/**
 * 服务端持有的执行运行（FlowRun）。
 *
 * AUD-15 后执行是**服务端状态机**：客户端不再回传 state，
 * 只持有 run_id 并按 version 做乐观锁。
 */
export interface FlowExecutionRun {
  run_id: string
  flow_id: string
  current_node_id: string
  status: FlowRunStatus
  /** 乐观锁版本号，提交时回传；不匹配后端返回 409。 */
  version: number
  step_count: number
  last_output?: string | null
  error_message?: string | null
  state: FlowRunState
  created_at?: string | null
  updated_at?: string | null
  /** 仅 GET /execute/runs/{run_id} 返回。 */
  approvals?: FlowApproval[]
}

/** 服务端持久化的执行状态（run.state）；客户端只读，不回传。 */
export interface FlowRunState {
  flow_id: string
  current_node_id: string
  accumulated_slots?: Record<string, unknown>
  status?: FlowRunStatus
  history_trace?: FlowTraceEntry[]
  last_output?: string | null
  error_message?: string | null
  step_count?: number
  /** 当前等待审批的节点；审批时必须原样回传。 */
  pending_approval_node_id?: string | null
  tool_results?: Record<string, unknown>
  simulations?: Array<Record<string, unknown>>
}

/** 人工审批记录（仅 GET /execute/runs/{run_id} 返回）。 */
export interface FlowApproval {
  id: string
  node_id: string
  approver_email: string
  approver_role: string
  decision: 'granted' | 'rejected'
  comment?: string | null
  created_at?: string | null
}

export async function listFlowCards(): Promise<FlowCard[]> {
  const resp = await apiClient.get('/flow-core/cards')
  return resp.data.data
}

export async function getFlowCard(flowId: string): Promise<FlowCard> {
  const resp = await apiClient.get(`/flow-core/cards/${flowId}`)
  return resp.data.data
}

/** 保存或更新规程卡（后端按 flow_id 做 upsert）。 */
export async function saveFlowCard(card: FlowCard): Promise<FlowCard> {
  const resp = await apiClient.post('/flow-core/cards', card)
  return resp.data.data
}

/** 自然语言经验逆向编译为标准 FlowCard 状态机。 */
export async function synthesizeSOP(
  textSop: string,
  options?: { name?: string; flowId?: string },
): Promise<FlowCard> {
  const resp = await apiClient.post('/flow-core/synthesize', {
    text_sop: textSop,
    name: options?.name,
    flow_id: options?.flowId,
  })
  return resp.data.data
}

/** 启动规程执行。返回 201 + 服务端签发的执行运行。 */
export async function startFlow(
  flowId: string,
  options?: {
    initialSlots?: Record<string, unknown>
    maxSteps?: number
    maxWallClockSeconds?: number
  },
): Promise<FlowExecutionRun> {
  const resp = await apiClient.post('/flow-core/execute/start', {
    flow_id: flowId,
    initial_slots: options?.initialSlots,
    max_steps: options?.maxSteps,
    max_wall_clock_seconds: options?.maxWallClockSeconds,
  })
  return resp.data.data
}

/** 列出当前企业的执行运行（跨企业不可见）。 */
export async function listFlowRuns(limit = 50): Promise<FlowExecutionRun[]> {
  const resp = await apiClient.get('/flow-core/execute/runs', { params: { limit } })
  return resp.data.data
}

/** 读取执行运行（含 approvals[]）；进程重启后仍可读回。 */
export async function getFlowRun(runId: string): Promise<FlowExecutionRun> {
  const resp = await apiClient.get(`/flow-core/execute/runs/${runId}`)
  return resp.data.data
}

/**
 * 单步推进。服务端持有权威状态，客户端只提交 run_id 与可选的乐观锁版本号；
 * 提交 state / approval_granted 会被后端以 422 拒绝。
 */
export async function stepFlow(
  runId: string,
  options?: { userInput?: string; expectedVersion?: number },
): Promise<FlowExecutionRun> {
  const resp = await apiClient.post('/flow-core/execute/step', {
    run_id: runId,
    user_input: options?.userInput,
    expected_version: options?.expectedVersion,
  })
  return resp.data.data
}

/**
 * 人工审批。决定只对 (run_id, node_id, 审批人) 生效；
 * 非授权审批人返回 403 且不落库、不推进；版本过期返回 409。
 */
export async function approveFlowRun(
  runId: string,
  nodeId: string,
  decision: 'granted' | 'rejected',
  options?: { comment?: string; expectedVersion?: number },
): Promise<FlowExecutionRun> {
  const resp = await apiClient.post(`/flow-core/execute/runs/${runId}/approve`, {
    node_id: nodeId,
    decision,
    comment: options?.comment,
    expected_version: options?.expectedVersion,
  })
  return resp.data.data
}
