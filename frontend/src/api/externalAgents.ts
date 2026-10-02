/**
 * 外部本地 Agent 接入 API 客户端。
 *
 * 对应后端 `backend/app/api/external_agents.py`（prefix=/external-agents）：
 * - GET  /external-agents                 已侦测 / 已接入的本地外部 Agent 清单
 * - POST /external-agents/{id}/connect    连接指定外部 Agent
 * - POST /external-agents/{id}/disconnect 断开指定外部 Agent
 * - POST /external-agents/{id}/dispatch   向外部 Agent 分派子任务委托
 *
 * 注意：本组端点**不走** `success_response()` 信封，直接返回裸 JSON
 * （列表为数组，connect/dispatch 为对象），因此这里读取 `resp.data` 而非 `resp.data.data`。
 */
import apiClient from './client'

/** 外部 Agent 类型：Codex CLI / Claude Code / 标准 MCP / 自研。 */
export type ExternalAgentType = 'codex' | 'claude' | 'mcp' | 'custom'

/** 外部 Agent 连接状态。 */
export type ExternalAgentStatus = 'offline' | 'detected' | 'connected' | 'busy'

/** 本地外部 Agent 档案（ExternalAgentItem）。 */
export interface ExternalAgent {
  id: string
  name: string
  type: ExternalAgentType
  status: ExternalAgentStatus
  command_path: string | null
  description: string
  capabilities: string[]
}

/** 连接 / 断开结果。 */
export interface ExternalAgentActionResult {
  success: boolean
  message: string
  agent?: ExternalAgent
}

/** 任务委托请求体。 */
export interface ExternalDispatchPayload {
  prompt: string
  context?: Record<string, unknown> | null
  timeout_seconds?: number
}

/** 任务委托回执（DispatchTaskResponse）。 */
export interface ExternalTaskReceipt {
  task_id: string
  agent_id: string
  status: string
  result: string
  duration_ms: number
}

/** 拉取本地外部 Agent 清单。 */
export async function listExternalAgents(): Promise<ExternalAgent[]> {
  const resp = await apiClient.get<ExternalAgent[]>('/external-agents')
  return resp.data
}

/** 连接指定外部 Agent。 */
export async function connectExternalAgent(
  agentId: string,
  options?: Record<string, unknown>,
): Promise<ExternalAgentActionResult> {
  const resp = await apiClient.post<ExternalAgentActionResult>(
    `/external-agents/${encodeURIComponent(agentId)}/connect`,
    { options: options ?? null },
  )
  return resp.data
}

/** 断开指定外部 Agent。 */
export async function disconnectExternalAgent(
  agentId: string,
): Promise<ExternalAgentActionResult> {
  const resp = await apiClient.post<ExternalAgentActionResult>(
    `/external-agents/${encodeURIComponent(agentId)}/disconnect`,
  )
  return resp.data
}

/** 向外部 Agent 分派子任务委托。 */
export async function dispatchTaskToAgent(
  agentId: string,
  payload: ExternalDispatchPayload,
): Promise<ExternalTaskReceipt> {
  const resp = await apiClient.post<ExternalTaskReceipt>(
    `/external-agents/${encodeURIComponent(agentId)}/dispatch`,
    payload,
  )
  return resp.data
}
