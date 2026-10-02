/**
 * 双盲反事实影子评估 API 客户端 — AutoTeams 5.0 战役 4。
 *
 * 端点契约（/api/v1/shadow/counterfactual/*）：
 * - POST /sessions                                              提交反事实推演（双盲差分 + 免干预转正裁决）
 * - GET  /sessions?enterprise_id=&employee_badge=&limit=&offset= 推演列表
 * - GET  /sessions/{session_id}                                 会话详情（含四维差分瀑布）
 * - POST /sessions/{session_id}/replay?hourly_labor_rate_yuan=   重放裁决（按新时薪重算净收益）
 * - GET  /promotion-gate?enterprise_id=&employee_badge=         免干预转正准入看板
 * - POST /promotion-gate?enterprise_id=&employee_badge=         重置准入看板（管理员）
 */
import apiClient from './client'
import { ApiResponse, PaginatedResponse } from '@/types'
import type {
  CounterfactualSession,
  CreateCounterfactualSessionRequest,
  PromotionGate,
  ReplayResult,
} from '@/types'
import {
  CounterfactualSessionListSchema,
  PromotionGateSchema,
  parseContract,
} from './schemas'

/** 推演会话列表（GET /shadow/counterfactual/sessions） */
export async function listCounterfactualSessions(
  enterpriseId: string,
  options: { employeeBadge?: string; limit?: number; offset?: number } = {},
): Promise<PaginatedResponse<CounterfactualSession>> {
  const { employeeBadge, limit = 20, offset = 0 } = options
  const response = await apiClient.get<
    ApiResponse<PaginatedResponse<CounterfactualSession>>
  >('/shadow/counterfactual/sessions', {
    params: {
      enterprise_id: enterpriseId,
      employee_badge: employeeBadge,
      limit,
      offset,
    },
  })
  return parseContract(
    CounterfactualSessionListSchema,
    response.data.data,
    'GET /shadow/counterfactual/sessions',
  )
}

/** 提交反事实推演（POST /shadow/counterfactual/sessions） */
export async function createCounterfactualSession(
  req: CreateCounterfactualSessionRequest,
): Promise<CounterfactualSession> {
  const response = await apiClient.post<ApiResponse<CounterfactualSession>>(
    '/shadow/counterfactual/sessions',
    req,
  )
  return response.data.data
}

/** 会话详情（GET /shadow/counterfactual/sessions/{session_id}） */
export async function getCounterfactualSession(
  sessionId: string,
): Promise<CounterfactualSession> {
  const response = await apiClient.get<ApiResponse<CounterfactualSession>>(
    `/shadow/counterfactual/sessions/${sessionId}`,
  )
  return response.data.data
}

/** 重放裁决：按新时薪重算净收益，不落库（收益折现推演） */
export async function replayCounterfactualSession(
  sessionId: string,
  hourlyLaborRateYuan?: number,
): Promise<ReplayResult> {
  const response = await apiClient.post<ApiResponse<ReplayResult>>(
    `/shadow/counterfactual/sessions/${sessionId}/replay`,
    null,
    { params: { hourly_labor_rate_yuan: hourlyLaborRateYuan } },
  )
  return response.data.data
}

/** 免干预转正准入看板（GET /shadow/counterfactual/promotion-gate） */
export async function getPromotionGate(
  enterpriseId: string,
  employeeBadge: string,
): Promise<PromotionGate> {
  const response = await apiClient.get<ApiResponse<PromotionGate>>(
    '/shadow/counterfactual/promotion-gate',
    { params: { enterprise_id: enterpriseId, employee_badge: employeeBadge } },
  )
  return parseContract(
    PromotionGateSchema,
    response.data.data,
    'GET /shadow/counterfactual/promotion-gate',
  )
}

/** 重置准入看板：清空转正标记与连续记录（POST /shadow/counterfactual/promotion-gate） */
export async function resetPromotionGate(
  enterpriseId: string,
  employeeBadge: string,
): Promise<{ enterprise_id: string; employee_badge: string; reset: number }> {
  const response = await apiClient.post<
    ApiResponse<{ enterprise_id: string; employee_badge: string; reset: number }>
  >('/shadow/counterfactual/promotion-gate', null, {
    params: { enterprise_id: enterpriseId, employee_badge: employeeBadge },
  })
  return response.data.data
}
