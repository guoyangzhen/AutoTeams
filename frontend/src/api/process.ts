import apiClient from './client'
import { ProcessingTask, ApiResponse } from '@/types'

export const startProcess = async (data: {
  folder_path: string
  enterprise_id: string
  agent_name: string
  agent_description?: string
  // P1-FE: Setup 向导收集的附加配置
  model?: string
  skills?: string[]
  index_strategy?: string
}): Promise<ProcessingTask> => {
  const response = await apiClient.post<ApiResponse<ProcessingTask>>('/process/start', data)
  return response.data.data
}

export const getProgress = async (taskId: string): Promise<ProcessingTask> => {
  const response = await apiClient.get<ApiResponse<ProcessingTask>>(`/process/${taskId}`)
  return response.data.data
}

export const getReport = async (taskId: string): Promise<{
  task_id: string
  status: string
  summary: string
  files_processed: number
  files_failed: number
  knowledge_count: number
  processing_time_seconds: number
}> => {
  const response = await apiClient.get<ApiResponse<{
    task_id: string
    status: string
    summary: string
    files_processed: number
    files_failed: number
    knowledge_count: number
    processing_time_seconds: number
  }>>(`/process/${taskId}/report`)
  return response.data.data
}

// P1-FE: 取消处理任务（后端 P2-2 已实现协作式取消）
export const cancelTask = async (taskId: string): Promise<ProcessingTask> => {
  const response = await apiClient.post<ApiResponse<ProcessingTask>>(`/process/${taskId}/cancel`)
  return response.data.data
}

// P1-11: 接入 GET /process 任务列表（后端 P1-8 已实现）
export interface ProcessListResponse {
  tasks: ProcessingTask[]
  total: number
}

export const listTasks = async (params?: {
  status?: string
  limit?: number
  offset?: number
}): Promise<ProcessListResponse> => {
  const response = await apiClient.get<ApiResponse<ProcessListResponse>>('/process', {
    params,
  })
  return response.data.data
}

