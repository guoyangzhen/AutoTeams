/**
 * 回环服务生命周期测试。
 *
 * 覆盖 macOS「关窗再打开」（createWindow 重复执行）不能泄漏端口/服务，
 * 以及退出时的关闭路径。关闭逻辑放在 server-lifecycle 里，才能在不开 Electron 的
 * 情况下用真实套接字验证。
 */
import { strict as assert } from 'node:assert'
import { afterEach, beforeEach, describe, it } from 'node:test'
import { activeDesktopServer, ensureDesktopServer, shutdownDesktopServer } from '../server-lifecycle.js'
import { parseUpstreamUrl } from '../upstream.js'
import { createStaticFixture, rawRequest, type StaticFixture } from './helpers.js'

const UNREACHABLE = 'http://127.0.0.1:9'
const TEST_TIMEOUT_MS = 8000

describe('回环服务生命周期', () => {
  let fixture: StaticFixture

  const options = () => ({
    rootDir: fixture.root,
    api: parseUpstreamUrl(UNREACHABLE, 'api'),
    collab: parseUpstreamUrl(UNREACHABLE, 'collab'),
    runtimeConfig: () => ({}),
    port: 0,
  })

  beforeEach(async () => {
    fixture = await createStaticFixture({ 'index.html': '<!doctype html><html><body>root</body></html>' })
  })

  afterEach(async () => {
    await shutdownDesktopServer()
    await fixture.cleanup()
  })

  it('重复 ensure 复用同一实例（origin 不随窗口重建变化）', async () => {
    const first = await ensureDesktopServer(options())
    const second = await ensureDesktopServer(options())
    assert.equal(first, second)
    assert.equal(first.origin, second.origin)
    assert.equal(activeDesktopServer(), first)
  })

  it('并发 ensure 只启动一个服务', async () => {
    const [a, b, c] = await Promise.all([ensureDesktopServer(options()), ensureDesktopServer(options()), ensureDesktopServer(options())])
    assert.equal(a, b)
    assert.equal(b, c)
  })

  it(
    'shutdown 真正释放监听，且可重复调用',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const server = await ensureDesktopServer(options())
      await rawRequest(server.origin, { path: '/health', secFetchSite: null })
      await shutdownDesktopServer()
      assert.equal(activeDesktopServer(), null)
      await assert.rejects(rawRequest(server.origin, { path: '/health', secFetchSite: null }))
      // 幂等：退出流程里可能被多次触发。
      await shutdownDesktopServer()
    },
  )

  it(
    '关闭后重新启动得到新的服务实例',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const first = await ensureDesktopServer(options())
      await shutdownDesktopServer()
      const second = await ensureDesktopServer(options())
      assert.notEqual(second, first)
      const response = await rawRequest(second.origin, { path: '/health', secFetchSite: null })
      assert.equal(response.status, 200)
    },
  )
})
