/**
 * 会话路径守卫（AUD-02 纵深防御）。
 *
 * SDK 内置 read/edit/write 工具会把模型给的路径解析成绝对路径后直接落盘，
 * cwd 只是默认值，不构成任何边界。本模块在真正触碰文件系统之前插入一层校验：
 * 先 realpath 解析符号链接，再用 path.relative 判断目标是否仍位于会话根目录内。
 *
 * 诚实说明：这是纵深防御，不是安全边界。
 * 校验与随后的 open()/write() 之间仍存在 TOCTOU 窗口（符号链接可被换掉），
 * 且同进程的 bash 完全可以绕过本模块。真正的隔离必须由容器/微虚拟机提供，
 * 见 SANDBOX_ISOLATION.md。
 */
import { realpathSync } from 'node:fs'
import { realpath } from 'node:fs/promises'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'

export class SandboxPathError extends Error {
  readonly code = 'SANDBOX_PATH_DENIED'

  constructor(message: string) {
    super(message)
    this.name = 'SandboxPathError'
  }
}

/** 词法包含判断：target 是否位于 root 之内（含 root 自身）。 */
export function isPathInside(root: string, target: string): boolean {
  const rel = relative(root, target)
  if (rel === '') return true
  return rel !== '..' && !rel.startsWith(`..${sep}`) && !isAbsolute(rel)
}

interface ExistingAncestor {
  /** 已存在目录（已 realpath）的真实路径 */
  realExisting: string
  /** 尚未存在的尾部片段（相对 realExisting） */
  remainder: string
}

async function realpathNearestExisting(absolutePath: string): Promise<ExistingAncestor> {
  let current = absolutePath
  const missing: string[] = []
  for (;;) {
    try {
      return { realExisting: await realpath(current), remainder: missing.reverse().join(sep) }
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code
      if (code !== 'ENOENT' && code !== 'ENOTDIR') throw error
      const parent = dirname(current)
      if (parent === current) throw error
      missing.push(basename(current))
      current = parent
    }
  }
}

function realpathNearestExistingSync(absolutePath: string): ExistingAncestor {
  let current = absolutePath
  const missing: string[] = []
  for (;;) {
    try {
      return { realExisting: realpathSync(current), remainder: missing.reverse().join(sep) }
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code
      if (code !== 'ENOENT' && code !== 'ENOTDIR') throw error
      const parent = dirname(current)
      if (parent === current) throw error
      missing.push(basename(current))
      current = parent
    }
  }
}

function assertContained(rootReal: string, resolved: string, requested: string): void {
  if (isPathInside(rootReal, resolved)) return
  throw new SandboxPathError(
    `路径越出会话目录，已拒绝：${requested}（解析为 ${resolved}，允许范围 ${rootReal}）`,
  )
}

export interface ResolveWithinRootOptions {
  /** 目标必须已存在（read/edit 用）；不存在时抛出 SandboxPathError */
  mustExist?: boolean
}

/**
 * 把模型给出的路径解析为「一定在 root 内部」的绝对路径。
 *
 * - `..` 逃逸、root 之外的绝对路径、指向外部的符号链接一律拒绝；
 * - 目标尚不存在时（write 新建文件）校验其最近的、已存在的祖先目录的 realpath。
 */
export async function resolveWithinRoot(
  root: string,
  candidate: string,
  options: ResolveWithinRootOptions = {},
): Promise<string> {
  if (typeof candidate !== 'string' || candidate.trim() === '') {
    throw new SandboxPathError('路径为空，已拒绝')
  }
  const rootReal = await realpath(root)
  const absolute = isAbsolute(candidate) ? resolve(candidate) : resolve(rootReal, candidate)
  // 先做一次纯词法判断，避免为明显越界的路径做无谓的 realpath。
  assertContained(rootReal, absolute, candidate)
  const { realExisting, remainder } = await realpathNearestExisting(absolute)
  const resolved = remainder ? join(realExisting, remainder) : realExisting
  assertContained(rootReal, resolved, candidate)
  if (options.mustExist) {
    try {
      await realpath(resolved)
    } catch {
      throw new SandboxPathError(`路径不存在，已拒绝：${candidate}`)
    }
  }
  return resolved
}

/** resolveWithinRoot 的同步版本，供同步的知识物化写入使用。 */
export function resolveWithinRootSync(root: string, candidate: string): string {
  if (typeof candidate !== 'string' || candidate.trim() === '') {
    throw new SandboxPathError('路径为空，已拒绝')
  }
  const rootReal = realpathSync(root)
  const absolute = isAbsolute(candidate) ? resolve(candidate) : resolve(rootReal, candidate)
  assertContained(rootReal, absolute, candidate)
  const { realExisting, remainder } = realpathNearestExistingSync(absolute)
  const resolved = remainder ? join(realExisting, remainder) : realExisting
  assertContained(rootReal, resolved, candidate)
  return resolved
}
