/**
 * 隐私优先的产品行为事件工具。
 *
 * 仅用于预定义的产品漏斗事件：不发送提示词、文件名、路径、资源 ID、IP、
 * User-Agent、密钥或任何自由文本。事件写入失败必须静默降级，绝不阻塞用户操作。
 */

import { getAnalyticsUrl } from '@/config/runtimeConfig'

export type CompanyJourneyState =
  | 'needs_compile'
  | 'needs_staff'
  | 'needs_approval'
  | 'needs_attention'
  | 'needs_first_run'
  | 'creating_value'

export type CompanyBriefAction =
  | 'compile'
  | 'staff'
  | 'start_demo'
  | 'review_approvals'
  | 'inspect_execution'
  | 'view_impact'

export type CompanyProductEvent =
  | 'company_brief_viewed'
  | 'company_brief_action_clicked'
  | 'company_tour_started'
  | 'company_tour_step_viewed'
  | 'company_tour_skipped'
  | 'company_tour_completed'
  | 'company_tour_restarted'

interface CompanyProductEventPayload {
  event_name: CompanyProductEvent
  session_id: string
  surface: 'company_overview'
  journey_state?: CompanyJourneyState
  action?: CompanyBriefAction
  tour_step?: number
  reduced_motion?: boolean
}

const SESSION_STORAGE_KEY = 'autoteams_product_session_v1'
const dispatchedEventKeys = new Set<string>()

function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null
  const match = document.cookie.match(new RegExp(`(^| )${name}(?:=([^;]*))?`))
  return match?.[2] ?? null
}

function createSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().replace(/-/g, '')
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 18)}`
}

function getSessionId(): string {
  if (typeof window === 'undefined') return 'server-render'
  const existing = window.sessionStorage.getItem(SESSION_STORAGE_KEY)
  if (existing) return existing
  const sessionId = createSessionId()
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, sessionId)
  return sessionId
}

function prefersReducedMotion(): boolean | undefined {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function getAnalyticsEndpoint(): string {
  // AUD-27: 读取运行时 API 基础地址（config.json → VITE_* → 同源 /api/v1），
  // 不再直接引用构建时内联的 import.meta.env。
  const configuredBase = getAnalyticsUrl().replace(/\/$/, '')
  // 兼容两种现有部署约定：`/api/v1` 或 `https://api.example.com`。
  // 不能假设环境变量总是包含版本前缀，否则容器部署会把事件发往错误路径。
  const apiBase = configuredBase.endsWith('/api/v1') ? configuredBase : `${configuredBase}/api/v1`
  return `${apiBase}/product-analytics/events`
}

/**
 * 静默上报受限事件。使用 fetch 而非全局 axios 客户端，避免分析服务短暂不可用时
 * 触发全局错误 toast；Cookie/CSRF 仍按普通浏览器 API 边界发送。
 */
export function trackCompanyProductEvent(
  eventName: CompanyProductEvent,
  fields: Omit<CompanyProductEventPayload, 'event_name' | 'session_id' | 'surface' | 'reduced_motion'> = {},
): void {
  if (typeof window === 'undefined') return

  const payload: CompanyProductEventPayload = {
    event_name: eventName,
    session_id: getSessionId(),
    surface: 'company_overview',
    reduced_motion: prefersReducedMotion(),
    ...fields,
  }
  const csrfToken = getCookie('csrf_token')

  void fetch(getAnalyticsEndpoint(), {
    method: 'POST',
    credentials: 'include',
    keepalive: true,
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
    body: JSON.stringify(payload),
  }).catch(() => {
    // 分析不可用不能影响任务创建、审批或导航；不将错误写入用户可见区域。
  })
}

/** 仅在当前 SPA 生命周期内发送一次，防止 effect 重跑造成重复漏斗计数。 */
export function trackCompanyProductEventOnce(
  key: string,
  eventName: CompanyProductEvent,
  fields: Omit<CompanyProductEventPayload, 'event_name' | 'session_id' | 'surface' | 'reduced_motion'> = {},
): void {
  if (dispatchedEventKeys.has(key)) return
  dispatchedEventKeys.add(key)
  trackCompanyProductEvent(eventName, fields)
}
