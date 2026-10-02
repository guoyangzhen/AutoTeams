import apiClient from './client'
import { ApiResponse } from '@/types'

/**
 * 审计日志 API 客户端。
 *
 * 对应后端 GET /audit-logs（分页查询）与 GET /audit-logs/export（CSV 导出）。
 * 管理员权限专用，普通用户调用会收到 403。
 *
 * 合规价值：把"180 天审计日志保留"从文档变成可点击证据，
 * 满足等保 2.0 与企业内部审计要求。
 */

/** 单条审计日志记录 */
export interface AuditLog {
  id: string
  user_id: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  ip_address: string | null
  user_agent: string | null
  details: Record<string, unknown> | null
  created_at: string
}

/** 分页列表响应 */
export interface AuditLogList {
  total: number
  logs: AuditLog[]
  limit: number
  offset: number
}

/** 查询参数 */
export interface AuditLogQuery {
  action?: string
  resource_type?: string
  user_id?: string
  start_date?: string
  end_date?: string
  keyword?: string
  limit?: number
  offset?: number
}

/** 分页查询审计日志 */
export async function getAuditLogs(query: AuditLogQuery = {}): Promise<AuditLogList> {
  const params: Record<string, string | number> = {}
  if (query.action) params.action = query.action
  if (query.resource_type) params.resource_type = query.resource_type
  if (query.user_id) params.user_id = query.user_id
  if (query.start_date) params.start_date = query.start_date
  if (query.end_date) params.end_date = query.end_date
  if (query.keyword) params.keyword = query.keyword
  if (query.limit !== undefined) params.limit = query.limit
  if (query.offset !== undefined) params.offset = query.offset

  const response = await apiClient.get<ApiResponse<AuditLogList>>('/audit-logs', { params })
  return response.data.data
}

/** 导出审计日志为 CSV（触发浏览器下载） */
export async function exportAuditLogs(query: AuditLogQuery = {}): Promise<void> {
  const params: Record<string, string> = {}
  if (query.action) params.action = query.action
  if (query.resource_type) params.resource_type = query.resource_type
  if (query.user_id) params.user_id = query.user_id
  if (query.start_date) params.start_date = query.start_date
  if (query.end_date) params.end_date = query.end_date
  if (query.keyword) params.keyword = query.keyword

  // CSV 导出走 blob，避免 JSON 拦截器干扰
  const response = await apiClient.get('/audit-logs/export', {
    params,
    responseType: 'blob',
  })

  // 创建下载链接
  const blob = new Blob([response.data as BlobPart], { type: 'text/csv;charset=utf-8-sig' })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  link.download = `audit_logs_${timestamp}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}

/** 链式完整性验证结果 */
export interface AuditChainVerifyResult {
  valid: boolean
  checked: number
  broken_at: string | null
  message: string
}

/**
 * 验证审计日志链的完整性（5.3.7 HMAC 链式防篡改）。
 *
 * 激活后端 GET /audit-logs/verify 端点 + utils/audit.py:verify_audit_chain。
 * 演示价值：把"HMAC 链式防篡改"从代码注释变成可点击证据，
 * 答辩时可现场点击验证、展示审计链未被篡改。
 *
 * 3.5.2: 后端改用 GET 实现（纯查询操作，符合 RESTful；GET 天然豁免 CSRF）。
 *
 * @param limit 验证最近 N 条（0 表示全部）
 */
export async function verifyAuditChain(limit: number = 0): Promise<AuditChainVerifyResult> {
  const response = await apiClient.get<ApiResponse<AuditChainVerifyResult>>(
    '/audit-logs/verify',
    { params: { limit } },
  )
  return response.data.data
}
