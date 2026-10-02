import apiClient from './client'
import { Agent, ApiResponse } from '@/types'

export const getAgents = async (): Promise<Agent[]> => {
  // 3.2.4: 后端默认 limit=50，前端显式请求 200 以维持"获取全部"语义
  // （大多数企业 Agent 数 < 200；如超过可后续实现分页 UI）
  const response = await apiClient.get<ApiResponse<Agent[]>>('/agents', {
    params: { limit: 200, offset: 0 },
  })
  return response.data.data
}

export const getAgent = async (id: string): Promise<Agent> => {
  const response = await apiClient.get<ApiResponse<Agent>>(`/agents/${id}`)
  return response.data.data
}

export const deleteAgent = async (id: string): Promise<void> => {
  await apiClient.delete(`/agents/${id}`)
}

export const updateAgent = async (id: string, data: {
  name?: string
  description?: string
  system_prompt?: string
  // P1-FE: 支持保存 Agent 配置（知识库参数等）
  config?: Record<string, unknown>
  // P0-2: 变更说明，写入 AgentVersion.changelog
  changelog?: string
}): Promise<Agent> => {
  const response = await apiClient.put<ApiResponse<Agent>>(`/agents/${id}`, data)
  return response.data.data
}

// P0-2: Agent 版本快照
export interface AgentVersion {
  id: string
  agent_id: string
  version: string
  config_snapshot: {
    name?: string
    description?: string
    system_prompt?: string
    config?: Record<string, unknown>
    version?: string
  }
  changelog: string | null
  is_active: boolean
  created_at: string
}

export const listAgentVersions = async (agentId: string): Promise<AgentVersion[]> => {
  const response = await apiClient.get<ApiResponse<AgentVersion[]>>(`/agents/${agentId}/versions`)
  return response.data.data
}

export const rollbackAgentVersion = async (agentId: string, versionId: string): Promise<Agent> => {
  const response = await apiClient.post<ApiResponse<Agent>>(`/agents/${agentId}/versions/${versionId}/rollback`)
  return response.data.data
}

export const getKnowledgeStats = async (agentId: string): Promise<{
  fileTypeDistribution: { name: string; value: number; color: string }[]
  knowledgeTimeline: { date: string; count: number }[]
  fileStatus: { name: string; value: number }[]
  totalFiles: number
  totalChunks: number
  agentName: string
}> => {
  const response = await apiClient.get<ApiResponse<any>>(`/agents/${agentId}/knowledge-stats`)
  return response.data.data
}

// P0-1c/S1: LangGraph 构建状态快照
export interface BuildState {
  thread_id: string
  current_step: string | null
  status: string | null  // running / paused / completed / failed
  agent_id: string | null
  messages: string[]
  test_result: Record<string, unknown> | null
  next_step: string[] | null
  is_paused: boolean
}

// P3.5: LangGraph 构建请求（POST /agents/build_via_graph）
export interface StartBuildRequest {
  name: string
  description?: string
  folder_path: string
  require_approval?: boolean
}

// P3.5: LangGraph 构建响应（含 thread_id 供 Canvas 轮询）
export interface StartBuildResponse {
  agent_id: string | null
  status: string
  current_step: string | null
  test_result: Record<string, unknown> | null
  messages: string[]
  thread_id: string
}

/**
 * P3.5: 通过 LangGraph 构建 Agent（启动 8 节点流水线）。
 * 返回 thread_id，前端可跳转 /canvas/{thread_id} 查看实时构建状态。
 */
export const startBuild = async (req: StartBuildRequest): Promise<StartBuildResponse> => {
  const response = await apiClient.post<ApiResponse<StartBuildResponse>>(
    '/agents/build_via_graph',
    req,
  )
  return response.data.data
}

export const getBuildState = async (threadId: string): Promise<BuildState> => {
  const response = await apiClient.get<ApiResponse<BuildState>>(`/agents/build/${threadId}/state`)
  return response.data.data
}

// P0-1c 复查补全：恢复 HITL 暂停的构建（审批通过/拒绝）
export const resumeBuild = async (
  threadId: string,
  approved: boolean,
  comment?: string,
): Promise<BuildState> => {
  const response = await apiClient.post<ApiResponse<BuildState>>(
    `/agents/build/${threadId}/resume`,
    { approved, comment },
  )
  return response.data.data
}

// 注：Chat 页面使用 SSE 流式端点 /agents/{id}/chat/stream，不走此函数

// D3-6.7: 文件监听启停（手动控制 watchdog）
export interface FileWatchState {
  agent_id: string
  folder_path?: string
  watching: boolean
}

export const startFileWatching = async (agentId: string): Promise<FileWatchState> => {
  const response = await apiClient.post<ApiResponse<FileWatchState>>(`/agents/${agentId}/watch`)
  return response.data.data
}

export const stopFileWatching = async (agentId: string): Promise<FileWatchState> => {
  const response = await apiClient.delete<ApiResponse<FileWatchState>>(`/agents/${agentId}/watch`)
  return response.data.data
}

// D3-M8: 手动触发增量更新
export interface IncrementalUpdateStats {
  agent_id: string
  folder_path: string
  stats: { added: number; updated: number; deleted: number; unchanged: number }
}

export const triggerIncrementalUpdate = async (agentId: string): Promise<IncrementalUpdateStats> => {
  const response = await apiClient.post<ApiResponse<IncrementalUpdateStats>>(`/agents/${agentId}/incremental-update`)
  return response.data.data
}

// D3→D4: 知识库回滚（D4 合并后可用，未合并时后端返回 501）
export const rollbackAgentKnowledge = async (agentId: string, versionId: string): Promise<Agent> => {
  const response = await apiClient.post<ApiResponse<Agent>>(`/agents/${agentId}/rollback-knowledge/${versionId}`)
  return response.data.data
}


