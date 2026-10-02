/**
 * 回环渲染服务的行为验收。
 *
 * 覆盖 AUD-28 的核心断点：随包资源解析、路由回退、运行时后端地址与鉴权连接方式，
 * 以及「不成为开放代理」的边界。全部走真实套接字，不 mock http。
 */
import { strict as assert } from 'node:assert'
import { after, before, describe, it } from 'node:test'
import { WebSocket, WebSocketServer } from 'ws'
import { startDesktopServer, type DesktopServer } from '../loopback-server.js'
import { parseUpstreamUrl } from '../upstream.js'
import {
  createStaticFixture,
  deferred,
  rawRequest,
  startFakeUpstream,
  type Deferred,
  type FakeUpstream,
  type StaticFixture,
} from './helpers.js'

const FIXTURE_FILES: Record<string, string> = {
  'index.html':
    '<!doctype html><html><head><script type="module" src="/assets/index-DxKplZ14.js"></script></head><body><div id="root"></div></body></html>',
  'assets/index-DxKplZ14.js': 'console.log("app-bundle")\n',
  'config.json': '{}',
  'local-runner/install.ps1': '# runner installer\n',
}

const TEST_TIMEOUT_MS = 8000
const UNREACHABLE = 'http://127.0.0.1:9'

describe('桌面端回环渲染服务', () => {
  let fixture: StaticFixture
  let api: FakeUpstream
  let collab: FakeUpstream
  let server: DesktopServer
  let streamTail: Deferred<void>
  let collabWsPaths: string[]

  before(async () => {
    fixture = await createStaticFixture(FIXTURE_FILES)
    streamTail = deferred<void>()
    api = await startFakeUpstream((req, res) => {
      if (req.url === '/api/v1/stream') {
        res.writeHead(200, { 'content-type': 'text/event-stream' })
        res.write('data: first\n\n')
        // 第二段由测试在「客户端确实收到第一段」之后才放行：
        // 代理若整体缓冲，这个 promise 永远不会 resolve，测试直接超时失败。
        void streamTail.promise.then(() => {
          res.write('data: second\n\n')
          res.end()
        })
        return
      }
      if (req.url === '/api/v1/login') {
        res.setHeader('set-cookie', [
          'access_token=a; Path=/; HttpOnly; Secure; SameSite=Strict',
          'refresh_token=r; Path=/; HttpOnly; Secure; SameSite=Strict',
          'csrf_token=c; Path=/; Secure; SameSite=Strict',
        ])
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ ok: true }))
        return
      }
      if (req.url === '/api/v1/redirect-external') {
        res.writeHead(302, { location: 'http://evil.example.com/collect' })
        res.end()
        return
      }
      if (req.url === '/api/v1/redirect-local') {
        res.writeHead(302, { location: '/api/v1/login' })
        res.end()
        return
      }
      res.writeHead(404, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ detail: 'not found' }))
    })

    collabWsPaths = []
    collab = await startFakeUpstream(
      (req, res) => {
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ path: req.url }))
      },
      (httpServer) => {
        const wss = new WebSocketServer({ server: httpServer })
        wss.on('connection', (socket, request) => {
          collabWsPaths.push(request.url ?? '')
          socket.on('message', (data) => socket.send(`echo:${data.toString()}`))
        })
      },
    )

    server = await startDesktopServer({
      rootDir: fixture.root,
      api: parseUpstreamUrl(api.origin, 'api'),
      collab: parseUpstreamUrl(collab.origin, 'collab'),
      runtimeConfig: (origin) => ({ apiBaseUrl: '/api/v1', collabWsUrl: `${origin.replace(/^http/, 'ws')}/collab-ws` }),
      port: 0,
    })
  })

  after(async () => {
    await server.close()
    await api.close()
    await collab.close()
    await fixture.cleanup()
  })

  it('绝对路径资源按包内目录解析，而不是盘符根目录', async () => {
    const page = await rawRequest(server.origin, { path: '/' })
    assert.equal(page.status, 200)
    assert.match(page.headers['content-type'] ?? '', /text\/html/)

    const bundle = await rawRequest(server.origin, { path: '/assets/index-DxKplZ14.js' })
    assert.equal(bundle.status, 200)
    assert.match(bundle.headers['content-type'] ?? '', /javascript/)
    assert.equal(bundle.body, FIXTURE_FILES['assets/index-DxKplZ14.js'])
  })

  it('深链刷新回退到 index.html，query 不影响路由', async () => {
    const deep = await rawRequest(server.origin, {
      path: '/dashboard/enterprise-42?tab=sop',
      headers: { accept: 'text/html,application/xhtml+xml' },
    })
    assert.equal(deep.status, 200)
    assert.equal(deep.body, FIXTURE_FILES['index.html'])
  })

  it('XHR 风格的 404 不会被 SPA 回退吞掉', async () => {
    const missing = await rawRequest(server.origin, {
      path: '/api/v1/does-not-exist',
      headers: { accept: 'text/html' },
    })
    assert.equal(missing.status, 404)
    assert.equal(missing.body.includes('<div id="root">'), false)
  })

  it('API 请求转发到固定上游，方法/路径/请求体原样送达', async () => {
    const response = await rawRequest(server.origin, {
      path: '/api/v1/login',
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{"email":"a@b.c"}',
    })
    assert.equal(response.status, 200)
    const recorded = api.requests.at(-1)
    assert.ok(recorded)
    assert.equal(recorded.method, 'POST')
    assert.equal(recorded.url, '/api/v1/login')
    assert.equal(recorded.body, '{"email":"a@b.c"}')
    assert.equal(recorded.headers.host, `127.0.0.1:${api.port}`)
  })

  it(
    'Cookie 与 CSRF 头原样往返，代理不改写任何鉴权语义',
    async () => {
      const response = await rawRequest(server.origin, {
        path: '/api/v1/login',
        method: 'POST',
        headers: {
          cookie: 'access_token=existing; csrf_token=xyz',
          'x-csrf-token': 'xyz',
          'content-type': 'application/json',
        },
        body: '{}',
      })
      const recorded = api.requests.at(-1)
      assert.ok(recorded)
      assert.equal(recorded.headers.cookie, 'access_token=existing; csrf_token=xyz')
      assert.equal(recorded.headers['x-csrf-token'], 'xyz')
      assert.equal(response.status, 200)
    },
  )

  it('Set-Cookie 的 Secure/SameSite/HttpOnly 原样下发，不在代理层剥离', async () => {
    const response = await rawRequest(server.origin, { path: '/api/v1/login' })
    const cookies = response.headers['set-cookie']
    assert.ok(Array.isArray(cookies))
    assert.equal(cookies.length, 3)
    assert.equal(cookies[0], 'access_token=a; Path=/; HttpOnly; Secure; SameSite=Strict')
    assert.equal(cookies[1], 'refresh_token=r; Path=/; HttpOnly; Secure; SameSite=Strict')
    assert.equal(cookies[2], 'csrf_token=c; Path=/; Secure; SameSite=Strict')
  })

  it('Connection 头点名的逐跳首部不会夹带到上游', async () => {
    await rawRequest(server.origin, {
      path: '/api/v1/login',
      headers: { connection: 'x-internal-token', 'x-internal-token': 'should-not-travel' },
    })
    const recorded = api.requests.at(-1)
    assert.ok(recorded)
    assert.equal(recorded.headers['x-internal-token'], undefined)
    // Node 客户端会用 agent:false 自行补上 Connection: close，
    // 这里只要求 Connection 不再是客户端点名的那个逐跳 token。
    assert.notEqual(recorded.headers.connection, 'x-internal-token')
  })

  it('浏览器侧的同源首部不回传给上游', async () => {
    await rawRequest(server.origin, { path: '/api/v1/login' })
    const recorded = api.requests.at(-1)
    assert.ok(recorded)
    assert.equal(recorded.headers.origin, undefined)
    assert.equal(recorded.headers['sec-fetch-site'], undefined)
  })

  it(
    'SSE 在上游结束前就把首块送达客户端（代理不缓冲）',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const firstChunk = deferred<void>()
      const responsePromise = rawRequest(server.origin, {
        path: '/api/v1/stream',
        onChunk: (chunk) => {
          if (chunk.toString().includes('first')) firstChunk.resolve()
        },
      })
      await firstChunk.promise
      streamTail.resolve()
      const response = await responsePromise
      assert.equal(response.status, 200)
      assert.equal(response.body.includes('data: first'), true)
      assert.equal(response.body.includes('data: second'), true)
    },
  )

  it('拒绝上游的跨源重定向', async () => {
    const response = await rawRequest(server.origin, { path: '/api/v1/redirect-external' })
    assert.equal(response.status, 502)
    assert.match(response.body, /跨源重定向/)
    assert.equal(response.headers.location, undefined)
  })

  it('同源重定向原样透传给渲染层', async () => {
    const response = await rawRequest(server.origin, { path: '/api/v1/redirect-local' })
    assert.equal(response.status, 302)
    assert.equal(response.headers.location, '/api/v1/login')
  })

  it('上游不可达时返回 502 而不是挂起', async () => {
    const isolated = await startDesktopServer({
      rootDir: fixture.root,
      // 保留端口 9（discard）：一定连不上，确保 502 分支被真实触发。
      api: parseUpstreamUrl(UNREACHABLE, 'api'),
      collab: parseUpstreamUrl(UNREACHABLE, 'collab'),
      runtimeConfig: () => ({}),
      port: 0,
    })
    try {
      const response = await rawRequest(isolated.origin, { path: '/api/v1/anything' })
      assert.equal(response.status, 502)
      assert.match(response.body, /上游不可达/)
    } finally {
      await isolated.close()
    }
  })

  it(
    '上游在响应中途断开时，客户端不会一直挂起',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const flaky = await startFakeUpstream((_req, res) => {
        res.writeHead(200, { 'content-type': 'text/event-stream' })
        res.write('data: start\n\n')
        // 上游在写收尾块之前断开：客户端必须 settle（由用例超时兜底），
        // 且不能收到上游崩溃后才「凭空出现」的数据。
        res.socket?.destroy()
      })
      const isolated = await startDesktopServer({
        rootDir: fixture.root,
        api: parseUpstreamUrl(flaky.origin, 'api'),
        collab: parseUpstreamUrl(flaky.origin, 'collab'),
        runtimeConfig: () => ({}),
        port: 0,
      })
      try {
        const outcome = await rawRequest(isolated.origin, { path: '/api/v1/flaky' }).then(
          (response) => response.body,
          () => '<aborted>',
        )
        assert.equal(outcome.includes('data: end'), false, `上游断开后仍收到收尾数据：${outcome}`)
      } finally {
        await isolated.close()
        await flaky.close()
      }
    },
  )

  it('拒绝 absolute-form 请求行，客户端不能指定上游', async () => {
    const response = await rawRequest(server.origin, { path: 'http://evil.example.com/api/v1/leak' })
    assert.equal(response.status, 400)
    assert.equal(
      api.requests.some((item) => item.url.includes('evil.example.com')),
      false,
    )
  })

  it('拒绝 authority-form（双斜杠）请求目标', async () => {
    const response = await rawRequest(server.origin, { path: '//evil.example.com/api/v1/leak' })
    assert.equal(response.status, 400)
  })

  it('Host 头不匹配时拒绝（防 DNS rebinding）', async () => {
    const response = await rawRequest(server.origin, { path: '/api/v1/me', host: 'evil.example.com' })
    assert.equal(response.status, 403)
    assert.match(response.body, /Host/)
  })

  it('缺少 Sec-Fetch-Site 的非浏览器客户端被拒绝', async () => {
    const response = await rawRequest(server.origin, { path: '/api/v1/me', secFetchSite: null })
    assert.equal(response.status, 403)
    assert.match(response.body, /Sec-Fetch-Site/)
  })

  it('跨站来源（Sec-Fetch-Site: cross-site）被拒绝', async () => {
    const response = await rawRequest(server.origin, { path: '/api/v1/me', secFetchSite: 'cross-site' })
    assert.equal(response.status, 403)
  })

  it('状态变更请求缺少同源 Origin 时被拒绝', async () => {
    const response = await rawRequest(server.origin, {
      path: '/api/v1/login',
      method: 'POST',
      origin: null,
      body: '{}',
    })
    assert.equal(response.status, 403)
    assert.match(response.body, /Origin/)
  })

  it('状态变更请求 Origin 指向别的站点时被拒绝', async () => {
    const response = await rawRequest(server.origin, {
      path: '/api/v1/login',
      method: 'POST',
      origin: 'http://evil.example.com',
      body: '{}',
    })
    assert.equal(response.status, 403)
  })

  it('查询参数不能把请求变成通用代理', async () => {
    const before = api.requests.length
    const response = await rawRequest(server.origin, { path: '/api/v1/me?target=http://evil.example.com' })
    assert.equal(response.status, 404)
    const recorded = api.requests[before]
    assert.equal(recorded.url, '/api/v1/me?target=http://evil.example.com')
    assert.equal(recorded.headers.host, `127.0.0.1:${api.port}`)
  })

  it('编码后的路径穿越拿不到包外文件', async () => {
    // WHATWG URL 会把 %2e%2e 当成点段归一化掉，落到 /package.json（包内不存在）。
    // 关键性质是「取不到包外内容」，具体状态码由归一化结果决定。
    const response = await rawRequest(server.origin, { path: '/%2e%2e/package.json' })
    assert.equal(response.status, 404)
    assert.equal(response.body.includes('autoteams-desktop'), false)
  })

  it('config.json 指向同源代理与回环 WebSocket', async () => {
    const response = await rawRequest(server.origin, { path: '/config.json' })
    assert.equal(response.status, 200)
    const config = JSON.parse(response.body) as Record<string, string>
    assert.equal(config.apiBaseUrl, '/api/v1')
    assert.equal(config.collabWsUrl, `${server.origin.replace(/^http/, 'ws')}/collab-ws`)
  })

  it('/health 不要求浏览器来源头，便于装机后命令行确认监听', async () => {
    const response = await rawRequest(server.origin, { path: '/health', secFetchSite: null })
    assert.equal(response.status, 200)
    assert.deepEqual(JSON.parse(response.body), { status: 'ok' })
  })

  it('/health 仍然要求正确的 Host', async () => {
    const response = await rawRequest(server.origin, { path: '/health', secFetchSite: null, host: 'evil.example.com' })
    assert.equal(response.status, 403)
  })

  it('非 GET/HEAD 请求不会落到静态资源路由', async () => {
    const response = await rawRequest(server.origin, { path: '/index.html', method: 'POST' })
    assert.equal(response.status, 404)
  })

  it('/collab-api 改写为上游 /api', async () => {
    const before = collab.requests.length
    const response = await rawRequest(server.origin, { path: '/collab-api/v1/sessions' })
    assert.equal(response.status, 200)
    assert.equal(collab.requests[before].url, '/api/v1/sessions')
  })

  it(
    'WebSocket 升级透传到固定上游 /ws',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const opened = new WebSocket(`${server.origin.replace(/^http/, 'ws')}/collab-ws?sessionId=1`, {
        headers: { origin: server.origin },
      })
      const echoed = await new Promise<string>((resolve, reject) => {
        opened.on('open', () => opened.send('ping'))
        opened.on('message', (data) => resolve(data.toString()))
        opened.on('error', reject)
      })
      assert.equal(echoed, 'echo:ping')
      assert.equal(collabWsPaths.at(-1), '/ws?sessionId=1')
      opened.close()
    },
  )

  it(
    'WebSocket 的 Origin 不匹配时被拒绝',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const outcome = await websocketOutcome(`${server.origin.replace(/^http/, 'ws')}/collab-ws`, {
        origin: 'http://evil.example.com',
      })
      assert.notEqual(outcome, 'opened')
    },
  )

  it(
    'WebSocket 缺少 Origin 时被拒绝',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const outcome = await websocketOutcome(`${server.origin.replace(/^http/, 'ws')}/collab-ws`, {})
      assert.notEqual(outcome, 'opened')
    },
  )

  it(
    'WebSocket 的 Host 不匹配时被拒绝',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const outcome = await websocketOutcome(`${server.origin.replace(/^http/, 'ws')}/collab-ws`, {
        origin: server.origin,
        host: 'evil.example.com',
      })
      assert.notEqual(outcome, 'opened')
    },
  )

  it(
    'close() 会断开活跃的 WebSocket 隧道并让 close 完成',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const isolatedCollab = await startFakeUpstream(
        (_req, res) => {
          res.writeHead(200, { 'content-type': 'application/json' })
          res.end('{}')
        },
        (httpServer) => {
          const wss = new WebSocketServer({ server: httpServer })
          wss.on('connection', (socket) => socket.on('message', (data) => socket.send(`echo:${data.toString()}`)))
        },
      )
      const isolated = await startDesktopServer({
        rootDir: fixture.root,
        api: parseUpstreamUrl(UNREACHABLE, 'api'),
        collab: parseUpstreamUrl(isolatedCollab.origin, 'collab'),
        runtimeConfig: () => ({}),
        port: 0,
      })
      const socket = new WebSocket(`${isolated.origin.replace(/^http/, 'ws')}/collab-ws`, {
        headers: { origin: isolated.origin },
      })
      await new Promise<void>((resolve, reject) => {
        socket.on('open', () => resolve())
        socket.on('error', reject)
      })
      const closed = deferred<void>()
      socket.on('close', () => closed.resolve())
      await isolated.close()
      // 不显式 destroy 隧道的话，close() 会一直挂着等这条连接结束。
      await closed.promise
      await isolatedCollab.close()
    },
  )

  it(
    'WebSocket 只能升级固定的 /collab-ws 路径',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const outcome = await websocketOutcome(`${server.origin.replace(/^http/, 'ws')}/api/v1/stream`, {
        origin: server.origin,
      })
      assert.equal(outcome, 'http-404')
    },
  )
})

describe('回环服务生命周期', () => {
  let fixture: StaticFixture

  before(async () => {
    fixture = await createStaticFixture(FIXTURE_FILES)
  })

  after(async () => {
    await fixture.cleanup()
  })

  it('固定端口被占用时退回随机端口，且不会误用占用者', async () => {
    const first = await startDesktopServer({
      rootDir: fixture.root,
      api: parseUpstreamUrl(UNREACHABLE, 'api'),
      collab: parseUpstreamUrl(UNREACHABLE, 'collab'),
      runtimeConfig: () => ({}),
      port: 0,
    })
    try {
      const second = await startDesktopServer({
        rootDir: fixture.root,
        api: parseUpstreamUrl(UNREACHABLE, 'api'),
        collab: parseUpstreamUrl(UNREACHABLE, 'collab'),
        runtimeConfig: () => ({}),
        port: first.port,
      })
      try {
        assert.notEqual(second.port, first.port)
        // 两个服务各自独立：第二个的 Host 校验只认自己的端口。
        const response = await rawRequest(second.origin, { path: '/api/v1/me', host: `127.0.0.1:${first.port}` })
        assert.equal(response.status, 403)
      } finally {
        await second.close()
      }
    } finally {
      await first.close()
    }
  })

  it(
    'close() 会断开存量连接并让监听真正释放',
    { timeout: TEST_TIMEOUT_MS },
    async () => {
      const instance = await startDesktopServer({
        rootDir: fixture.root,
        api: parseUpstreamUrl(UNREACHABLE, 'api'),
        collab: parseUpstreamUrl(UNREACHABLE, 'collab'),
        runtimeConfig: () => ({}),
        port: 0,
      })
      await rawRequest(instance.origin, { path: '/health', secFetchSite: null })
      await instance.close()
      await assert.rejects(rawRequest(instance.origin, { path: '/health', secFetchSite: null }))

      // 端口已释放，可以被再次占用。
      const reused = await startDesktopServer({
        rootDir: fixture.root,
        api: parseUpstreamUrl(UNREACHABLE, 'api'),
        collab: parseUpstreamUrl(UNREACHABLE, 'collab'),
        runtimeConfig: () => ({}),
        port: instance.port,
      })
      assert.equal(reused.port, instance.port)
      await reused.close()
    },
  )

  it('拒绝非回环绑定地址', async () => {
    await assert.rejects(
      startDesktopServer({
        rootDir: fixture.root,
        api: parseUpstreamUrl(UNREACHABLE, 'api'),
        collab: parseUpstreamUrl(UNREACHABLE, 'collab'),
        runtimeConfig: () => ({}),
        host: '0.0.0.0',
      }),
      /只允许绑定回环地址/,
    )
  })
})

/** 连接一个 WebSocket，返回它到底是握手成功、被拒还是直接报错。 */
function websocketOutcome(url: string, headers: Record<string, string>): Promise<string> {
  return new Promise<string>((resolve) => {
    const socket = new WebSocket(url, { headers })
    socket.on('open', () => {
      socket.close()
      resolve('opened')
    })
    socket.on('unexpected-response', (_request, response) => {
      response.resume()
      resolve(`http-${response.statusCode}`)
    })
    socket.on('error', () => resolve('error'))
  })
}
