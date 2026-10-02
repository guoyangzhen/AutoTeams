/**
 * 桌面端测试用的真实 HTTP 夹具。
 *
 * 原则：全部走真实套接字与真实请求行/首部，不 mock `http`，
 * 这样才能覆盖 absolute-form、Host、Sec-Fetch-Site、流式响应这些真实反例。
 */
import * as fs from 'node:fs/promises'
import * as http from 'node:http'
import * as os from 'node:os'
import * as path from 'node:path'
import type { AddressInfo } from 'node:net'

export interface RecordedRequest {
  method: string
  url: string
  headers: http.IncomingHttpHeaders
  body: string
}

export interface FakeUpstream {
  origin: string
  port: number
  requests: RecordedRequest[]
  close(): Promise<void>
}
export async function startFakeUpstream(
  handler: (req: http.IncomingMessage, res: http.ServerResponse, body: string) => void,
  attach?: (server: http.Server) => void,
): Promise<FakeUpstream> {
  const requests: RecordedRequest[] = []
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer) => chunks.push(chunk))
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString('utf8')
      requests.push({ method: req.method ?? 'GET', url: req.url ?? '', headers: req.headers, body })
      handler(req, res, body)
    })
  })
  attach?.(server)
  await new Promise<void>((resolve) => server.listen({ host: '127.0.0.1', port: 0, exclusive: true }, resolve))
  const { port } = server.address() as AddressInfo
  return {
    origin: `http://127.0.0.1:${port}`,
    port,
    requests,
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections()
        server.close(() => resolve())
      }),
  }
}

export interface StaticFixture {
  root: string
  cleanup(): Promise<void>
}

/** 写出一个临时静态资源目录。 */
export async function createStaticFixture(files: Record<string, string>): Promise<StaticFixture> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'autoteams-desktop-static-'))
  for (const [relative, content] of Object.entries(files)) {
    const target = path.join(root, relative)
    await fs.mkdir(path.dirname(target), { recursive: true })
    await fs.writeFile(target, content, 'utf8')
  }
  return {
    root,
    cleanup: () => fs.rm(root, { recursive: true, force: true }),
  }
}

export interface RawResponse {
  status: number
  headers: http.IncomingHttpHeaders
  body: string
  /** 每个数据块到达的相对毫秒数，用于验证流式响应没有被整体缓冲。 */
  chunkTimings: number[]
}

export interface RawRequestOptions {
  path: string
  method?: string
  headers?: Record<string, string>
  body?: string
  /** 覆盖 Sec-Fetch-Site；显式传 null 表示完全不发该首部。 */
  secFetchSite?: string | null
  origin?: string | null
  /** 每收到一块数据即回调；用于「未结束前是否已收到首块」这类断言。 */
  onChunk?: (chunk: Buffer) => void
  /** 覆盖 Host 头；显式传入用于验证 Host 校验分支。 */
  host?: string
}

/** 发出真实请求（保留请求行原样，用于 absolute-form 等反例）。 */
export function rawRequest(origin: string, options: RawRequestOptions): Promise<RawResponse> {
  const { hostname, port } = new URL(origin)
  return new Promise<RawResponse>((resolve, reject) => {
    const headers: Record<string, string> = { ...(options.headers ?? {}) }
    if (options.secFetchSite !== null) headers['sec-fetch-site'] = options.secFetchSite ?? 'same-origin'
    if (options.origin !== null) headers.origin = options.origin ?? origin
    if (options.host !== undefined) headers.host = options.host

    const started = Date.now()
    const req = http.request(
      {
        hostname,
        port: Number(port),
        method: options.method ?? 'GET',
        // 直接把 path 作为请求目标写入请求行，不让客户端库做归一化。
        path: options.path,
        headers,
        agent: false,
      },
      (res) => {
        const chunks: Buffer[] = []
        const chunkTimings: number[] = []
        res.on('data', (chunk: Buffer) => {
          chunks.push(chunk)
          chunkTimings.push(Date.now() - started)
          options.onChunk?.(chunk)
        })
        res.on('end', () => {
          resolve({
            status: res.statusCode ?? 0,
            headers: res.headers,
            body: Buffer.concat(chunks).toString('utf8'),
            chunkTimings,
          })
        })
      },
    )
    req.on('error', reject)
    req.end(options.body)
  })
}

export interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
}

/** 手动完成的闸门：让测试按「事件到达」推进时序，而不是靠固定 sleep 猜时间。 */
export function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined
  const promise = new Promise<T>((settle) => {
    resolve = settle
  })
  return { promise, resolve }
}
