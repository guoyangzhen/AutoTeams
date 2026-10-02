/**
 * 随包静态资源解析。
 *
 * AUD-28：`loadFile` 下 Vite 产物的 `src="/assets/index-*.js"` 会按 `file:` 规则解析到
 * 盘符根目录（`E:/assets/index-*.js`），必然 404。桌面端改为经回环服务提供后，
 * 这里负责把 URL 路径安全地映射到 `frontend/dist` 内的真实文件。
 *
 * 拒绝规则（任一命中即 400/403，绝不落到文件系统）：
 * - 非 `/` 开头的路径、含 NUL 或反斜杠的路径
 * - 任何 `..` 段（含 `%2e%2e`、双重编码等）
 * - 盘符段、含 `:` 或控制字符的段（Windows 特有绕过面）
 * - realpath 落在根目录之外的符号链接
 */

import * as fs from 'node:fs/promises'
import * as path from 'node:path'

export type StaticAssetResolution =
  | { kind: 'file'; filePath: string; contentType: string }
  | { kind: 'not-found' }
  | { kind: 'rejected'; status: number; reason: string }

const CONTENT_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
  '.wasm': 'application/wasm',
  '.zip': 'application/zip',
  '.pdf': 'application/pdf',
}

const DEFAULT_CONTENT_TYPE = 'application/octet-stream'
const WINDOWS_DRIVE_PATTERN = /^[a-zA-Z]:/
const ILLEGAL_SEGMENT_PATTERN = /[<>:"|?*\\\u0000-\u001f\u007f]/

/** `candidate` 是否位于 `root` 之内。Windows 下文件系统大小写不敏感，比较时统一小写。 */
function isInsideRoot(root: string, candidate: string): boolean {
  const caseFold = process.platform === 'win32'
  const relative = path.relative(caseFold ? root.toLowerCase() : root, caseFold ? candidate.toLowerCase() : candidate)
  // relative === '' 表示 candidate 就是根目录本身（请求 `/`），仍属合法。
  return !relative.startsWith('..') && !path.isAbsolute(relative)
}

/**
 * 反复解码直到不再变化。
 * 单次解码会漏掉 `%252e%252e`（解出 `%2e%2e`）这类双重编码；这里最多解 5 轮，
 * 超过后仍含 `%` 的一律按非法路径拒绝。
 */
function decodeRepeatedly(rawPath: string): string {
  let value = rawPath
  for (let round = 0; round < 5; round += 1) {
    let decoded: string
    try {
      decoded = decodeURIComponent(value)
    } catch {
      throw new Error('非法百分号编码')
    }
    if (decoded === value) return value
    value = decoded
  }
  if (value.includes('%')) throw new Error('编码层数过深')
  return value
}

export async function resolveStaticAsset(rootDir: string, rawPath: string): Promise<StaticAssetResolution> {
  if (!rawPath.startsWith('/')) {
    return { kind: 'rejected', status: 400, reason: '路径必须以 / 开头' }
  }

  let decoded: string
  try {
    decoded = decodeRepeatedly(rawPath)
  } catch (error) {
    return { kind: 'rejected', status: 400, reason: (error as Error).message }
  }

  if (decoded.includes('\u0000')) {
    return { kind: 'rejected', status: 400, reason: '路径含 NUL' }
  }
  if (decoded.includes('\\')) {
    return { kind: 'rejected', status: 400, reason: '路径含反斜杠' }
  }

  const segments: string[] = []
  for (const segment of decoded.split('/')) {
    if (segment === '' || segment === '.') continue
    if (segment === '..') {
      return { kind: 'rejected', status: 400, reason: '路径穿越（..）' }
    }
    if (WINDOWS_DRIVE_PATTERN.test(segment) || ILLEGAL_SEGMENT_PATTERN.test(segment)) {
      return { kind: 'rejected', status: 400, reason: `路径段不合法：${segment}` }
    }
    segments.push(segment)
  }

  const realRoot = await fs.realpath(rootDir).catch(() => null)
  if (realRoot === null) {
    return { kind: 'not-found' }
  }

  const resolved = path.resolve(realRoot, ...segments)
  if (!isInsideRoot(realRoot, resolved)) {
    return { kind: 'rejected', status: 403, reason: '解析后越出资源根目录' }
  }

  // realpath 展开路径上的全部符号链接，再判一次是否仍在根目录内（符号链接逃逸）。
  let real = await fs.realpath(resolved).catch(() => null)
  if (real === null) {
    return { kind: 'not-found' }
  }
  if (!isInsideRoot(realRoot, real)) {
    return { kind: 'rejected', status: 403, reason: '符号链接指向资源根目录之外' }
  }

  if ((await fs.stat(real)).isDirectory()) {
    const indexFile = path.join(real, 'index.html')
    real = await fs.realpath(indexFile).catch(() => null)
    if (real === null) {
      return { kind: 'not-found' }
    }
    if (!isInsideRoot(realRoot, real)) {
      return { kind: 'rejected', status: 403, reason: '目录索引符号链接越界' }
    }
  }

  const contentType = CONTENT_TYPES[path.extname(real).toLowerCase()] ?? DEFAULT_CONTENT_TYPE
  return { kind: 'file', filePath: real, contentType }
}
