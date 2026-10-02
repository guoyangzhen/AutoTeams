/**
 * 本地路径安全校验 — 与后端 path_security.py 保持同等规则。
 *
 * 规则：
 * - 拒绝空字节、绝对路径拼接、`..` 越界、符号链接逃逸。
 * - 所有读写都必须在「授权根目录」内。
 * - 拒绝访问隐藏系统目录（如 ~/.ssh 等敏感位置）。
 */
import { realpathSync, statSync, existsSync, readdirSync } from 'fs'
import { resolve, isAbsolute, normalize, sep, relative, dirname } from 'path'

/** 敏感目录名（精确匹配，大小写不敏感） */
const SENSITIVE_DIRS = new Set([
  '.ssh',
  '.aws',
  '.git',
  '.npm',
  '.config',
  '.credentials',
  'appdata',
  'windows',
  'program files',
  'program files (x86)',
  'system32',
  'python',
  'node_modules',
  '.venv',
  'venv',
])

/** 归一化并校验某路径是否位于授权根目录内（对称链接后）。
 *
 * @param mustExist 是否要求路径真实存在（读/列表为 true；写入新建文件为 false）
 */
export function resolveWithinRoot(root: string, relativePath: string, mustExist = true): string {
  if (!root) throw new Error('授权根目录为空')
  if (relativePath.includes('\0')) throw new Error('路径包含空字节')

  // 规范化相对路径（拒绝 .. 逃逸）
  const normalized = normalize(relativePath || '.')
  if (normalized.split(sep).includes('..')) {
    throw new Error('路径包含非法向上跳转（..）')
  }

  const abs = isAbsolute(relativePath)
    ? resolve(relativePath)
    : resolve(root, normalized)

  // 校验是否在根目录内
  const rel = relative(root, abs)
  if (rel.startsWith('..') || isAbsolute(rel)) {
    throw new Error('路径超出授权根目录')
  }

  // 符号链接逃逸校验：解析真实路径后再次确认在根目录内
  const realRoot = realpathSync(root)
  if (mustExist) {
    if (!existsSync(abs)) {
      throw new Error(`路径不存在: ${relativePath}`)
    }
    const realAbs = realpathSync(abs)
    const realRel = relative(realRoot, realAbs)
    if (realRel.startsWith('..') || isAbsolute(realRel)) {
      throw new Error('路径通过符号链接逃逸出授权目录')
    }
  } else {
    // 文件尚不存在（写入新建）：校验其父目录存在且在授权根内，防止经父目录符号链接逃逸
    const parent = dirname(abs)
    if (!existsSync(parent)) {
      throw new Error(`父目录不存在: ${dirname(relativePath) || '.'}`)
    }
    const realParent = realpathSync(parent)
    const realRel = relative(realRoot, realParent)
    if (realRel.startsWith('..') || isAbsolute(realRel)) {
      throw new Error('路径通过符号链接逃逸出授权目录')
    }
  }

  return abs
}

/** 校验一个绝对授权根目录是否合法（存在、是目录、不在敏感目录内） */
export function validateGrantRoot(candidate: string): string {
  if (!candidate) throw new Error('授权路径为空')
  if (candidate.includes('\0')) throw new Error('路径包含空字节')
  const abs = resolve(candidate)
  if (!existsSync(abs)) {
    throw new Error(`路径不存在: ${candidate}`)
  }
  if (!statSync(abs).isDirectory()) {
    throw new Error(`授权路径不是文件夹: ${candidate}`)
  }
  // 敏感目录检查
  const parts = abs.split(sep).map((p) => p.toLowerCase())
  for (const part of parts) {
    if (SENSITIVE_DIRS.has(part)) {
      throw new Error(`拒绝授权敏感目录: ${candidate}`)
    }
  }
  return realpathSync(abs)
}

/** 读取目录的逻辑文件树（相对路径 + 类型 + 大小），深度受限 */
export function listTree(
  root: string,
  relativePath: string,
  maxDepth = 8,
): Array<{ path: string; type: 'file' | 'dir'; size?: number }> {
  const abs = resolveWithinRoot(root, relativePath)
  const entries: Array<{ path: string; type: 'file' | 'dir'; size?: number }> = []
  const walk = (dir: string, depth: number) => {
    if (depth > maxDepth) return
    const children = safeReaddir(dir)
    for (const name of children) {
      const full = resolve(dir, name)
      const rel = relative(root, full).split(sep).join('/')
      try {
        const st = statSync(full)
        if (st.isDirectory()) {
          entries.push({ path: rel, type: 'dir' })
          walk(full, depth + 1)
        } else if (st.isFile()) {
          entries.push({ path: rel, type: 'file', size: st.size })
        }
      } catch {
        // 跳过无法读取的条目
      }
    }
  }
  walk(abs, 0)
  return entries
}

/** 安全读取目录（失败返回空数组） */
function safeReaddir(dir: string): string[] {
  try {
    return readdirSync(dir)
  } catch {
    return []
  }
}

/** 校验 read/write 目标是否为合法文件路径（读要求存在，写可不存在） */
export function resolveFileTarget(root: string, relativePath: string, mustExist: boolean): string {
  const abs = resolveWithinRoot(root, relativePath, mustExist)
  if (mustExist && !statSync(abs).isFile()) {
    throw new Error(`不是合法文件: ${relativePath}`)
  }
  return abs
}