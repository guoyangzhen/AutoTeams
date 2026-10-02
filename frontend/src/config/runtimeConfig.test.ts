import { describe, expect, it, beforeEach, vi } from 'vitest'
import {
  DEFAULT_API_BASE_URL,
  getAnalyticsUrl,
  getApiBaseUrl,
  getCollabWsUrl,
  loadRuntimeConfig,
  resetRuntimeConfig,
} from './runtimeConfig'

/**
 * AUD-27 单元测试：运行时 config.json 覆盖构建时 VITE_*，并可安全降级。
 * 构建时 VITE_API_BASE_URL 在 vitest 下为 undefined，因此回退基线是 /api/v1。
 */

function stubConfigJson(body: unknown, ok = true) {
  const fetchMock = vi.fn(async () => ({
    ok,
    json: async () => body,
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => {
  resetRuntimeConfig()
})

describe('loadRuntimeConfig', () => {
  it('容器运行时配置的 apiBaseUrl 覆盖同源默认值', async () => {
    stubConfigJson({ apiBaseUrl: 'https://api.example.com/api/v1' })
    await loadRuntimeConfig()

    expect(getApiBaseUrl()).toBe('https://api.example.com/api/v1')
    expect(getAnalyticsUrl()).toBe('https://api.example.com/api/v1')
  })

  it('未提供运行时覆盖时回退到同源 /api/v1', async () => {
    stubConfigJson({})

    await loadRuntimeConfig()

    expect(getApiBaseUrl()).toBe(DEFAULT_API_BASE_URL)
  })

  it('去掉结尾斜杠，避免拼接出双斜杠', async () => {
    stubConfigJson({ apiBaseUrl: 'https://api.example.com/api/v1/' })

    await loadRuntimeConfig()

    expect(getApiBaseUrl()).toBe('https://api.example.com/api/v1')
  })

  it('config.json 缺失（HTTP 404）时降级，不阻塞启动', async () => {
    stubConfigJson({}, false)

    await expect(loadRuntimeConfig()).resolves.toEqual({})
    expect(getApiBaseUrl()).toBe(DEFAULT_API_BASE_URL)
  })

  it('config.json 内容不是对象时降级', async () => {
    stubConfigJson(['not', 'an', 'object'])

    await expect(loadRuntimeConfig()).resolves.toEqual({})
    expect(getApiBaseUrl()).toBe(DEFAULT_API_BASE_URL)
  })

  it('请求异常时降级', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline')
      }),
    )

    await expect(loadRuntimeConfig()).resolves.toEqual({})
    expect(getApiBaseUrl()).toBe(DEFAULT_API_BASE_URL)
  })

  it('并发调用共享同一次拉取', async () => {
    const fetchMock = stubConfigJson({ apiBaseUrl: '/api/v1' })

    await Promise.all([loadRuntimeConfig(), loadRuntimeConfig(), loadRuntimeConfig()])

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('显式配置的协作 WebSocket 覆盖同源推导', async () => {
    stubConfigJson({ collabWsUrl: 'wss://collab.example.com/ws' })

    await loadRuntimeConfig()

    expect(getCollabWsUrl()).toBe('wss://collab.example.com/ws')
  })

  it('未配置协作 WebSocket 时按当前 origin 推导（对应 nginx 反代）', async () => {
    stubConfigJson({})

    await loadRuntimeConfig()

    const expected = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/collab-ws`
    expect(getCollabWsUrl()).toBe(expected)
  })
})
