import apiClient from './client'
import { SetupSession, SetupMessage, SetupPlan, ApiResponse } from '@/types'

export const startSetup = async (folderPath: string, enterpriseId: string): Promise<SetupSession> => {
  const response = await apiClient.post<ApiResponse<SetupSession>>('/setup/start', {
    folder_path: folderPath,
    enterprise_id: enterpriseId
  })
  return response.data.data
}

export const sendSetupMessage = async (sessionId: string, message: string): Promise<SetupMessage> => {
  const response = await apiClient.post<ApiResponse<SetupMessage>>(`/setup/${sessionId}/message`, {
    content: message
  })
  return response.data.data
}

export const getSetupPlan = async (sessionId: string): Promise<SetupPlan> => {
  const response = await apiClient.get<ApiResponse<SetupPlan>>(`/setup/${sessionId}/plan`)
  return response.data.data
}

export const confirmSetupPlan = async (
  sessionId: string,
  confirmed: boolean,
  modifications?: Record<string, unknown>,
  options?: {
    model?: string
    index_strategy?: string
    selected_skills?: string[]
  }
): Promise<{
  session_id: string
  status: string
  plan: Record<string, unknown>
}> => {
  const response = await apiClient.post<ApiResponse<{
    session_id: string
    status: string
    plan: Record<string, unknown>
  }>>(`/setup/${sessionId}/confirm`, {
    confirmed,
    modifications,
    // P1-6.4: 显式传递 Setup 向导差异化参数
    model: options?.model,
    index_strategy: options?.index_strategy,
    selected_skills: options?.selected_skills,
  })
  return response.data.data
}
