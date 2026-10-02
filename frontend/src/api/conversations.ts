import apiClient from './client'
import { Conversation, Message, ApiResponse } from '@/types'

export const getConversations = async (agentId?: string): Promise<Conversation[]> => {
  const response = await apiClient.get<ApiResponse<Conversation[]>>('/conversations', {
    params: agentId ? { agent_id: agentId } : undefined
  })
  return response.data.data
}

export const getConversation = async (id: string): Promise<Conversation> => {
  const response = await apiClient.get<ApiResponse<Conversation>>(`/conversations/${id}`)
  return response.data.data
}

export const createConversation = async (agentId: string, title?: string): Promise<Conversation> => {
  const response = await apiClient.post<ApiResponse<Conversation>>('/conversations', {
    agent_id: agentId,
    title: title || '新协作'
  })
  return response.data.data
}

export const getMessages = async (conversationId: string): Promise<Message[]> => {
  const response = await apiClient.get<ApiResponse<Conversation>>(`/conversations/${conversationId}`)
  return response.data.data.messages || []
}

export const updateSatisfaction = async (messageId: string, satisfaction: string): Promise<Message> => {
  const response = await apiClient.put<ApiResponse<Message>>(`/conversations/messages/${messageId}/satisfaction`, {
    satisfaction
  })
  return response.data.data
}

/**
 * 协作管理（激活 update_conversation / delete_conversation 端点）。
 * 在 Chat.tsx 侧边栏协作列表添加重命名/删除按钮即可激活。
 */

/** 更新协作标题 */
export async function updateConversation(
  conversationId: string,
  title: string,
): Promise<Conversation> {
  const response = await apiClient.put<ApiResponse<Conversation>>(
    `/conversations/${conversationId}`,
    { title },
  )
  return response.data.data
}

/** 删除协作及其所有消息（HTTP 204，无返回体） */
export async function deleteConversation(conversationId: string): Promise<void> {
  await apiClient.delete(`/conversations/${conversationId}`)
}

/**
 * 隐式满意度上报（激活 record_implicit_feedback 端点）。
 *
 * 触发场景：
 * - copy: 用户点击 AI 消息的"复制"按钮 → implicit:satisfied:copy
 * - regenerate: 用户点击"重新生成" → implicit:unsatisfied:regenerate
 * - dwell: 用户停留 < 3 秒就切走 → implicit:unsatisfied:dwell
 *
 * 不会覆盖已存在的显式满意度（satisfied* / unsatisfied*）。
 */
export type ImplicitFeedbackSignal = 'copy' | 'regenerate' | 'dwell'

export async function recordImplicitFeedback(
  messageId: string,
  payload: { signal: ImplicitFeedbackSignal; dwell_seconds?: number },
): Promise<Message> {
  const response = await apiClient.post<ApiResponse<Message>>(
    `/conversations/messages/${messageId}/implicit-feedback`,
    payload,
  )
  return response.data.data
}
