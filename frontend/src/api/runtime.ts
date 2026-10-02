/**
 * Runtime API 客户端 — 对应 spec.md §10.7 WT2 端点。
 *
 * 端点契约（/api/v1/runtime/*）：
 * - GET  /runtime/{enterprise_id}                  当前 Runtime
 * - GET  /runtime/{enterprise_id}/versions        版本列表（分页）
 * - GET  /runtime/{enterprise_id}/versions/{version}  按版本获取
 * - POST /runtime/{enterprise_id}/rollback          回滚
 * - GET  /runtime/{enterprise_id}/diff              版本 diff
 * - GET  /runtime/{enterprise_id}/organization      组织运行时
 * - GET  /runtime/{enterprise_id}/agents            Agent 模板列表
 * - GET  /runtime/{enterprise_id}/processes         流程引擎列表
 */
import apiClient from './client'
import { ApiResponse, PaginatedResponse } from '@/types'
import type {
  RuntimeCompileResult,
  RuntimeVersionSummary,
  RuntimeDiff,
  RollbackRequest,
  RollbackResponse,
  RuntimeOrganization,
  AgentConfigTemplate,
  ProcessEngineInstance,
} from '@/types'
import {
  mockDelay,
  mockRuntime,
  mockRuntimeVersions,
  mockRuntimeDiff,
} from './mockData'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** 当前 Runtime（GET /runtime/{enterprise_id}） */
export async function getRuntime(enterpriseId: string): Promise<RuntimeCompileResult> {
  if (USE_MOCK) {
    return mockDelay({ ...mockRuntime })
  }
  const response = await apiClient.get<ApiResponse<RuntimeCompileResult>>(
    `/runtime/${enterpriseId}`,
  )
  return response.data.data
}

/** 版本列表（GET /runtime/{enterprise_id}/versions?limit=&offset=） */
export async function listRuntimeVersions(
  enterpriseId: string,
  limit = 20,
  offset = 0,
): Promise<PaginatedResponse<RuntimeVersionSummary>> {
  if (USE_MOCK) {
    return mockDelay({ items: mockRuntimeVersions, total: mockRuntimeVersions.length })
  }
  const response = await apiClient.get<ApiResponse<PaginatedResponse<RuntimeVersionSummary>>>(
    `/runtime/${enterpriseId}/versions`,
    { params: { limit, offset } },
  )
  return response.data.data
}

/** 按版本获取 Runtime（GET /runtime/{enterprise_id}/versions/{version}） */
export async function getRuntimeVersion(
  enterpriseId: string,
  version: string,
): Promise<RuntimeCompileResult> {
  if (USE_MOCK) {
    return mockDelay({ ...mockRuntime, version })
  }
  const response = await apiClient.get<ApiResponse<RuntimeCompileResult>>(
    `/runtime/${enterpriseId}/versions/${version}`,
  )
  return response.data.data
}

/** 回滚（POST /runtime/{enterprise_id}/rollback） */
export async function rollbackRuntime(
  enterpriseId: string,
  req: RollbackRequest,
): Promise<RollbackResponse> {
  if (USE_MOCK) {
    // status 对齐后端实际返回 'rolled_back'（非 'success'）
    return mockDelay({ new_active_version: req.target_version, status: 'rolled_back', runtime_id: 'rt-mock-001' })
  }
  const response = await apiClient.post<ApiResponse<RollbackResponse>>(
    `/runtime/${enterpriseId}/rollback`,
    req,
  )
  return response.data.data
}

/** 版本 diff（GET /runtime/{enterprise_id}/diff?a=&b=） */
export async function diffRuntimeVersions(
  enterpriseId: string,
  versionA: string,
  versionB: string,
): Promise<RuntimeDiff> {
  if (USE_MOCK) {
    return mockDelay({ ...mockRuntimeDiff })
  }
  const response = await apiClient.get<ApiResponse<RuntimeDiff>>(
    `/runtime/${enterpriseId}/diff`,
    { params: { a: versionA, b: versionB } },
  )
  return response.data.data
}

/** 组织运行时（GET /runtime/{enterprise_id}/organization） */
export async function getOrganization(enterpriseId: string): Promise<RuntimeOrganization> {
  if (USE_MOCK) {
    return mockDelay({ ...mockRuntime.organization })
  }
  const response = await apiClient.get<ApiResponse<RuntimeOrganization>>(
    `/runtime/${enterpriseId}/organization`,
  )
  return response.data.data
}

/** Agent 模板列表（GET /runtime/{enterprise_id}/agents） */
export async function getAgentTemplates(
  enterpriseId: string,
): Promise<{ agents: AgentConfigTemplate[] }> {
  if (USE_MOCK) {
    return mockDelay({ agents: mockRuntime.agents })
  }
  const response = await apiClient.get<ApiResponse<{ agents: AgentConfigTemplate[] }>>(
    `/runtime/${enterpriseId}/agents`,
  )
  return response.data.data
}

/** 流程引擎列表（GET /runtime/{enterprise_id}/processes） */
export async function getProcessEngines(
  enterpriseId: string,
): Promise<{ processes: ProcessEngineInstance[] }> {
  if (USE_MOCK) {
    return mockDelay({ processes: mockRuntime.process_engines })
  }
  const response = await apiClient.get<ApiResponse<{ processes: ProcessEngineInstance[] }>>(
    `/runtime/${enterpriseId}/processes`,
  )
  return response.data.data
}
