/**
 * 桌面端导航 / 外部链接策略（纯函数，可单测）。
 *
 * AUD-28：旧实现用 `url.startsWith(DEV_SERVER_URL)` 判定「内部页面」，
 * `http://localhost:3000.evil.example` 会被误判为内部页面而允许在应用窗口内加载；
 * `setWindowOpenHandler` 又把**任意 scheme** 交给 `shell.openExternal`。
 *
 * 这里改为：
 * - 内部页面：URL 可解析且 **origin 精确相等**（先归一化 origin，含默认端口）
 * - 外部页面：仅 http/https
 * - 其余（file:、javascript:、data:、smb:、ms-msdt: 等）：一律拒绝
 */

export type NavigationDecision = 'internal' | 'external' | 'blocked'

/** 归一化 origin：`http://Localhost:80/x` → `http://localhost:80`。 */
function normalizeOrigin(url: URL): string {
  const port = url.port === '' ? (url.protocol === 'https:' ? '443' : url.protocol === 'http:' ? '80' : '') : url.port
  return port === '' ? `${url.protocol}//${url.hostname}` : `${url.protocol}//${url.hostname}:${port}`
}

/** 允许的内部 origin（已归一化）。非法 origin 被丢弃：宁可少放行，也不要因配置写错放开全部导航。 */
export function normalizeAllowedOrigins(origins: Iterable<string>): string[] {
  const normalized: string[] = []
  for (const origin of origins) {
    try {
      normalized.push(normalizeOrigin(new URL(origin)))
    } catch {
      continue
    }
  }
  return normalized
}

export function classifyNavigation(rawUrl: string, allowedOrigins: readonly string[]): NavigationDecision {
  let url: URL
  try {
    url = new URL(rawUrl)
  } catch {
    return 'blocked'
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return 'blocked'
  }
  // 带 userinfo 的 URL（http://a@evil.example/）在地址栏里极具欺骗性，直接拒绝。
  if (url.username !== '' || url.password !== '') {
    return 'blocked'
  }
  return allowedOrigins.includes(normalizeOrigin(url)) ? 'internal' : 'external'
}

/**
 * 交给系统浏览器的最终闸门。
 * `shell.openExternal` 会按 scheme 唤起系统处理器（file:、smb:、ms-msdt: 等都能触发
 * 本地程序或资源管理器），因此在主进程里再独立校验一次，而不是只依赖调用点的判断。
 */
export function isSafeExternalUrl(rawUrl: string): boolean {
  let url: URL
  try {
    url = new URL(rawUrl)
  } catch {
    return false
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return false
  }
  // 与 classifyNavigation 一致：带 userinfo 的地址不进系统浏览器，避免地址栏钓鱼。
  return url.username === '' && url.password === ''
}
