import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios'
import {
  AuthRefreshCoordinator,
  AuthSessionExpiredError,
  isRefreshEligibleUrl,
  type RefreshableRequest,
} from './authRefresh'
import {
  getApiBaseUrl,
  isRuntimeConfigLoaded,
  loadRuntimeConfig,
} from '@/config/runtimeConfig'
import { dispatchAppError } from '@/utils/errors'

// P3-1: 维护最近一次后端返回的 request_id，供 ErrorBoundary 等诊断 UI 读取
let lastRequestId: string | undefined

export function getLastRequestId(): string | undefined {
  return lastRequestId
}

// BE-SEC-01: 从 document.cookie 读取指定名称的值
function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(^| )' + name + '(?:=([^;]*))?'))
  return match?.[2] ?? null
}

// P1-1: 启用跨域 Cookie（HttpOnly access/refresh token 由浏览器自动携带）
// AUD-27: 这里**不**写死 baseURL。构建时的 VITE_API_BASE_URL 已被内联进产物，
// 无法被容器环境变量改写；baseURL 改由请求拦截器在运行时解析
// （config.json → VITE_API_BASE_URL → 同源 /api/v1）。
const apiClient = axios.create({
  timeout: 60000,
  withCredentials: true,
  // 注意：不要在此处设置默认 Content-Type。axios 会根据请求体自动选择：
  // - 普通对象 → application/json
  // - FormData → multipart/form-data（并自动附带 boundary）
  // 若这里写死 application/json，文件夹上传（FormData）会被错误序列化，
  // 导致后端「请求参数验证失败」。
})

// P1-1 + BE-SEC-01: token 已存 HttpOnly Cookie；状态变更请求自动附加 CSRF token
apiClient.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    // AUD-27: 首次请求前确保运行时配置已就绪；就绪后走同步路径，零额外开销。
    if (!config.baseURL) {
      if (!isRuntimeConfigLoaded()) {
        await loadRuntimeConfig()
      }
      config.baseURL = getApiBaseUrl()
    }
    const method = config.method?.toLowerCase() || ''
    if (['post', 'put', 'patch', 'delete'].includes(method)) {
      const csrfToken = getCookie('csrf_token')
      if (csrfToken) {
        config.headers['X-CSRF-Token'] = csrfToken
      }
    }
    return config
  },
  (error: AxiosError) => {
    return Promise.reject(error)
  }
)

// P0-12 + AUD-26: 401 自动刷新 token 机制
// - access token 过期时，自动用 refresh token 换取新 token 并重试原请求
// - 并发 401 共享同一次刷新（single-flight），不会刷新风暴
// - 每个请求最多刷新一次，重放后仍 401 即判定会话失效
// - 刷新失败（401/403）视为终局失效并广播事件，由 AuthProvider 清空登录态
// - 刷新失败（网络/5xx）视为瞬时故障，保留登录态
// 注意：/auth/me **不在**禁止刷新的名单里 —— 会话探测必须能自愈，
// 否则 access 过期 + refresh 有效时刷新页面就被登出。
async function performTokenRefresh(): Promise<void> {
  // P1-1: refresh token 在 HttpOnly Cookie 中，请求体为空即可
  // P0-S4: /auth/refresh 同样需要 CSRF token（Double Submit Cookie）
  const csrfToken = getCookie('csrf_token')
  // url 为相对路径，与 baseURL 拼接；不可再带 /api/v1 前缀，否则解析为
  // /api/v1/api/v1/auth/refresh 导致刷新恒 404、用户被误判为登录失效
  const refreshResponse = await axios.post(
    '/auth/refresh',
    {},
    {
      withCredentials: true,
      baseURL: getApiBaseUrl(),
      // 刷新超时会直接卡住所有并发 401 的重放，单独收紧到 15s。
      timeout: 15000,
      headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : undefined,
    }
  )
  if (!refreshResponse.data?.success) {
    throw new Error('刷新 token 响应异常')
  }
}

/** 会话终局失效事件；AuthProvider 订阅后清空登录态并跳转登录页。 */
export const AUTH_SESSION_EXPIRED_EVENT = 'auth-session-expired'

const authRefreshCoordinator = new AuthRefreshCoordinator({
  refresh: performTokenRefresh,
  isSessionTerminal: (error) => {
    // 只有后端明确拒绝（401/403）才代表 refresh token 已失效。
    // 网络中断、超时、5xx 都是瞬时故障，不能据此登出用户。
    const status = (error as { response?: { status?: number } } | null)?.response?.status
    return status === 401 || status === 403
  },
  onSessionExpired: () => {
    window.dispatchEvent(new CustomEvent(AUTH_SESSION_EXPIRED_EVENT))
  },
})

/** 登录成功后调用：解除「会话已终局失效」标记。 */
export function resetAuthSession(): void {
  authRefreshCoordinator.reset()
}

apiClient.interceptors.response.use(
  (response) => {
    // P3-1: 保存后端返回的 request_id，便于错误排查与链路追踪
    const requestId = response.headers['x-request-id'] as string | undefined
    if (requestId) {
      lastRequestId = requestId
      const extendedConfig = response.config as InternalAxiosRequestConfig & {
        requestId?: string
      }
      extendedConfig.requestId = requestId
    }
    return response
  },
  async (error: AxiosError<{ message?: string }>) => {
    interface ExtendedRequestConfig extends InternalAxiosRequestConfig, RefreshableRequest {
      _networkRetry?: boolean
      requestId?: string
    }
    const originalRequest = error.config as ExtendedRequestConfig | undefined
    const url = originalRequest?.url || ''

    // P3-1: 从响应头提取 request_id，附加到错误对象并更新全局记录
    const requestId = error.response?.headers['x-request-id'] as string | undefined
    if (requestId) {
      lastRequestId = requestId
    }
    if (originalRequest && requestId) {
      originalRequest.requestId = requestId
    }

    // 5.0 鲁棒性增强：对瞬时网络中断或超时，幂等 GET 请求自动进行 1 次平滑重试（300ms 抖动补偿）
    if (
      !error.response &&
      originalRequest &&
      originalRequest.method?.toLowerCase() === 'get' &&
      !originalRequest._networkRetry
    ) {
      originalRequest._networkRetry = true
      await new Promise((resolve) => setTimeout(resolve, 300))
      return apiClient(originalRequest)
    }

    // 无 HTTP 响应：请求被取消 / 网络中断 / HMR 重载 / 页面导航中断
    // 这类错误通常是瞬时的，不弹错误提示、不写入控制台，静默拒绝即可
    if (!error.response) {
      return Promise.reject(new Error(error.message || '网络请求中断'))
    }

    // AUD-26: 401 → 静默刷新一次后重放原请求。
    // single-flight + 每请求一次上限由 AuthRefreshCoordinator 保证。
    if (error.response.status === 401 && originalRequest && isRefreshEligibleUrl(url)) {
      try {
        return await authRefreshCoordinator.runWithRefresh(originalRequest, (config) =>
          apiClient(config),
        )
      } catch (refreshError) {
        const sessionExpired = refreshError instanceof AuthSessionExpiredError
        const message = sessionExpired
          ? refreshError.message
          : (refreshError as Error)?.message || '请求失败'
        const wrapped = new Error(
          requestId && sessionExpired ? `${message} (RequestId: ${requestId})` : message,
        )
        ;(wrapped as Error & { requestId?: string }).requestId = requestId
        return Promise.reject(wrapped)
      }
    }

    // 非 401 错误，或 auth 端点 401（凭据本身无效，刷新无意义），直接透传
    const message = error.response?.data?.message || error.message || '请求失败'
    // P1-FE: 对非 401 的 API 错误发出全局提示（401 由路由守卫统一处理登录态）
    if (error.response?.status !== 401) {
      dispatchAppError(message)
    }
    const wrappedError = new Error(requestId ? `${message} (RequestId: ${requestId})` : message)
    ;(wrappedError as Error & { requestId?: string }).requestId = requestId
    return Promise.reject(wrappedError)
  }
)

export default apiClient
