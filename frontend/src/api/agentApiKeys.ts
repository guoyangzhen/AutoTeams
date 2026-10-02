import apiClient from './client'
import { ApiResponse } from '@/types'

/**
 * Agent API 机器凭证（外部 Agent 调用 AutoTeams REST API）管理客户端。
 *
 * 安全约定：明文 api_key 只在创建响应中出现一次，后端仅保存 HMAC 摘要；
 * 前端创建成功后一次性展示，之后只能看到 key_prefix。
 */

export const AGENT_API_SCOPES = [
  { value: 'agent:read', label: '读取 Agent（列表/详情）' },
  { value: 'agent:chat', label: 'Agent 对话' },
  { value: 'build:read', label: '读取构建任务' },
  { value: 'compiler:read', label: '读取编译任务' },
] as const

export interface AgentApiCredential {
  id: string
  name: string
  key_prefix: string
  scopes: string[]
  allowed_agent_ids: string[]
  is_active: boolean
  expires_at: string | null
  revoked_at: string | null
  last_used_at: string | null
  created_at: string
}

export interface CreatedAgentApiCredential extends AgentApiCredential {
  /** 仅创建响应返回一次，永不重放 */
  api_key: string
}

export interface CreateAgentApiCredentialPayload {
  name: string
  scopes: string[]
  allowed_agent_ids?: string[]
  expires_at?: string | null
}

/** 列出企业全部机器凭证（含已撤销，便于审计） */
export async function listAgentApiCredentials(): Promise<AgentApiCredential[]> {
  const response = await apiClient.get<ApiResponse<AgentApiCredential[]>>(
    '/agent-api/credentials'
  )
  return response.data.data ?? []
}

/** 创建机器凭证；响应中的 api_key 只出现这一次 */
export async function createAgentApiCredential(
  payload: CreateAgentApiCredentialPayload
): Promise<CreatedAgentApiCredential> {
  const response = await apiClient.post<ApiResponse<CreatedAgentApiCredential>>(
    '/agent-api/credentials',
    payload
  )
  return response.data.data
}

/** 撤销机器凭证（立即生效） */
export async function revokeAgentApiCredential(credentialId: string): Promise<void> {
  await apiClient.post(`/agent-api/credentials/${credentialId}/revoke`)
}
