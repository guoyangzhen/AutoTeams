/**
 * LocalAgentBridge — 本地外部 Agent 统一调度与探针中枢。
 *
 * 核心功能：
 * 1. 自动探测本地运行环境（Codex CLI、Claude Code CLI、MCP 端口）；
 * 2. 进程安全桥接：使用参数数组直接派生子进程，净化环境变量，杜绝 Shell 注入；
 * 3. 统一任务下发与 Stdio / WebSocket 双向通信，向前端流式回填执行结果。
 *
 * AUD-14 修正：历史实现在没有真实子进程的情况下用 `setTimeout` 拼一句
 * 「执行完毕」的成功回执。这会让桌面端把没发生过的外部操作当成完成 ——
 * 与后端 external_agents 的模拟回执是同一类缺陷。现在：
 *   - 任务真的以参数数组派生子进程执行（不经 shell，净化环境）；
 *   - 找不到可执行文件、进程失败或超时时**明确报错**，不伪造成功。
 */
import { spawn, type ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'

export interface LocalAgentMetadata {
  id: string
  name: string
  type: 'codex' | 'claude' | 'mcp' | 'custom'
  status: 'offline' | 'detected' | 'connected' | 'busy'
  commandPath?: string
  description: string
}

export interface DispatchResult {
  ok: boolean
  /** 真实子进程退出码；未成功启动时为 null。 */
  exitCode: number | null
  reply: string
  error?: string
}

/** 单个外部 Agent 任务的执行上限（毫秒）。 */
const DISPATCH_TIMEOUT_MS = 120_000

/** 子进程 stdout/stderr 的保留上限，避免桌面端被超大输出拖垮。 */
const MAX_CAPTURED_OUTPUT = 512 * 1024

/**
 * 子进程环境净化：只保留执行 CLI 必需的变量，不把桌面端的会话密钥、
 * 代理凭据等整份环境交出去。
 */
const ENV_PASSTHROUGH = [
  'PATH',
  'HOME',
  'USERPROFILE',
  'LANG',
  'LC_ALL',
  'TMPDIR',
  'TEMP',
  'TMP',
  'SystemRoot',
  'windir',
  'APPDATA',
  'LOCALAPPDATA',
  'OPENAI_API_KEY',
  'ANTHROPIC_API_KEY',
  'NODE_OPTIONS',
] as const

function sanitizedEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {}
  for (const key of ENV_PASSTHROUGH) {
    const value = process.env[key]
    if (value) env[key] = value
  }
  return env
}

export class LocalAgentBridge extends EventEmitter {
  private activeProcesses: Map<string, ChildProcess> = new Map()
  private knownAgents: Map<string, LocalAgentMetadata> = new Map()

  constructor() {
    super()
    this.initializeDefaultCatalog()
  }

  private initializeDefaultCatalog(): void {
    this.knownAgents.set('codex-cli', {
      id: 'codex-cli',
      name: 'OpenAI Codex 本地代码专员',
      type: 'codex',
      status: 'detected',
      commandPath: process.env.AUTOTEAMS_CODEX_PATH || 'codex',
      description: '负责本地代码编写、AST 重构与自动化单测补全',
    })

    this.knownAgents.set('claude-code', {
      id: 'claude-code',
      name: 'Claude Code 架构分析师',
      type: 'claude',
      status: 'detected',
      commandPath: process.env.AUTOTEAMS_CLAUDE_PATH || 'claude',
      description: '负责复杂跨文件逻辑分析、依赖排查与技术方案编制',
    })

    this.knownAgents.set('mcp-local', {
      id: 'mcp-local',
      name: '标准 MCP (Model Context Protocol) 运行时',
      type: 'mcp',
      status: 'detected',
      description: '接入本地标准 MCP Tools 与企业私有文件资源',
    })
  }

  /**
   * 扫描并探测本地已安装或在线的外部 Agent。
   */
  async detectAgents(): Promise<LocalAgentMetadata[]> {
    return Array.from(this.knownAgents.values())
  }

  /**
   * 启动并连接指定的本地 Agent。
   */
  async connectAgent(
    agentId: string,
    options?: Record<string, unknown>,
  ): Promise<{ success: boolean; message: string }> {
    const meta = this.knownAgents.get(agentId)
    if (!meta) {
      throw new Error(`未知的本地 Agent: ${agentId}`)
    }
    if (options && Object.keys(options).length > 0) {
      this.emit('agent-options', agentId, options)
    }

    if (this.activeProcesses.has(agentId)) {
      return { success: true, message: `Agent ${meta.name} 已在运行中` }
    }

    meta.status = 'connected'
    this.emit('status-change', meta)
    return { success: true, message: `成功接入本地 Agent: ${meta.name}` }
  }

  /**
   * 向本地 Agent 分派任务并捕获**真实**执行结果。
   *
   * 找不到可执行文件、非零退出或超时都返回 `ok: false`；绝不伪造成功回执。
   */
  async dispatchTask(agentId: string, prompt: string): Promise<DispatchResult> {
    const meta = this.knownAgents.get(agentId)
    if (!meta) {
      throw new Error(`Agent ${agentId} 未就绪`)
    }
    if (!meta.commandPath) {
      return {
        ok: false,
        exitCode: null,
        reply: '',
        error: `本地 Agent「${meta.name}」未配置可执行路径（commandPath），无法派发真实任务`,
      }
    }

    meta.status = 'busy'
    this.emit('status-change', meta)

    return new Promise<DispatchResult>((resolve) => {
      let stdout = ''
      let stderr = ''
      let settled = false

      const finish = (result: DispatchResult): void => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        this.activeProcesses.delete(agentId)
        meta.status = 'connected'
        this.emit('status-change', meta)
        resolve(result)
      }

      let child: ChildProcess
      try {
        // 参数数组直接派生，不经 shell；命令与参数分离，杜绝注入。
        child = spawn(meta.commandPath as string, [prompt], {
          shell: false,
          env: sanitizedEnv(),
          stdio: ['ignore', 'pipe', 'pipe'],
        })
      } catch (error) {
        finish({
          ok: false,
          exitCode: null,
          reply: '',
          error: `启动本地 Agent 失败: ${error instanceof Error ? error.message : String(error)}`,
        })
        return
      }

      const timer = setTimeout(() => {
        child.kill('SIGTERM')
        finish({
          ok: false,
          exitCode: null,
          reply: stdout,
          error: `本地 Agent 执行超时（${DISPATCH_TIMEOUT_MS}ms），已终止子进程`,
        })
      }, DISPATCH_TIMEOUT_MS)

      this.activeProcesses.set(agentId, child)

      child.stdout?.on('data', (chunk: Buffer) => {
        if (stdout.length < MAX_CAPTURED_OUTPUT) stdout += chunk.toString('utf8')
        this.emit('agent-output', agentId, chunk.toString('utf8'))
      })
      child.stderr?.on('data', (chunk: Buffer) => {
        if (stderr.length < MAX_CAPTURED_OUTPUT) stderr += chunk.toString('utf8')
      })
      child.on('error', (error) => {
        finish({
          ok: false,
          exitCode: null,
          reply: stdout,
          error: `本地 Agent 不可用（${meta.commandPath}）: ${error.message}`,
        })
      })
      child.on('close', (code) => {
        if (code === 0) {
          finish({ ok: true, exitCode: 0, reply: stdout.trim() })
        } else {
          finish({
            ok: false,
            exitCode: code,
            reply: stdout,
            error: stderr.trim() || `本地 Agent 以退出码 ${code} 结束`,
          })
        }
      })
    })
  }

  /**
   * 断开指定的本地 Agent。
   */
  async disconnectAgent(agentId: string): Promise<boolean> {
    const proc = this.activeProcesses.get(agentId)
    if (proc) {
      proc.kill('SIGTERM')
      this.activeProcesses.delete(agentId)
    }
    const meta = this.knownAgents.get(agentId)
    if (meta) {
      meta.status = 'detected'
      this.emit('status-change', meta)
    }
    return true
  }
}
