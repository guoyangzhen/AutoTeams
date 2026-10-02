/**
 * 进化层（Evolution）API 客户端 — 对应 spec.md §10.7 WT4 端点。
 *
 * 端点契约（/api/v1/evolution/*）：
 * - GET  /evolution/{enterprise_id}/suggestions        建议列表（分页）
 * - POST /evolution/suggestions/{id}/apply              应用建议
 * - POST /evolution/suggestions/{id}/reject            拒绝建议
 * - GET  /evolution/{enterprise_id}/metrics             指标
 * - GET  /evolution/{enterprise_id}/timeline           Evolution Timeline（分页）
 */
import apiClient from './client'
import { ApiResponse, PaginatedResponse } from '@/types'
import type {
  AdvisorSuggestion,
  ApplySuggestionResponse,
  RejectSuggestionRequest,
  RejectSuggestionResponse,
  OrgMetrics,
  EvolutionTimelineItem,
  MaturityRating,
  OptimizationHistoryItem,
  ApplyOptimizationResponse,
  RollbackCheckResult,
} from '@/types'
import {
  mockDelay,
  mockSuggestions,
  mockOrgMetrics,
  mockEvolutionTimeline,
} from './mockData'
import {
  AdvisorSuggestionListSchema,
  ApplySuggestionResponseSchema,
  GenerateSuggestionsResponseSchema,
  MaturityRatingSchema,
  OptimizationListSchema,
  OrgMetricsSchema,
  RejectSuggestionResponseSchema,
  TimelineListSchema,
  parseContract,
} from './schemas'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** 建议列表（GET /evolution/{enterprise_id}/suggestions?limit=&offset=） */
export async function listSuggestions(
  enterpriseId: string,
  limit = 50,
  offset = 0,
): Promise<PaginatedResponse<AdvisorSuggestion>> {
  if (USE_MOCK) {
    return mockDelay({ items: mockSuggestions, total: mockSuggestions.length })
  }
  const response = await apiClient.get<ApiResponse<PaginatedResponse<AdvisorSuggestion>>>(
    `/evolution/${enterpriseId}/suggestions`,
    { params: { limit, offset } },
  )
  return parseContract(
    AdvisorSuggestionListSchema,
    response.data.data,
    'GET /evolution/suggestions',
  )
}

/** 应用建议（POST /evolution/suggestions/{id}/apply） */
export async function applySuggestion(id: string): Promise<ApplySuggestionResponse> {
  if (USE_MOCK) {
    return mockDelay({ applied: true, affected_agents: ['agent-sales-001'] })
  }
  const response = await apiClient.post<ApiResponse<ApplySuggestionResponse>>(
    `/evolution/suggestions/${id}/apply`,
  )
  return parseContract(
    ApplySuggestionResponseSchema,
    response.data.data,
    'POST /evolution/suggestions/apply',
  )
}

/** 拒绝建议（POST /evolution/suggestions/{id}/reject） */
export async function rejectSuggestion(
  id: string,
  req: RejectSuggestionRequest,
): Promise<RejectSuggestionResponse> {
  if (USE_MOCK) {
    return mockDelay({ rejected: true })
  }
  const response = await apiClient.post<ApiResponse<RejectSuggestionResponse>>(
    `/evolution/suggestions/${id}/reject`,
    req,
  )
  return parseContract(
    RejectSuggestionResponseSchema,
    response.data.data,
    'POST /evolution/suggestions/reject',
  )
}

/** 组织分析指标（GET /evolution/{enterprise_id}/metrics?period=） */
export async function getOrgMetrics(
  enterpriseId: string,
  period = '30d',
): Promise<OrgMetrics> {
  if (USE_MOCK) {
    return mockDelay({ ...mockOrgMetrics })
  }
  const response = await apiClient.get<ApiResponse<OrgMetrics>>(
    `/evolution/${enterpriseId}/metrics`,
    { params: { period } },
  )
  return parseContract(OrgMetricsSchema, response.data.data, 'GET /evolution/metrics')
}

/** Evolution Timeline（GET /evolution/{enterprise_id}/timeline?limit=&offset=） */
export async function getEvolutionTimeline(
  enterpriseId: string,
  limit = 50,
  offset = 0,
): Promise<PaginatedResponse<EvolutionTimelineItem>> {
  if (USE_MOCK) {
    return mockDelay({ items: mockEvolutionTimeline, total: mockEvolutionTimeline.length })
  }
  const response = await apiClient.get<ApiResponse<PaginatedResponse<EvolutionTimelineItem>>>(
    `/evolution/${enterpriseId}/timeline`,
    { params: { limit, offset } },
  )
  return parseContract(
    TimelineListSchema,
    response.data.data,
    'GET /evolution/timeline',
  )
}

/** 触发生成优化建议（POST /evolution/{enterprise_id}/suggestions/generate） */
export async function generateSuggestions(
  enterpriseId: string,
): Promise<{ suggestions: AdvisorSuggestion[]; count: number }> {
  if (USE_MOCK) {
    return mockDelay({ suggestions: mockSuggestions, count: mockSuggestions.length })
  }
  const response = await apiClient.post<
    ApiResponse<{ suggestions: AdvisorSuggestion[]; count: number }>
  >(`/evolution/${enterpriseId}/suggestions/generate`)
  return parseContract(
    GenerateSuggestionsResponseSchema,
    response.data.data,
    'POST /evolution/suggestions/generate',
  )
}

/** 成熟度评级（GET /evolution/{enterprise_id}/maturity） */
export async function getMaturity(enterpriseId: string): Promise<MaturityRating> {
  if (USE_MOCK) {
    return mockDelay({
      level: 'L2',
      name: '协作',
      description: 'AI 执行部分，人类审批关键节点',
      achieved: true,
      dimensions: {
        agent_count: 6,
        automation_rate: 0.42,
        human_intervention: 0.3,
        collaboration_events: 18,
        approval_gates: 3,
      },
    })
  }
  const response = await apiClient.get<ApiResponse<MaturityRating>>(
    `/evolution/${enterpriseId}/maturity`,
  )
  return parseContract(MaturityRatingSchema, response.data.data, 'GET /evolution/maturity')
}

/** 优化历史（GET /evolution/{enterprise_id}/optimizations?limit=&offset=） */
export async function listOptimizations(
  enterpriseId: string,
  limit = 20,
  offset = 0,
): Promise<PaginatedResponse<OptimizationHistoryItem>> {
  if (USE_MOCK) {
    return mockDelay({ items: [], total: 0 })
  }
  const response = await apiClient.get<
    ApiResponse<PaginatedResponse<OptimizationHistoryItem>>
  >(`/evolution/${enterpriseId}/optimizations`, { params: { limit, offset } })
  return parseContract(
    OptimizationListSchema,
    response.data.data,
    'GET /evolution/optimizations',
  )
}

/** 应用优化项（POST /evolution/{enterprise_id}/optimizations/{agent_id}/apply?optimization_id=） */
export async function applyOptimization(
  enterpriseId: string,
  agentId: string,
  optimizationId: string,
): Promise<ApplyOptimizationResponse> {
  const response = await apiClient.post<ApiResponse<ApplyOptimizationResponse>>(
    `/evolution/${enterpriseId}/optimizations/${agentId}/apply`,
    null,
    { params: { optimization_id: optimizationId } },
  )
  return response.data.data
}

/** 质量退化检查 + 自动回滚（POST /evolution/{enterprise_id}/rollback-check/{agent_id}） */
export async function checkAndRollback(
  enterpriseId: string,
  agentId: string,
): Promise<RollbackCheckResult> {
  const response = await apiClient.post<ApiResponse<RollbackCheckResult>>(
    `/evolution/${enterpriseId}/rollback-check/${agentId}`,
  )
  return response.data.data
}
