/**
 * AutoTeams · 具身执行器（Local Runner 2.0）前端 API 客户端。
 *
 * 严格对齐后端 `backend/app/api/runner_v2.py`（prefix=/runner/v2）与
 * `backend/app/services/runner_v2_protocol.py` 的 `to_dict()` 输出：
 * - 端侧档案字段为 online / last_seen（非 is_online / last_heartbeat）
 * - 任务主键为 task_id，生命周期字段为 state（非 id / status）
 * - 指令步骤字段为 op / target / text / risk（非 action / instruction）
 * - 护栏裁决 verdict 为 allow | block | require_2fa
 */
import apiClient from './client'

/** 端侧 Runner 档案。 */
export interface PhysicalRunner {
  runner_id: string
  platform: string
  version: string
  scopes: string[]
  capabilities: Record<string, boolean>
  /** 心跳未过期即为在线（后端按 TTL 实时计算） */
  online: boolean
  last_seen: string
}

/** 物理操作的两条端侧通道。 */
export type PhysicalChannel = 'browser_action' | 'desktop_accessibility'

/** 一条具身物理指令（与端侧 local-runner/src/physical.ts 契约一致）。 */
export interface PhysicalStep {
  op: string
  target?: string | null
  text?: string | null
  credential_ref?: string | null
  rows?: Array<Record<string, string>> | null
  keys?: string[] | null
  url?: string | null
  label?: string | null
  /** high 触发双因子确认门禁 */
  risk: 'normal' | 'high'
}

/** 物理任务生命周期。 */
export type PhysicalTaskState =
  | 'blocked'
  | 'awaiting_2fa'
  | 'dispatched'
  | 'executing'
  | 'completed'
  | 'failed'
  | 'cancelled'

/** Tri-Rule 护栏裁决。 */
export interface PhysicalVerdict {
  action: 'allow' | 'block' | 'require_2fa'
  rule: string
  reason: string
  step_index?: number | null
}

/** 物理任务云端档案。 */
export interface PhysicalTask {
  task_id: string
  enterprise_id: string
  channel: PhysicalChannel
  state: PhysicalTaskState
  runner_id: string | null
  created_by: string
  challenge_id: string | null
  verdict: PhysicalVerdict
  result: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

/** 双因子确认状态机（两道因子串联）。 */
export type ChallengeState =
  | 'pending_endpoint_confirmation'
  | 'pending_device_code'
  | 'approved'
  | 'rejected'
  | 'expired'

/** 高危操作双因子确认挑战。 */
export interface TwoFactorChallenge {
  challenge_id: string
  task_id: string
  enterprise_id: string
  state: ChallengeState
  reason: string
  endpoint_confirmed_by: string | null
  /** 第二因子（端侧物理确认码）是否已核验 */
  device_verified: boolean
  expires_at: string
  created_at: string
}

/** 操作审计留痕（凭据已脱敏）。 */
export interface PhysicalAuditEntry {
  trace_id: string
  task_id: string | null
  runner_id: string | null
  actor: string
  event: string
  decision: string
  detail: string
  created_at: string
}

/** 列出端侧 Runner 档案与在线状态。 */
export async function listPhysicalRunners(): Promise<PhysicalRunner[]> {
  const resp = await apiClient.get('/runner/v2/runners')
  return resp.data.data
}

/** 列出本企业物理任务。 */
export async function listPhysicalTasks(): Promise<PhysicalTask[]> {
  const resp = await apiClient.get('/runner/v2/tasks')
  return resp.data.data
}

/** 获取单条物理任务详情。 */
export async function getPhysicalTask(taskId: string): Promise<PhysicalTask> {
  const resp = await apiClient.get(`/runner/v2/tasks/${taskId}`)
  return resp.data.data
}

/** 下发物理任务：护栏裁决后放行 / 阻断 / 挂起双因子确认。 */
export async function dispatchPhysicalTask(payload: {
  channel: PhysicalChannel
  runner_id: string
  steps: PhysicalStep[]
}): Promise<PhysicalTask> {
  const resp = await apiClient.post('/runner/v2/tasks', payload)
  return resp.data.data
}

/** 列出本企业高危操作双因子确认挑战。 */
export async function listTwoFactorChallenges(): Promise<TwoFactorChallenge[]> {
  const resp = await apiClient.get('/runner/v2/challenges')
  return resp.data.data
}

/** 双因子第一因子：云端意图确认 / 人工否决。 */
export async function confirmTwoFactor(
  challengeId: string,
  decision: 'approve' | 'reject',
): Promise<TwoFactorChallenge> {
  const resp = await apiClient.post(`/runner/v2/challenges/${challengeId}/confirm`, {
    decision,
  })
  return resp.data.data
}

/** 读取操作审计留痕。 */
export async function readPhysicalAudit(limit = 50): Promise<PhysicalAuditEntry[]> {
  const resp = await apiClient.get('/runner/v2/audit', { params: { limit } })
  return resp.data.data
}
