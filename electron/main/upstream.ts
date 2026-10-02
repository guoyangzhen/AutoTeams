/**
 * 桌面端「固定上游」配置解析。
 *
 * AUD-28：桌面渲染层改为由主进程内的回环服务提供后，后端地址不能再是构建期内联的
 * 字符串，而必须由主进程在启动时解析成**单一、固定、可校验**的上游。
 *
 * 约束（任一不满足即拒绝启动，不静默降级到别的后端）：
 * - 只允许 http/https；file:、ftp:、自定义 scheme 一律拒绝
 * - 不允许内嵌凭据（user:pass@），避免凭据随请求头/日志外泄
 * - 不允许 query / fragment：上游只接受「origin + 可选路径前缀」
 * - 路径前缀不得含 `..`、反斜杠、控制字符、盘符或百分号编码
 */

export interface UpstreamTarget {
  /** 归一化后的 origin，例如 `http://127.0.0.1:8000`。 */
  readonly origin: string
  /** 路径前缀：`''` 或以 `/` 开头、以 `/` 结尾的片段，例如 `/gateway`。 */
  readonly prefix: string
  readonly host: string
  readonly port: number
  readonly secure: boolean
  /** 直接用于 http(s).request 的 hostname。 */
  readonly hostname: string
}

const SCHEME_PATTERN = /^[a-zA-Z][a-zA-Z0-9+.-]*:/
const CONTROL_CHAR_PATTERN = /[\u0000-\u001f\u007f]/
const WINDOWS_DRIVE_PATTERN = /^[a-zA-Z]:/

/** 归一化路径前缀：`/a/b/` → `/a/b`；`/` 或空 → `''`。 */
function normalizePrefix(rawPath: string): string {
  let value = rawPath
  while (value.endsWith('/')) value = value.slice(0, -1)
  if (value === '') return ''
  if (!value.startsWith('/')) value = `/${value}`
  return value
}

export function parseUpstreamUrl(raw: string, label: string): UpstreamTarget {
  const value = typeof raw === 'string' ? raw.trim() : ''
  if (!value) {
    throw new Error(`${label} 未配置`)
  }
  if (CONTROL_CHAR_PATTERN.test(value)) {
    throw new Error(`${label} 含控制字符：${JSON.stringify(raw)}`)
  }
  if (!SCHEME_PATTERN.test(value)) {
    throw new Error(`${label} 必须是绝对 URL（含 http:// 或 https://）：${value}`)
  }

  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error(`${label} 不是合法 URL：${value}`)
  }

  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`${label} 只支持 http/https，收到：${url.protocol}`)
  }
  if (url.username || url.password) {
    throw new Error(`${label} 不允许内嵌凭据（user:pass@host）`)
  }
  if (url.search || url.hash) {
    throw new Error(`${label} 不允许包含 query 或 fragment：${value}`)
  }
  if (!url.hostname) {
    throw new Error(`${label} 缺少主机名：${value}`)
  }

  const prefix = normalizePrefix(url.pathname)
  if (prefix) {
    // WHATWG URL 已经把 `..` 归一化掉了（`http://h/a/../../etc` → `/etc`），
    // 所以这里重点挡**编码后仍能存活**的段：百分号、反斜杠、控制字符、盘符。
    const segments = prefix.split('/').filter((segment) => segment !== '')
    const unsafe = segments.some(
      (segment) =>
        segment === '..' ||
        segment === '.' ||
        segment.includes('\\') ||
        segment.includes('%') ||
        WINDOWS_DRIVE_PATTERN.test(segment),
    )
    if (unsafe || CONTROL_CHAR_PATTERN.test(prefix)) {
      throw new Error(`${label} 的路径前缀不合法：${prefix}`)
    }
  }

  // 默认端口归一化：origin 里不出现 :80 / :443，避免 Host 白名单出现两种写法。
  const port = url.port === '' ? (url.protocol === 'https:' ? 443 : 80) : Number(url.port)

  return {
    origin: `${url.protocol}//${url.hostname}:${port}`,
    prefix,
    host: url.hostname,
    port,
    secure: url.protocol === 'https:',
    hostname: url.hostname,
  }
}

/**
 * 拼出上游请求路径。
 * 只折叠**路径部分**的重复斜杠：query 里出现 `target=http://x` 这类值时，
 * 连 query 一起折叠会把 `http://` 变成 `http:/`，属于真实数据损坏。
 */
export function joinUpstreamPath(prefix: string, requestPath: string): string {
  const suffix = requestPath.startsWith('/') ? requestPath : `/${requestPath}`
  const queryAt = suffix.indexOf('?')
  const rawPath = queryAt === -1 ? suffix : suffix.slice(0, queryAt)
  const query = queryAt === -1 ? '' : suffix.slice(queryAt)
  return `${prefix}${rawPath.replace(/\/{2,}/g, '/')}${query}`
}

/** 生成指向该上游的 `http(s).request` 连接参数。 */
export function upstreamRequestOptions(
  target: UpstreamTarget,
  method: string,
  requestPath: string,
  headers: Record<string, string | string[] | undefined>,
): {
  hostname: string
  port: number
  method: string
  path: string
  headers: Record<string, string | string[]>
  agent: false
} {
  const forwarded: Record<string, string | string[]> = {}
  for (const [key, value] of Object.entries(headers)) {
    if (value === undefined) continue
    forwarded[key.toLowerCase()] = value
  }
  return {
    hostname: target.hostname,
    port: target.port,
    method,
    path: joinUpstreamPath(target.prefix, requestPath),
    headers: forwarded,
    // 每次请求独立建连：桌面端不需要连接复用，也避免上游长连接把套接字挂住。
    agent: false,
  }
}
