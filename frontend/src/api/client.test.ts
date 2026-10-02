import { describe, expect, it, beforeEach, vi } from 'vitest'
import axios, {
  AxiosError,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import apiClient, { AUTH_SESSION_EXPIRED_EVENT, resetAuthSession } from './client'
import { getMe } from './auth'
import { resetRuntimeConfig } from '@/config/runtimeConfig'

/**
 * AUD-26 集成测试：驱动真实的 axios 拦截器链（只替换最底层的 adapter），
 * 验证「刷新页面恢复会话 / 无效 refresh 登出 / 并发 401 只刷一次 / 网络故障不循环」。
 */

const USER = { id: 'user-1', enterprise_id: 'ent-1', role: 'member' }

function jsonResponse(
  config: InternalAxiosRequestConfig,
  status: number,
  data: unknown,
): AxiosResponse {
  return { data, status, statusText: 'OK', headers: {}, config }
}

function httpError(
  config: InternalAxiosRequestConfig,
  status: number,
  data: unknown = { message: 'unauthorized' },
): AxiosError {
  return new AxiosError(
    `Request failed with status code ${status}`,
    'ERR_BAD_REQUEST',
    config,
    undefined,
    { data, status, statusText: '', headers: {}, config },
  )
}

function networkError(config: InternalAxiosRequestConfig): AxiosError {
  return new AxiosError('Network Error', 'ERR_NETWORK', config)
}

/**
 * 屏障：等到 N 个请求都真正进入 adapter 才放行。
 * 这是「请求确实并发在途」的真实信号，而不是猜一个 sleep 时长。
 */
function createBarrier(participants: number) {
  let arrived = 0
  let open: () => void = () => {}
  const gate = new Promise<void>((resolve) => {
    open = resolve
  })
  return async function waitAtBarrier(): Promise<void> {
    arrived += 1
    if (arrived >= participants) open()
    await gate
  }
}

/** apiClient 侧的假后端，返回它实际收到的 URL 序列。 */
function serveApi(
  handler: (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>,
): string[] {
  const seen: string[] = []
  const adapter: AxiosAdapter = async (config) => {
    seen.push(config.url ?? '')
    return handler(config)
  }
  apiClient.defaults.adapter = adapter
  return seen
}

/** 全局 axios 侧（刷新走的是裸 axios.post，不经过 apiClient），返回调用计数。 */
function serveRefresh(
  handler: (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>,
): () => number {
  let count = 0
  const adapter: AxiosAdapter = async (config) => {
    count += 1
    return handler(config)
  }
  axios.defaults.adapter = adapter
  return () => count
}

beforeEach(() => {
  resetAuthSession()
  resetRuntimeConfig()
  // 运行时配置拉取：默认给一个空对象，等价于「无运行时覆盖」。
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => ({}) })),
  )
})

describe('401 静默刷新（单航班 / 每请求一次上限）', () => {
  it('刷新页面时 access 过期、refresh 有效 → 会话被恢复（不登出）', async () => {
    let meCalls = 0
    const seen = serveApi(async (config) => {
      meCalls += 1
      // 第一次 access token 过期被拒；刷新成功后的重放放行。
      if (meCalls === 1) throw httpError(config, 401)
      return jsonResponse(config, 200, { success: true, data: USER })
    })
    const refreshCount = serveRefresh(async (config) =>
      jsonResponse(config, 200, { success: true }),
    )

    await expect(getMe()).resolves.toEqual(USER)

    expect(refreshCount()).toBe(1)
    expect(seen).toEqual(['/auth/me', '/auth/me'])
  })

  it('refresh 也失效 → 判定会话失效并登出（且不反复打后端）', async () => {
    const seen = serveApi(async (config) => {
      throw httpError(config, 401)
    })
    const refreshCount = serveRefresh(async (config) => {
      throw httpError(config, 401, { message: 'refresh token revoked' })
    })
    const onExpired = vi.fn()
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, onExpired)

    await expect(getMe()).rejects.toThrow('登录已过期，请重新登录')
    expect(onExpired).toHaveBeenCalledTimes(1)
    // 刷新就失败了，原请求不会被重放。
    expect(seen).toEqual(['/auth/me'])

    // 之后的 401 不再触发刷新（会话已确认失效）——不会形成刷新风暴。
    await expect(getMe()).rejects.toThrow('登录已过期，请重新登录')
    expect(refreshCount()).toBe(1)
    expect(seen).toEqual(['/auth/me', '/auth/me'])
    expect(onExpired).toHaveBeenCalledTimes(1)

    window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, onExpired)
  })

  it('重放后再次 401 → 每个请求最多刷新一次，不无限重试', async () => {
    const seen = serveApi(async (config) => {
      throw httpError(config, 401)
    })
    const refreshCount = serveRefresh(async (config) =>
      jsonResponse(config, 200, { success: true }),
    )

    await expect(getMe()).rejects.toThrow('登录已过期，请重新登录')

    // 一次原请求 + 一次重放；重放仍是 401，按 _authRefreshAttempted 直接失败。
    expect(seen).toEqual(['/auth/me', '/auth/me'])
    expect(refreshCount()).toBe(1)
  })

  it('并发的多个 401 共享同一次刷新', async () => {
    const waitAtBarrier = createBarrier(3)
    const attempts = new Map<string, number>()
    const seen = serveApi(async (config) => {
      const url = config.url ?? ''
      await waitAtBarrier()
      const n = (attempts.get(url) ?? 0) + 1
      attempts.set(url, n)
      if (n === 1) throw httpError(config, 401)
      return jsonResponse(config, 200, { success: true, data: [] })
    })
    const refreshCount = serveRefresh(async (config) =>
      jsonResponse(config, 200, { success: true }),
    )

    const results = await Promise.allSettled([
      apiClient.get('/tasks'),
      apiClient.get('/agents'),
      apiClient.get('/teams'),
    ])

    expect(results.every((r) => r.status === 'fulfilled')).toBe(true)
    expect(refreshCount()).toBe(1)
    expect(seen.sort()).toEqual(['/agents', '/agents', '/tasks', '/tasks', '/teams', '/teams'])
  })

  it('登录端点的 401 不触发刷新（凭据错误刷新也救不回来）', async () => {
    const seen = serveApi(async (config) => {
      throw httpError(config, 401, { message: '用户名或密码错误' })
    })
    const refreshCount = serveRefresh(async (config) =>
      jsonResponse(config, 200, { success: true }),
    )

    await expect(apiClient.post('/auth/login', { username: 'x' })).rejects.toThrow(
      '用户名或密码错误',
    )
    expect(refreshCount()).toBe(0)
    expect(seen).toEqual(['/auth/login'])
  })

  it('网络故障：只做一次幂等重试后失败，既不刷新也不死循环', async () => {
    // 这里刻意走真实的 300ms 网络重试退避（生产代码里的 setTimeout），
    // 目的是证明它有上界；用假定时器反而测不到真实链路。
    const seen = serveApi(async (config) => {
      throw networkError(config)
    })
    const refreshCount = serveRefresh(async (config) =>
      jsonResponse(config, 200, { success: true }),
    )

    await expect(getMe()).rejects.toThrow('Network Error')

    expect(seen).toEqual(['/auth/me', '/auth/me'])
    expect(refreshCount()).toBe(0)
  })

  it('刷新请求本身网络中断：视为瞬时故障，保留登录态，不挂起', async () => {
    const seen = serveApi(async (config) => {
      throw httpError(config, 401)
    })
    const refreshCount = serveRefresh(async (config) => {
      throw networkError(config)
    })
    const onExpired = vi.fn()
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, onExpired)

    await expect(getMe()).rejects.toThrow('Network Error')

    // 瞬时故障不是会话失效：既不广播登出事件，也不把协调器标记为终局失效。
    expect(onExpired).not.toHaveBeenCalled()
    expect(refreshCount()).toBe(1)
    // 失败后没有重放原请求（避免拿着旧 access token 无限重试）。
    expect(seen).toEqual(['/auth/me'])

    window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, onExpired)
  })
})
