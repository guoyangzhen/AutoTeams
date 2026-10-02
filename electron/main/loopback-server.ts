/**
 * 桌面端渲染层回环服务：随包静态资源 + 固定上游代理 + WebSocket 透传。
 *
 * 为什么不再用 `loadFile`（AUD-28）：
 * - `file:` 下 Vite 产物的绝对路径 `src="/assets/index-*.js"` 解析到盘符根，必然 404
 * - `file:` 没有同源语义，`/config.json`、`/api/v1` 这类同源相对请求无法工作
 * - `file:` 下 BrowserRouter 刷新没有服务端回退，会直接落到「文件不存在」
 *
 * 安全边界（本文件是唯一入口，全部在这里收口）：
 * - 只监听 127.0.0.1，不接受局域网连接
 * - Host 必须精确等于本服务（防 DNS rebinding 打到本机服务）
 * - 浏览器来源头受限：Sec-Fetch-Site 只接受 same-origin / none
 * - 状态变更方法必须带与本服务同源的 Origin
 * - 代理目标在启动时固定：客户端无法通过请求行、Host、查询参数改变上游
 *   （absolute-form 请求行直接 400），因此不会成为开放代理
 * - `Connection` 头点名的逐跳首部一并剔除（RFC 7230），不能靠它夹带私有头
 * - 上游跨源 3xx 重定向一律拒绝，避免桌面端被当成跳板
 * - 静态路径穿越、编码绕过、符号链接逃逸在 static-assets 中拒绝
 *
 * 不做的事：不在代理层改写 Set-Cookie（含 Secure/SameSite/Domain）、不关闭
 * webSecurity、不放宽后端鉴权。Cookie 属性由后端配置决定，这里只做原样透传。
 */

import * as fs from 'node:fs'
import * as http from 'node:http'
import * as https from 'node:https'
import * as path from 'node:path'
import type { Duplex } from 'node:stream'
import { resolveStaticAsset } from './static-assets.js'
import { upstreamRequestOptions, type UpstreamTarget } from './upstream.js'

export const DEFAULT_LOOPBACK_PORT = 34115

/** 固定逐跳首部：转发时必须丢弃。 */
const HOP_BY_HOP_HEADERS: Record<string, true> = {
  connection: true,
  'keep-alive': true,
  'proxy-authenticate': true,
  'proxy-authorization': true,
  te: true,
  trailer: true,
  'transfer-encoding': true,
  upgrade: true,
}

/**
 * 收集逐跳首部集合。
 * RFC 7230：`Connection: X-Internal` 里的 `X-Internal` 也是逐跳的，
 * 代理必须连同 `Connection` 一起剔除，否则上游会收到本不该跨 hop 的头。
 */
function hopByHopHeaders(headers: http.IncomingHttpHeaders): Record<string, true> {
  const result: Record<string, true> = { ...HOP_BY_HOP_HEADERS }
  const connection = headers.connection
  const raw = Array.isArray(connection) ? connection.join(',') : connection
  if (raw !== undefined) {
    for (const token of raw.split(',')) {
      const name = token.trim().toLowerCase()
      if (name !== '') result[name] = true
    }
  }
  return result
}

/**
 * 不转发给上游的首部。
 * - `host`：必须替换成上游 host，否则上游按 Host 做路由/虚拟主机时会错
 * - `origin` / `sec-fetch-*`：浏览器视角下这是**同源**请求，透传出去只会让后端 CORS
 *   白名单误判；同源性已由本服务在入口处统一校验，这里清除。
 */
const REQUEST_HEADERS_NOT_FORWARDED: Record<string, true> = {
  host: true,
  origin: true,
  'sec-fetch-site': true,
  'sec-fetch-mode': true,
  'sec-fetch-dest': true,
  'sec-fetch-user': true,
}

const API_PREFIX = '/api'
const COLLAB_API_PREFIX = '/collab-api'
const COLLAB_WS_PATH = '/collab-ws'
const CONFIG_PATH = '/config.json'
const HEALTH_PATH = '/health'

export interface DesktopServerOptions {
  /** 随包静态资源根目录（打包后为 `resources/frontend`）。 */
  rootDir: string
  api: UpstreamTarget
  collab: UpstreamTarget
  /** 生成渲染层可见的 config.json 内容；origin 由本服务给出。 */
  runtimeConfig: (origin: string) => Record<string, unknown>
  host?: string
  port?: number
}

export interface DesktopServer {
  readonly origin: string
  readonly host: string
  readonly port: number
  close(): Promise<void>
}

function sendJson(res: http.ServerResponse, status: number, body: Record<string, unknown>): void {
  const payload = JSON.stringify(body)
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
    'cache-control': 'no-store',
  })
  res.end(payload)
}

function collectHeaders(headers: http.IncomingHttpHeaders): Record<string, string | string[]> {
  const result: Record<string, string | string[]> = {}
  for (const [key, value] of Object.entries(headers)) {
    if (value === undefined) continue
    result[key.toLowerCase()] = value
  }
  return result
}

/** 是否是本服务允许的浏览器来源。缺头（非浏览器客户端）一律拒绝。 */
function hasAcceptableFetchSite(req: http.IncomingMessage): boolean {
  const site = req.headers['sec-fetch-site']
  return site === 'same-origin' || site === 'none'
}

function isOriginHeader(value: string | string[] | undefined, origin: string): boolean {
  const raw = Array.isArray(value) ? value[0] : value
  return raw === origin
}

/**
 * 校验请求来源。返回 null 表示通过，否则返回拒绝原因。
 * `/health` 放开来源头限制，是为了让装机后的命令行 smoke 能确认服务在监听；
 * 它只返回静态状态、不代理、不读配置，因此不构成开放代理面。
 */
function checkRequestSource(
  req: http.IncomingMessage,
  authority: string,
  origin: string,
  pathname: string,
): string | null {
  if (typeof req.headers.host !== 'string' || req.headers.host.toLowerCase() !== authority) {
    return 'Host 不匹配，拒绝（防 DNS rebinding）'
  }
  if (pathname === HEALTH_PATH) {
    return null
  }
  if (!hasAcceptableFetchSite(req)) {
    return 'Sec-Fetch-Site 不是 same-origin/none，拒绝跨站访问'
  }
  const method = (req.method ?? 'GET').toUpperCase()
  if (method !== 'GET' && method !== 'HEAD' && !isOriginHeader(req.headers.origin, origin)) {
    return '状态变更请求缺少同源 Origin，拒绝'
  }
  return null
}

/**
 * 上游重定向检查：只允许同源（含相对地址）。
 * 跨源 Location 会把桌面端变成任意跳转跳板，也会把窗口导航到外部站点。
 */
function isSameOriginLocation(location: string, target: UpstreamTarget): boolean {
  try {
    return new URL(location, `${target.origin}${target.prefix}`).origin === target.origin
  } catch {
    return false
  }
}

function proxyRequest(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  target: UpstreamTarget,
  requestPath: string,
): void {
  const hopByHop = hopByHopHeaders(req.headers)
  const headers = collectHeaders(req.headers)
  for (const key of Object.keys(headers)) {
    if (hopByHop[key] === true || REQUEST_HEADERS_NOT_FORWARDED[key] === true) {
      delete headers[key]
    }
  }
  headers.host = `${target.hostname}:${target.port}`

  const transport = target.secure ? https : http
  const upstream = transport.request(upstreamRequestOptions(target, req.method ?? 'GET', requestPath, headers), (upstreamRes) => {
    const location = upstreamRes.headers.location
    if (location !== undefined && !isSameOriginLocation(location, target)) {
      upstreamRes.destroy()
      sendJson(res, 502, { detail: '上游返回跨源重定向，已拒绝' })
      return
    }

    const upstreamHopByHop = hopByHopHeaders(upstreamRes.headers)
    const outHeaders: Record<string, string | string[]> = {}
    for (const [key, value] of Object.entries(upstreamRes.headers)) {
      if (value === undefined) continue
      if (upstreamHopByHop[key.toLowerCase()] === true) continue
      // Set-Cookie 原样透传：Secure / SameSite / Domain 一律不改写。
      outHeaders[key.toLowerCase()] = value
    }
    res.writeHead(upstreamRes.statusCode ?? 502, outHeaders)
    upstreamRes.pipe(res)
  })

  upstream.on('error', () => {
    if (res.headersSent) {
      res.destroy()
      return
    }
    sendJson(res, 502, { detail: `上游不可达：${target.origin}` })
  })
  // 客户端断开时立刻销毁上游请求，避免连接与带宽被挂住。
  res.on('close', () => upstream.destroy())
  req.pipe(upstream)
}

async function serveStatic(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  rootDir: string,
  pathname: string,
  method: string,
): Promise<void> {
  const acceptsHtml = (req.headers.accept ?? '').includes('text/html')
  let resolution = await resolveStaticAsset(rootDir, pathname)
  if (resolution.kind === 'not-found' && acceptsHtml && (method === 'GET' || method === 'HEAD')) {
    // BrowserRouter 刷新/深链回退：未命中静态文件时回 index.html。
    // 仅限 GET/HEAD + Accept: text/html，绝不吞掉 XHR/fetch 的 404。
    resolution = await resolveStaticAsset(rootDir, '/index.html')
  }

  if (resolution.kind === 'rejected') {
    sendJson(res, resolution.status, { detail: resolution.reason })
    return
  }
  if (resolution.kind === 'not-found') {
    sendJson(res, 404, { detail: '资源不存在' })
    return
  }

  const stats = await fs.promises.stat(resolution.filePath)
  const isEntryDocument = path.basename(resolution.filePath) === 'index.html'
  res.writeHead(200, {
    'content-type': resolution.contentType,
    'content-length': stats.size,
    // 入口文档不缓存，避免升级后旧壳长期持有过期资源引用；带指纹的产物长缓存。
    'cache-control': isEntryDocument ? 'no-store' : 'public, max-age=3600',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
  })
  if (method === 'HEAD') {
    res.end()
    return
  }
  fs.createReadStream(resolution.filePath)
    .on('error', () => res.destroy())
    .pipe(res)
}

/**
 * 建立 WebSocket 隧道，并保证两侧 socket 一定被回收。
 *
 * 升级后的 socket 不再受 `server.closeAllConnections()` 管理（Node 在 upgrade 后
 * 就把它移出连接跟踪），所以必须自己登记，才能在应用退出时被 destroy。
 */
function proxyWebSocket(
  req: http.IncomingMessage,
  clientSocket: Duplex,
  head: Buffer,
  target: UpstreamTarget,
  requestPath: string,
  liveTunnels: Set<Duplex>,
): void {
  const hopByHop = hopByHopHeaders(req.headers)
  const headers = collectHeaders(req.headers)
  for (const key of Object.keys(headers)) {
    if (hopByHop[key] === true || REQUEST_HEADERS_NOT_FORWARDED[key] === true) {
      delete headers[key]
    }
  }
  headers.host = `${target.hostname}:${target.port}`
  headers.connection = 'Upgrade'
  headers.upgrade = 'websocket'

  const track = (socket: Duplex): void => {
    liveTunnels.add(socket)
    socket.on('close', () => liveTunnels.delete(socket))
  }
  track(clientSocket)

  const transport = target.secure ? https : http
  const upstream = transport.request(upstreamRequestOptions(target, 'GET', requestPath, headers))

  upstream.on('upgrade', (upstreamRes, upstreamSocket, upstreamHead) => {
    // 101 是握手响应：除 transfer-encoding 外全部回给客户端。
    // `Connection: Upgrade`、`Upgrade: websocket`、`Sec-WebSocket-Accept` 都不能丢，
    // 否则客户端判定握手失败（ws 报 "Unexpected server response: 101"）。
    const responseLines = ['HTTP/1.1 101 Switching Protocols\r\n']
    for (const [key, value] of Object.entries(upstreamRes.headers)) {
      if (value === undefined) continue
      if (key.toLowerCase() === 'transfer-encoding') continue
      responseLines.push(`${key}: ${Array.isArray(value) ? value.join(', ') : value}\r\n`)
    }
    responseLines.push('\r\n')
    clientSocket.write(responseLines.join(''))
    upstreamSocket.pipe(clientSocket)
    clientSocket.pipe(upstreamSocket)
    track(upstreamSocket)
    // 任一侧结束/出错都要拆掉另一侧，否则连接会一直挂着。
    clientSocket.on('close', () => upstreamSocket.destroy())
    upstreamSocket.on('close', () => clientSocket.destroy())
    clientSocket.on('error', () => upstreamSocket.destroy())
    upstreamSocket.on('error', () => clientSocket.destroy())
    if (upstreamHead.length > 0) clientSocket.write(upstreamHead)
  })
  upstream.on('response', () => {
    clientSocket.end('HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
  })
  upstream.on('error', () => clientSocket.destroy())
  clientSocket.on('error', () => upstream.destroy())
  upstream.end(head.length > 0 ? head : undefined)
}

/** 只接受 origin-form 的请求目标；代理用的 absolute-form 一律拒绝。 */
function parseRequestTarget(req: http.IncomingMessage): URL | null {
  const raw = req.url ?? '/'
  if (!raw.startsWith('/') || raw.startsWith('//')) return null
  try {
    return new URL(raw, 'http://127.0.0.1')
  } catch {
    return null
  }
}

export async function startDesktopServer(options: DesktopServerOptions): Promise<DesktopServer> {
  const host = options.host ?? '127.0.0.1'
  if (host !== '127.0.0.1') {
    throw new Error(`桌面渲染服务只允许绑定回环地址，收到：${host}`)
  }

  const server = http.createServer()
  /** 升级后的 WebSocket 隧道：closeAllConnections() 管不到，必须自己登记。 */
  const liveTunnels = new Set<Duplex>()
  let origin = ''

  /** 期望的 Host 头：绑定地址 + 实际端口（origin 去掉 scheme 即为 authority）。 */
  const authority = (): string => (origin === '' ? '127.0.0.1' : origin.slice('http://'.length))

  server.on('request', (req, res) => {
    void (async () => {
      const target = parseRequestTarget(req)
      if (target === null) {
        sendJson(res, 400, { detail: '仅接受 origin-form 请求目标' })
        return
      }
      const rejection = checkRequestSource(req, authority(), origin, target.pathname)
      if (rejection !== null) {
        sendJson(res, 403, { detail: rejection })
        return
      }

      const method = (req.method ?? 'GET').toUpperCase()

      if (target.pathname === HEALTH_PATH) {
        sendJson(res, 200, { status: 'ok' })
        return
      }
      if (target.pathname === CONFIG_PATH) {
        sendJson(res, 200, options.runtimeConfig(origin))
        return
      }
      if (target.pathname === API_PREFIX || target.pathname.startsWith(`${API_PREFIX}/`)) {
        proxyRequest(req, res, options.api, target.pathname + target.search)
        return
      }
      if (target.pathname.startsWith(`${COLLAB_API_PREFIX}/`)) {
        // 上游路径前缀由 upstreamRequestOptions 统一拼接，这里只做 /collab-api → /api 改写。
        proxyRequest(req, res, options.collab, `/api${target.pathname.slice(COLLAB_API_PREFIX.length)}${target.search}`)
        return
      }
      if (method !== 'GET' && method !== 'HEAD') {
        sendJson(res, 404, { detail: '未知端点' })
        return
      }

      await serveStatic(req, res, options.rootDir, target.pathname, method)
    })().catch(() => {
      if (!res.headersSent) sendJson(res, 500, { detail: '桌面渲染服务内部错误' })
      else res.destroy()
    })
  })

  server.on('upgrade', (req, socket, head) => {
    const target = parseRequestTarget(req)
    // 与 HTTP 入口同标准：路径必须是固定的 /collab-ws、Host 必须精确匹配、
    // Origin 必须同源。浏览器发起的 WebSocket 一定带 Origin，因此缺 Origin 也拒绝。
    if (target === null || target.pathname !== COLLAB_WS_PATH) {
      socket.end('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n')
      return
    }
    if (typeof req.headers.host !== 'string' || req.headers.host.toLowerCase() !== authority()) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n')
      return
    }
    if (req.headers.origin !== origin) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n')
      return
    }
    // 上游路径前缀同样由 upstreamRequestOptions 拼接。
    proxyWebSocket(req, socket, head, options.collab, `/ws${target.search}`, liveTunnels)
  })

  // 畸形请求行/头直接断开，不让解析错误冒泡成崩溃。
  server.on('clientError', (_error, socket) => {
    if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
    else socket.destroy()
  })

  const port = await new Promise<number>((resolve, reject) => {
    const listenOn = (candidate: number) => {
      const onError = (error: NodeJS.ErrnoException) => {
        server.removeListener('error', onError)
        // 固定端口被占用时退回随机端口；绝不「假设」占用者就是本服务。
        if (error.code === 'EADDRINUSE' && candidate !== 0) {
          listenOn(0)
          return
        }
        reject(error)
      }
      server.once('error', onError)
      server.listen({ host, port: candidate, exclusive: true }, () => {
        server.removeListener('error', onError)
        const address = server.address()
        if (address === null || typeof address === 'string') reject(new Error('监听回环端口失败'))
        else resolve(address.port)
      })
    }
    listenOn(options.port ?? DEFAULT_LOOPBACK_PORT)
  })

  origin = `http://${host}:${port}`

  return {
    origin,
    host,
    port,
    close: () =>
      new Promise<void>((resolve) => {
        // 先断开存量连接再 close，否则 keep-alive 连接会让 close 一直等下去；
        // 升级后的 WebSocket 已不在 Node 的连接跟踪里，这里显式 destroy。
        server.closeAllConnections()
        for (const tunnel of liveTunnels) tunnel.destroy()
        liveTunnels.clear()
        server.close(() => resolve())
      }),
  }
}
