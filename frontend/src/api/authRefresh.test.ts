import { describe, expect, it, vi } from 'vitest'
import {
  AuthRefreshCoordinator,
  AuthSessionExpiredError,
  isRefreshEligibleUrl,
  type RefreshableRequest,
} from './authRefresh'

/** 模拟后端明确拒绝（401）。 */
function unauthorized(): { response: { status: number } } {
  return { response: { status: 401 } }
}

/** 模拟网络中断：没有 response。 */
function networkError(): Error {
  return Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' })
}

function httpError(status: number): { response: { status: number } } {
  return { response: { status } }
}

function createCoordinator(refresh: () => Promise<void>) {
  const onSessionExpired = vi.fn()
  const coordinator = new AuthRefreshCoordinator({ refresh, onSessionExpired })
  return { coordinator, onSessionExpired }
}

describe('isRefreshEligibleUrl', () => {
  it('allows /auth/me so a page reload can self-heal an expired access token', () => {
    expect(isRefreshEligibleUrl('/auth/me')).toBe(true)
  })

  it('refuses to refresh credential endpoints whose 401 means "bad credentials"', () => {
    expect(isRefreshEligibleUrl('/auth/login')).toBe(false)
    expect(isRefreshEligibleUrl('/auth/register')).toBe(false)
    expect(isRefreshEligibleUrl('/auth/refresh')).toBe(false)
    expect(isRefreshEligibleUrl('/auth/logout')).toBe(false)
    expect(isRefreshEligibleUrl('/auth/invite')).toBe(false)
  })

  it('does not misjudge lookalike paths as auth endpoints', () => {
    // 旧实现用 includes('/auth/login')，会把 login-logs 误判成登录端点。
    expect(isRefreshEligibleUrl('/users/auth/login-logs')).toBe(true)
    expect(isRefreshEligibleUrl('/auth/me?full=1')).toBe(true)
    expect(isRefreshEligibleUrl('/auth/login-logs')).toBe(true)
  })

  it('treats an unknown url as ineligible rather than refreshing blindly', () => {
    expect(isRefreshEligibleUrl(undefined)).toBe(false)
    expect(isRefreshEligibleUrl('')).toBe(false)
  })
})

describe('AuthRefreshCoordinator', () => {
  it('refreshes exactly once on a 401 and replays the request', async () => {
    const refresh = vi.fn().mockResolvedValue(undefined)
    const { coordinator } = createCoordinator(refresh)
    const request: RefreshableRequest = {}
    const execute = vi.fn().mockResolvedValue('ok')

    await expect(coordinator.runWithRefresh(request, execute)).resolves.toBe('ok')

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(execute).toHaveBeenCalledTimes(1)
    expect(execute).toHaveBeenCalledWith(request)
  })

  it('does not refresh a second time when the replayed request 401s again', async () => {
    const refresh = vi.fn().mockResolvedValue(undefined)
    const { coordinator } = createCoordinator(refresh)
    const request: RefreshableRequest = {}
    const execute = vi.fn().mockResolvedValue('ok')

    await coordinator.runWithRefresh(request, execute)
    // 重放后仍然 401：同一个 config 再次进入 401 分支。
    await expect(coordinator.runWithRefresh(request, execute)).rejects.toBeInstanceOf(
      AuthSessionExpiredError,
    )

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(execute).toHaveBeenCalledTimes(1)
    expect(coordinator.refreshAttempts).toBe(1)
  })

  it('shares a single refresh across concurrent 401s', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const refresh = vi.fn().mockImplementation(() => gate)
    const { coordinator, onSessionExpired } = createCoordinator(refresh)

    const requests: RefreshableRequest[] = [{}, {}, {}, {}]
    const pending = requests.map((request) =>
      coordinator.runWithRefresh(request, vi.fn().mockResolvedValue(request)),
    )
    expect(coordinator.isRefreshing).toBe(true)

    release()
    const results = await Promise.all(pending)

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(coordinator.refreshAttempts).toBe(1)
    expect(results).toHaveLength(4)
    // 每个等待方都拿到了自己的重放结果，而不是共享同一个请求。
    expect(new Set(results).size).toBe(4)
    expect(onSessionExpired).not.toHaveBeenCalled()
  })

  it('clears the session when the refresh itself is rejected by the backend', async () => {
    const refresh = vi.fn().mockRejectedValue(unauthorized())
    const { coordinator, onSessionExpired } = createCoordinator(refresh)

    const results = await Promise.allSettled([
      coordinator.runWithRefresh({} as RefreshableRequest, vi.fn()),
      coordinator.runWithRefresh({} as RefreshableRequest, vi.fn()),
    ])

    expect(results.every((r) => r.status === 'rejected')).toBe(true)
    expect(refresh).toHaveBeenCalledTimes(1)
    // 会话清空只广播一次（4 个并发 401 也只广播一次）。
    expect(onSessionExpired).toHaveBeenCalledTimes(1)
    expect(coordinator.hasExpired).toBe(true)
  })

  it('stops refreshing once the session is known dead', async () => {
    const refresh = vi.fn().mockRejectedValue(unauthorized())
    const { coordinator, onSessionExpired } = createCoordinator(refresh)

    await expect(
      coordinator.runWithRefresh({} as RefreshableRequest, vi.fn()),
    ).rejects.toBeInstanceOf(AuthSessionExpiredError)

    // 后续 401 不再打后端，避免对着已失效的 refresh token 反复刷新。
    await expect(
      coordinator.runWithRefresh({} as RefreshableRequest, vi.fn()),
    ).rejects.toBeInstanceOf(AuthSessionExpiredError)

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(onSessionExpired).toHaveBeenCalledTimes(1)
  })

  it('keeps the session on a transient refresh failure and does not hang', async () => {
    const refresh = vi.fn().mockRejectedValue(networkError())
    const { coordinator, onSessionExpired } = createCoordinator(refresh)

    await expect(
      coordinator.runWithRefresh({} as RefreshableRequest, vi.fn()),
    ).rejects.toThrow('Network Error')

    // 瞬时故障不是会话失效：不清登录态。
    expect(onSessionExpired).not.toHaveBeenCalled()
    expect(coordinator.hasExpired).toBe(false)

    // 后续请求仍可重新尝试刷新，且不会累积挂起的 Promise。
    refresh.mockResolvedValueOnce(undefined)
    await expect(
      coordinator.runWithRefresh({} as RefreshableRequest, vi.fn().mockResolvedValue('ok')),
    ).resolves.toBe('ok')
    expect(coordinator.isRefreshing).toBe(false)
  })

  it('treats 5xx from the refresh endpoint as transient', async () => {
    const refresh = vi.fn().mockRejectedValue(httpError(502))
    const { coordinator, onSessionExpired } = createCoordinator(refresh)

    await expect(
      coordinator.runWithRefresh({} as RefreshableRequest, vi.fn()),
    ).rejects.toBeTruthy()

    expect(onSessionExpired).not.toHaveBeenCalled()
    expect(coordinator.hasExpired).toBe(false)
  })

  it('re-arms refreshing after a successful login', async () => {
    const refresh = vi.fn().mockRejectedValue(unauthorized())
    const { coordinator } = createCoordinator(refresh)
    await expect(
      coordinator.runWithRefresh({} as RefreshableRequest, vi.fn()),
    ).rejects.toBeInstanceOf(AuthSessionExpiredError)
    expect(coordinator.hasExpired).toBe(true)

    refresh.mockResolvedValue(undefined)
    coordinator.reset()

    await expect(
      coordinator.runWithRefresh({} as RefreshableRequest, vi.fn().mockResolvedValue('ok')),
    ).resolves.toBe('ok')
    expect(coordinator.hasExpired).toBe(false)
  })
})
