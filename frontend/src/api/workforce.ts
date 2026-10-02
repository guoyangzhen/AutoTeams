/**
 * Workforce API 客户端 — 对应 spec.md §10.7 WT3 端点。
 *
 * 端点契约（/api/v1/workforce/*）：
 * - POST /workforce/generate                         触发生成（返回推荐）
 * - POST /workforce/confirm                          确认推荐（批量创建）
 * - GET  /workforce/{enterprise_id}                  列出 Workforce（分页）
 * - GET  /workforce/{agent_id}/lifecycle             生命周期状态
 * - POST /workforce/{agent_id}/transition            阶段转换
 * - GET  /workforce/{agent_id}/memory/{conversation_id}  查询记忆
 */
import apiClient from './client'
import { ApiResponse, PaginatedResponse } from '@/types'
import type {
  WorkforceGenerateRequest,
  WorkforceGenerateResponse,
  WorkforceConfirmRequest,
  WorkforceConfirmResponse,
  AgentRunMetrics,
  LifecycleState,
  LifecycleTransitionRequest,
  LifecycleTransitionResponse,
  AgentMemory,
} from '@/types'
import {
  mockDelay,
  mockWorkforceGenerate,
  mockWorkforceConfirm,
  mockAgentRunMetrics,
  mockLifecycleState,
  mockAgentMemory,
} from './mockData'
import { WorkforceListSchema, parseContract } from './schemas'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** 触发生成（返回推荐列表，POST /workforce/generate） */
export async function generateWorkforce(
  req: WorkforceGenerateRequest,
): Promise<WorkforceGenerateResponse> {
  if (USE_MOCK) {
    return mockDelay({ ...mockWorkforceGenerate })
  }
  const response = await apiClient.post<ApiResponse<WorkforceGenerateResponse>>(
    '/workforce/generate',
    req,
  )
  return response.data.data
}

/** 确认推荐（批量创建，POST /workforce/confirm） */
export async function confirmWorkforce(
  req: WorkforceConfirmRequest,
): Promise<WorkforceConfirmResponse> {
  if (USE_MOCK) {
    return mockDelay({ ...mockWorkforceConfirm })
  }
  const response = await apiClient.post<ApiResponse<WorkforceConfirmResponse>>(
    '/workforce/confirm',
    req,
  )
  return response.data.data
}

/** 列出 Workforce（GET /workforce/{enterprise_id}?limit=&offset=） */
export async function listWorkforce(
  enterpriseId: string,
  limit = 50,
  offset = 0,
): Promise<PaginatedResponse<AgentRunMetrics>> {
  if (USE_MOCK) {
    return mockDelay({ items: mockAgentRunMetrics, total: mockAgentRunMetrics.length })
  }
  const response = await apiClient.get<ApiResponse<PaginatedResponse<AgentRunMetrics>>>(
    `/workforce/${enterpriseId}`,
    { params: { limit, offset } },
  )
  return parseContract(WorkforceListSchema, response.data.data, 'GET /workforce')
}

/** 生命周期状态（GET /workforce/{agent_id}/lifecycle） */
export async function getLifecycle(agentId: string): Promise<LifecycleState> {
  if (USE_MOCK) {
    return mockDelay({ ...mockLifecycleState })
  }
  const response = await apiClient.get<ApiResponse<LifecycleState>>(
    `/workforce/${agentId}/lifecycle`,
  )
  return response.data.data
}

/** 阶段转换（POST /workforce/{agent_id}/transition） */
export async function transitionLifecycle(
  agentId: string,
  req: LifecycleTransitionRequest,
): Promise<LifecycleTransitionResponse> {
  if (USE_MOCK) {
    return mockDelay({ new_stage: req.target_stage, status: 'success' })
  }
  const response = await apiClient.post<ApiResponse<LifecycleTransitionResponse>>(
    `/workforce/${agentId}/transition`,
    req,
  )
  return response.data.data
}

/** 查询记忆（GET /workforce/{agent_id}/memory/{conversation_id}） */
export async function getAgentMemory(
  agentId: string,
  conversationId: string,
): Promise<AgentMemory> {
  if (USE_MOCK) {
    return mockDelay({ ...mockAgentMemory })
  }
  const response = await apiClient.get<ApiResponse<AgentMemory>>(
    `/workforce/${agentId}/memory/${conversationId}`,
  )
  return response.data.data
}

/** 编排方法项（P0-2） */
export interface OrchestrationMethod {
  key: string
  label: string
  /** active=当前可用；roadmap=规划中 */
  status: 'active' | 'roadmap'
}

/** 编排能力摘要（P0-2） */
export interface OrchestrationSummary {
  methods: OrchestrationMethod[]
  default_method: string
  default_method_label: string
  complexity: string[]
}

/** 编排能力摘要（GET /workforce/{enterprise_id}/orchestration） */
export async function getOrchestration(
  enterpriseId: string,
): Promise<OrchestrationSummary> {
  const response = await apiClient.get<ApiResponse<OrchestrationSummary>>(
    `/workforce/${enterpriseId}/orchestration`,
  )
  return response.data.data
}
