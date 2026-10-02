/**
 * runner-executor —— LocalRunnerExecutor 抽象（AI 执行链路）。
 *
 * 将云端 AI 的受限文件操作桥接至本地 Runner。安全边界由协议和 Runner 双重
 * 固化：仅支持 list/read/write/delete，不暴露通用命令、解释器、包管理器或
 * Agentic CLI，从而避免工作目录约束被子进程或脚本钩子绕过。
 */
import { Type } from 'typebox'
import { defineTool, type ToolDefinition } from '@earendil-works/pi-coding-agent'
import { dispatchTask } from './runner-session.js'

/** 本地任务操作类型（与 local-runner/src/executor.ts 对齐）。 */
export type LocalOp = 'list' | 'read' | 'write' | 'delete'

/** 需 read_write 授权的文件操作。 */
const WRITE_OPS: LocalOp[] = ['write', 'delete']

/** 执行一次受限本地文件操作（经 Runner 在授权目录内执行）。 */
export async function runLocalOperation(
  grantId: string,
  tool: LocalOp,
  params: {
    path?: string
    content?: string | null
  },
  timeoutMs = 130_000,
): Promise<unknown> {
  return dispatchTask(
    grantId,
    {
      tool,
      path: typeof params.path === 'string' ? params.path : '',
      content: params.content == null ? null : String(params.content),
    },
    timeoutMs,
  )
}

/** §9.5：列出已授权本地路径的逻辑文件树（相对路径）。 */
export function listLocalPathTree(grantId: string): Promise<unknown> {
  return runLocalOperation(grantId, 'list', { path: '.' })
}

/** §9.5：读取已授权本地路径中的单个文件。 */
export function readLocalPathFile(grantId: string, path: string): Promise<unknown> {
  return runLocalOperation(grantId, 'read', { path })
}

/**
 * 创建 local_fs 自定义工具。
 *
 * 该工具在本地模式会话中注入，所有输入都通过 Runner 的路径校验执行。读写授权
 * 并不授予程序执行权限；用户若需要运行命令，应在自己的受控终端中独立完成。
 */
export function createLocalFsTool(grantId: string, scope: string): ToolDefinition {
  const writable = scope === 'read_write'
  return defineTool({
    name: 'local_fs',
    label: '本地文件操作',
    description:
      '在用户授权的本地目录内执行受限文件操作。' +
      (writable
        ? '支持：list（列出目录）、read（读取文件）、write（写入文件）、delete（删除文件）。'
        : '当前为只读授权，仅支持：list（列出目录）、read（读取文件）。') +
      '路径必须相对于授权根目录；不支持命令、脚本或 Agentic CLI。',
    promptSnippet: 'local_fs: 在本地授权目录执行 list/read/write/delete 文件操作',
    promptGuidelines: [
      '本地模式下只能用 local_fs 操作被授权目录，且必须使用相对授权根目录的路径。',
      '不要使用内置 read/bash/write/edit：它们运行在云端沙箱而非用户本地机器。',
      writable
        ? '写入和删除需要读写授权；禁止尝试执行命令、脚本、包管理器或 Agentic CLI。'
        : '当前为只读授权，禁止写入和删除，仅可 list/read。',
      '执行完成后，只向用户汇报结果摘要，不要暴露内部工具调用细节。',
    ],
    parameters: Type.Object({
      operation: Type.Union(
        [
          Type.Literal('list'),
          Type.Literal('read'),
          Type.Literal('write'),
          Type.Literal('delete'),
        ],
        { description: '要执行的文件操作类型' },
      ),
      path: Type.Optional(
        Type.String({ description: '相对授权根目录的路径；list 可指向子目录，read/write/delete 为文件路径' }),
      ),
      content: Type.Optional(Type.String({ description: 'write 操作的写入内容' })),
    }),
    async execute(_toolCallId, params) {
      const op = params.operation
      const details: { result?: unknown; isError: boolean; error?: string } = {
        isError: false,
      }
      let text: string
      if (!writable && WRITE_OPS.includes(op)) {
        details.isError = true
        details.error = 'scope=read 禁止写操作'
        text = '当前授权为只读，禁止 write/delete 操作'
      } else {
        try {
          const result = await runLocalOperation(grantId, op, {
            path: params.path,
            content: params.content,
          })
          details.result = result
          text = typeof result === 'string' ? result : JSON.stringify(result, null, 2)
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err)
          details.isError = true
          details.error = message
          text = `local_fs 操作失败：${message}`
        }
      }
      return { content: [{ type: 'text', text }], details }
    },
  })
}
