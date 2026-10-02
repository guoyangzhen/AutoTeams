import apiClient from '@/api/client'
import type { ApiResponse } from '@/types'

/** 企业管理员可读取的首页价值路径聚合漏斗。仅包含匿名会话级计数。 */
export interface CompanyActionSummary {
  window_days: number
  total_events: number
  unique_sessions: number
  brief_views: number
  brief_action_clicks: number
  tour_started: number
  tour_completed: number
  tour_skipped: number
  action_clicks: Record<string, number>
  journey_states: Record<string, number>
  generated_at: string
}

/**
 * 读取经营行动简报和首次导览的企业级聚合数据。
 *
 * 后端仅允许当前企业管理员访问；调用方应将 403 视为“当前角色不显示洞察”，
 * 而不是普通业务错误，以免成员用户看到无意义的失败提示。
 */
export async function getCompanyActionSummary(days: number = 30): Promise<CompanyActionSummary> {
  const response = await apiClient.get<ApiResponse<CompanyActionSummary>>(
    '/product-analytics/company-action-summary',
    { params: { days } },
  )
  return response.data.data
}
