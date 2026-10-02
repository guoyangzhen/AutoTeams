import apiClient from './client'
import { ApiResponse } from '@/types'

/**
 * 协作式 Agent 创建 Copilot API。
 *
 * 端点 POST /setup/copilot（无尾斜杠，避免 307 丢 Authorization 头）。
 * 用户在 Home 对话框说一句话即可创建 Agent，跳过 7 步 Setup 向导。
 */

export interface CopilotResult {
  status: string
  thread_id: string
  agent_id?: string
  agent_name: string
  redirect_url: string
  build_status?: string
  message?: string
}

/**
 * 通过自然语言消息创建 Agent。
 *
 * @param message 用户自然语言消息，如 "用 D:\公司文档 做一个 HR 答疑助手"
 * @param sessionId 可选会话 ID，用于将来串联多轮协作
 * @returns 创建结果，含 thread_id 与跳转链接 redirect_url
 */
export const createAgentViaCopilot = async (
  message: string,
  sessionId?: string,
): Promise<CopilotResult> => {
  const response = await apiClient.post<ApiResponse<CopilotResult>>('/setup/copilot', {
    message,
    session_id: sessionId ?? null,
  })
  return response.data.data
}
