/**
 * 访谈（Interview）API 客户端 — 对应 spec.md §10.7 WT4 端点。
 *
 * 端点契约（/api/v1/interview/*）：
 * - POST /interview/sessions                  启动访谈
 * - GET  /interview/sessions/{id}             会话状态
 * - GET  /interview/sessions/{id}/next-question  下一问
 * - POST /interview/sessions/{id}/answers     提交回答
 */
import apiClient from './client'
import { ApiResponse } from '@/types'
import type {
  InterviewSession,
  InterviewQuestion,
  InterviewAnswerRequest,
  InterviewAnswerResponse,
} from '@/types'
import { mockDelay, mockInterviewSession, mockInterviewQuestions } from './mockData'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** 启动访谈（POST /interview/sessions） */
export async function startSession(enterpriseId: string): Promise<InterviewSession> {
  if (USE_MOCK) {
    return mockDelay({ ...mockInterviewSession, enterprise_id: enterpriseId })
  }
  const response = await apiClient.post<ApiResponse<InterviewSession>>('/interview/sessions', {
    enterprise_id: enterpriseId,
  })
  return response.data.data
}

/** 会话状态（GET /interview/sessions/{id}） */
export async function getSession(sessionId: string): Promise<InterviewSession> {
  if (USE_MOCK) {
    return mockDelay({ ...mockInterviewSession, session_id: sessionId })
  }
  const response = await apiClient.get<ApiResponse<InterviewSession>>(
    `/interview/sessions/${sessionId}`,
  )
  return response.data.data
}

/** 下一问（GET /interview/sessions/{id}/next-question） */
export async function getNextQuestion(sessionId: string): Promise<InterviewQuestion> {
  if (USE_MOCK) {
    // Mock：按已回答数返回下一题（answered_count 后端 StartSessionResponse 不返回，Mock 提供）
    const idx = mockInterviewSession.answered_count ?? 0
    const question = mockInterviewQuestions[idx] || mockInterviewQuestions[0]
    return mockDelay({ ...question })
  }
  const response = await apiClient.get<ApiResponse<InterviewQuestion>>(
    `/interview/sessions/${sessionId}/next-question`,
  )
  return response.data.data
}

/** 提交回答（POST /interview/sessions/{id}/answers） */
export async function submitAnswer(
  sessionId: string,
  req: InterviewAnswerRequest,
): Promise<InterviewAnswerResponse> {
  if (USE_MOCK) {
    const nextIdx = (mockInterviewSession.answered_count ?? 0) + 1
    const nextQuestion = mockInterviewQuestions[nextIdx] || null
    return mockDelay({
      updated_completeness: Math.min(100, (mockInterviewSession.completeness ?? 0) + 3),
      next_question: nextQuestion,
    })
  }
  const response = await apiClient.post<ApiResponse<InterviewAnswerResponse>>(
    `/interview/sessions/${sessionId}/answers`,
    req,
  )
  return response.data.data
}
