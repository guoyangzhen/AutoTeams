/**
 * AUD-26：401 静默刷新的单航班（single-flight）协调器。
 *
 * 修复的缺陷：/auth/me 被列入「禁止自动刷新」端点，access token 过期而
 * refresh token 仍有效时，刷新页面直接被登出。
 *
 * 约束（均由 tests/api/authRefresh.test.ts 覆盖）：
 * 1. 单个请求最多触发一次刷新（重放后再次 401 直接失败）；
 * 2. 并发 401 共享同一次刷新（single-flight），不会形成刷新风暴；
 * 3. 刷新失败按「是否终局」区分：401/403 视为会话失效并清空登录态；
 *    网络中断/5xx 视为瞬时故障，保留登录态，等待下一次机会；
 * 4. 瞬时故障不产生死等：每个请求各自 reject。
 */

export class AuthSessionExpiredError extends Error {
  readonly code = 'AUTH_SESSION_EXPIRED'

  constructor(message = '登录已过期，请重新登录') {
    super(message)
    this.name = 'AuthSessionExpiredError'
  }
}

/** 401 时**禁止**触发静默刷新的端点：这些端点的 401 意味着凭据本身无效，刷新救不回来。 */
const NO_REFRESH_PATTERNS = [
  '/auth/login',
  '/auth/register',
  '/auth/refresh',
  '/auth/logout',
  '/auth/invite',
]

/**
 * 精确路径匹配，避免子串误判：旧实现用 `url.includes(p)`，
 * 会把 `/users/auth/login-logs` 误判成 `/auth/login`。
 */
export function isRefreshEligibleUrl(url: string | undefined): boolean {
  if (!url) return false
  const path = url.split('?')[0]
  return !NO_REFRESH_PATTERNS.some((p) => path === p || path.startsWith(p + '/'))
}

export interface RefreshableRequest {
  /** 重放后再次 401 时据此拒绝，保证「每个请求最多一次刷新」。 */
  _authRefreshAttempted?: boolean
}

export interface AuthRefreshCoordinatorOptions {
  /** 真正发起刷新（POST /auth/refresh）。只会被并发调用方共享执行一次。 */
  refresh: () => Promise<void>
  /** 判定刷新失败是否代表会话彻底失效。默认：仅 401/403 视为终局。 */
  isSessionTerminal?: (error: unknown) => boolean
  /** 会话终局失效时回调一次（用于清空登录态）。 */
  onSessionExpired?: (error: unknown) => void
}

function defaultIsSessionTerminal(error: unknown): boolean {
  const response = (error as { response?: { status?: number } } | null)?.response
  const status = response?.status
  return status === 401 || status === 403
}

export class AuthRefreshCoordinator {
  private inflight: Promise<void> | null = null
  private expired = false
  private attempts = 0

  constructor(private readonly options: AuthRefreshCoordinatorOptions) {}

  get isRefreshing(): boolean {
    return this.inflight !== null
  }

  /** true 表示已确认会话不可恢复，后续 401 不再尝试刷新。 */
  get hasExpired(): boolean {
    return this.expired
  }

  /** 实际发起的刷新次数（测试与诊断用）。 */
  get refreshAttempts(): number {
    return this.attempts
  }

  /** 登录成功后调用：清掉终局失效标记，让后续 401 重新可以刷新。 */
  reset(): void {
    this.expired = false
    this.attempts = 0
  }

  /**
   * 单航班刷新：第一个调用方发起刷新，之后所有并发调用方等待同一个 Promise。
   * 刷新结束后 in-flight 被清空，后续的 401 可以发起新的刷新尝试。
   */
  refreshOnce(): Promise<void> {
    if (this.inflight) return this.inflight
    // Promise.resolve().then(...) 把真正的 refresh() 调用推迟一个微任务，
    // 保证下面 this.inflight 的赋值一定先于任何递归进入。
    const inflight = Promise.resolve()
      .then(() => {
        this.attempts += 1
        return this.options.refresh()
      })
      .catch((error: unknown) => {
        const isTerminal = (this.options.isSessionTerminal ?? defaultIsSessionTerminal)(error)
        if (isTerminal) {
          this.expired = true
          this.options.onSessionExpired?.(error)
        }
        throw error
      })
    this.inflight = inflight
    // 无论成败都清空 in-flight，后续的 401 才能发起新的刷新尝试。
    // 用 then(清理, 清理) 而不是 finally()，避免派生出一个无人处理的 rejected promise。
    const clearInflight = () => {
      if (this.inflight === inflight) this.inflight = null
    }
    inflight.then(clearInflight, clearInflight)
    return inflight
  }

  /**
   * 401 处理入口：刷新一次，然后重放原请求。
   *
   * @param request    原请求配置（会被打上 `_authRefreshAttempted` 标记）
   * @param execute    重放函数
   * @throws AuthSessionExpiredError 会话已失效
   */
  async runWithRefresh<TRequest extends RefreshableRequest, TResult>(
    request: TRequest,
    execute: (request: TRequest) => Promise<TResult>,
  ): Promise<TResult> {
    // 上限：同一请求只刷新一次；会话已确认失效时直接失败，不再打后端。
    if (this.expired || request._authRefreshAttempted) {
      throw new AuthSessionExpiredError()
    }
    request._authRefreshAttempted = true
    try {
      await this.refreshOnce()
    } catch (error) {
      if (this.expired) throw new AuthSessionExpiredError()
      // 瞬时故障（网络中断 / 5xx）：保留登录态，把真实原因抛给调用方。
      throw error
    }
    return execute(request)
  }
}
