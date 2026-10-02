/**
 * 本地任务执行器 —— 在用户显式授权的目录内执行受限文件操作。
 *
 * 安全边界：
 * - 仅支持 list/read/write/delete；不提供任何通用命令或 Agentic CLI 执行面。
 * - 所有路径经 path-utils 校验，拒绝符号链接逃逸、..、空字节与越界。
 * - 写入和删除必须由用户授予 read_write scope。
 *
 * 重要：云端服务、提示词或模型输出均不应被视为可执行命令。本 Runner 刻意
 * 不暴露 cli/agentic，避免解释器、包管理器、脚本钩子或子进程突破授权目录边界。
 */
import { readFileSync, writeFileSync, unlinkSync, mkdirSync, statSync } from 'fs'
import { dirname } from 'path'
import {
  resolveFileTarget,
  listTree,
  validateGrantRoot,
} from './path-utils.js'

/** 本地授权根目录（由 setup 时本地校验后确定） */
let grantRoot: string | null = null

/** 当前授权范围（read / read_write） */
let grantScope: string = 'read'

/** 单次读写最大字节数，防止 Runner 被大文件读写耗尽内存。 */
const MAX_FILE_BYTES = 5 * 1024 * 1024

/** 本 Runner 明确拒绝的高风险历史工具。 */
const UNSUPPORTED_EXECUTION_TOOLS = new Set(['cli', 'agentic'])

/**
 * 注入消息回传能力。
 *
 * 保留该导出以兼容 client 初始化流程；Runner 当前通过 executeTask 的返回值回传任务结果。
 */
export function configureExecutor(_send: (payload: unknown) => void): void {
  // 不保存回调，避免不必要的全局可变状态。
}

/** 本地校验授权路径并确定根目录；返回已就绪的 Runner 信息。 */
export function initLocal(root: string, scope: string): {
  runnerId: string
  resolvedPath: string
  toolManifest: { tools: string[]; maxFileBytes: number; executionPolicy: string }
} {
  const resolved = validateGrantRoot(root)
  grantRoot = resolved
  grantScope = scope
  return {
    runnerId: `local-${process.pid}-${Date.now().toString(36)}`,
    resolvedPath: resolved,
    toolManifest: {
      tools: ['list', 'read', scope === 'read_write' ? 'write' : null, scope === 'read_write' ? 'delete' : null].filter(
        Boolean,
      ) as string[],
      maxFileBytes: MAX_FILE_BYTES,
      executionPolicy: 'file_operations_only',
    },
  }
}

/** 强制写操作需 read_write 权限。 */
function requireWriteScope(): void {
  if (grantScope !== 'read_write') {
    throw new Error('当前授权为只读，禁止写入或删除')
  }
}

/** 在内存中读取受限大小的 UTF-8 文本文件。 */
function readUtf8WithinLimit(absPath: string): string {
  const size = statSync(absPath).size
  if (size > MAX_FILE_BYTES) {
    throw new Error(`文件超过单次读取上限（${MAX_FILE_BYTES} bytes）`)
  }
  return readFileSync(absPath, 'utf8')
}

/** 校验写入内容大小。 */
function assertWritableContent(content: string): void {
  const bytes = Buffer.byteLength(content, 'utf8')
  if (bytes > MAX_FILE_BYTES) {
    throw new Error(`写入内容超过单次写入上限（${MAX_FILE_BYTES} bytes）`)
  }
}

/**
 * 执行云端下发的任务。
 *
 * 网络客户端只构造 taskId/tool/path/content 四个字段；执行器类型同样不保留
 * command/cwd/adapter/prompt，防止已废弃的执行能力重新出现在可调用契约中。
 */
export async function executeTask(task: {
  taskId: string
  tool: string
  path: string
  content: string | null
}): Promise<{ ok: boolean; data?: unknown; error?: string }> {
  if (!grantRoot) {
    return { ok: false, error: '本地守护进程尚未就绪（未设置授权目录）' }
  }
  if (UNSUPPORTED_EXECUTION_TOOLS.has(task.tool)) {
    return {
      ok: false,
      error: '安全策略禁止通用命令和 Agentic CLI 执行；本地 Runner 仅支持 list/read/write/delete 文件操作',
    }
  }

  try {
    switch (task.tool) {
      case 'list': {
        const entries = listTree(grantRoot, task.path || '.')
        return { ok: true, data: { type: 'list', entries } }
      }
      case 'read': {
        const abs = resolveFileTarget(grantRoot, task.path, true)
        const content = readUtf8WithinLimit(abs)
        return { ok: true, data: { type: 'read', path: task.path, content } }
      }
      case 'write': {
        requireWriteScope()
        if (task.content === null || task.content === undefined) {
          return { ok: false, error: 'write 需要提供 content' }
        }
        const content = String(task.content)
        assertWritableContent(content)
        const abs = resolveFileTarget(grantRoot, task.path, false)
        mkdirSync(dirname(abs), { recursive: true })
        writeFileSync(abs, content, 'utf8')
        return { ok: true, data: { type: 'write', path: task.path, bytes: Buffer.byteLength(content, 'utf8') } }
      }
      case 'delete': {
        requireWriteScope()
        const abs = resolveFileTarget(grantRoot, task.path, true)
        unlinkSync(abs)
        return { ok: true, data: { type: 'delete', path: task.path } }
      }
      default:
        return { ok: false, error: `未知或不受支持的工具: ${task.tool}` }
    }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : '本地执行失败' }
  }
}
