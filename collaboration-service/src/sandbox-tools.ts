/**
 * 沙箱工具装配（AUD-02 纵深防御）。
 *
 * SDK 内置 read/bash/edit/write 直接使用宿主文件系统与 shell，cwd 只是默认值。
 * 这里用 SDK 提供的 `create*ToolDefinition(..., { operations })` 钩子接入四层限制：
 *   1. 路径守卫：realpath + 相对路径包含判断，模型无法读/写会话目录之外的文件；
 *   2. 环境变量白名单：子进程拿不到服务的任何凭据；
 *   3. bash 默认拒绝：只有显式允许列表中的单一命令可以执行；
 *   4. 资源上限：强制超时、输出字节上限、进程树清理。
 *
 * 诚实说明：以上都是纵深防御，**不是**安全边界。会话内的 bash 仍可访问服务进程
 * 可见的整个文件系统与网络。真正的隔离需要把模型可执行工具放进无凭据的
 * 受限容器/微虚拟机，见 SANDBOX_ISOLATION.md。
 */
import { spawn, type ChildProcess } from 'node:child_process'
import { constants } from 'node:fs'
import { access, mkdir, readFile, writeFile } from 'node:fs/promises'
import { basename } from 'node:path'
import {
  createBashToolDefinition,
  createEditToolDefinition,
  createReadToolDefinition,
  createWriteToolDefinition,
} from '@earendil-works/pi-coding-agent'
import type { ToolDefinition } from '@earendil-works/pi-coding-agent'
import { resolveWithinRoot } from './path-guard.js'
import { buildSandboxEnv } from './sandbox-env.js'

export class SandboxCommandDeniedError extends Error {
  readonly code = 'SANDBOX_COMMAND_DENIED'

  constructor(message: string) {
    super(message)
    this.name = 'SandboxCommandDeniedError'
  }
}

/** 沙箱内允许出现的工具名（覆盖同名内置工具）。 */
export const SANDBOX_TOOL_NAMES = ['read', 'bash', 'edit', 'write'] as const

const DEFAULT_BASH_TIMEOUT_MS = 60_000
const MAX_BASH_TIMEOUT_MS = 10 * 60_000
const DEFAULT_BASH_MAX_OUTPUT_BYTES = 256 * 1024

/** 命令中出现这些字符即拒绝：它们意味着命令链、替换或重定向，而非单一命令。 */
const FORBIDDEN_COMMAND_CHARS = /[\n\r\t;&|<>`$(){}\[\]!*?\\"'#]/

/**
 * 解释器与包装器：即使被显式加入允许列表也会拒绝，因为它们能把单一命令
 * 重新变成任意 shell（`bash -c`、`env`、`xargs`、`sudo` 等）。
 */
const DENIED_COMMANDS: Record<string, true> = {
  sh: true, bash: true, zsh: true, fish: true, dash: true, ksh: true, csh: true, tcsh: true, ash: true,
  busybox: true, cmd: true, 'cmd.exe': true, command: true, powershell: true, 'powershell.exe': true,
  pwsh: true, 'pwsh.exe': true, env: true, sudo: true, doas: true, su: true, runas: true, xargs: true,
  eval: true, exec: true, source: true, nohup: true, setsid: true, timeout: true, watch: true,
  script: true, nice: true, ionice: true, stdbuf: true, unbuffer: true, ssh: true, scp: true, sftp: true,
  telnet: true, nc: true, ncat: true, netcat: true,
}

export interface SandboxBashPolicy {
  /** 允许执行的命令 basename（已按平台大小写规则归一） */
  allowedCommands: Set<string>
  /** COLLAB_SANDBOX_ALLOW_UNRESTRICTED_BASH=true：跳过命令校验（仅限临时排障） */
  unrestricted: boolean
  timeoutMs: number
  maxOutputBytes: number
}

function parsePositiveInt(raw: string | undefined, fallback: number, max: number): number {
  const parsed = Number.parseInt(raw ?? '', 10)
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback
  return Math.min(parsed, max)
}

let warnedPolicy = false

/** 从环境变量读取 bash 策略；默认拒绝（允许列表为空）。 */
export function loadSandboxBashPolicy(env: NodeJS.ProcessEnv = process.env): SandboxBashPolicy {
  const caseInsensitive = process.platform === 'win32'
  const allowed = new Set(
    (env.COLLAB_SANDBOX_BASH_ALLOWLIST || '')
      .split(',')
      .map((entry) => basename(entry.trim().replace(/^["']|["']$/g, '')))
      .filter(Boolean)
      .map((name) => (caseInsensitive ? name.toLowerCase() : name)),
  )
  const unrestricted = /^(true|1|yes|on)$/i.test(env.COLLAB_SANDBOX_ALLOW_UNRESTRICTED_BASH || '')
  if (env.NODE_ENV?.trim().toLowerCase() === 'production' && (unrestricted || allowed.size > 0)) {
    throw new SandboxCommandDeniedError(
      'Production host command execution is disabled: remove sandbox command opt-ins until an isolated executor is available.',
    )
  }
  const policy: SandboxBashPolicy = {
    allowedCommands: allowed,
    unrestricted,
    timeoutMs: parsePositiveInt(env.COLLAB_SANDBOX_BASH_TIMEOUT_MS, DEFAULT_BASH_TIMEOUT_MS, MAX_BASH_TIMEOUT_MS),
    maxOutputBytes: parsePositiveInt(
      env.COLLAB_SANDBOX_BASH_MAX_OUTPUT_BYTES,
      DEFAULT_BASH_MAX_OUTPUT_BYTES,
      16 * 1024 * 1024,
    ),
  }
  if (!warnedPolicy) {
    warnedPolicy = true
    if (policy.unrestricted) {
      console.warn(
        '[沙箱] 警告：COLLAB_SANDBOX_ALLOW_UNRESTRICTED_BASH=true，模型可以在服务进程权限下执行任意命令。' +
          '仅限本地排障，生产环境必须关闭并改用容器隔离。',
      )
    } else if (policy.allowedCommands.size === 0) {
      console.warn(
        '[沙箱] bash 默认拒绝：未配置 COLLAB_SANDBOX_BASH_ALLOWLIST，模型可用的 bash 命令为空。' +
          '如需放行请显式配置命令列表（逗号分隔）。',
      )
    }
  }
  return policy
}

/**
 * 默认拒绝的命令校验：
 * - 必须是无 shell 元字符的**单一**命令（不接受 `a && b`、`$(...)`、重定向、引号）；
 * - 命令 basename 必须在允许列表内；
 * - 解释器/提权包装器一律拒绝。
 */
export function assertCommandAllowed(command: string, policy: SandboxBashPolicy): string[] {
  // Also enforce at execution time: a caller may supply a policy without using the loader.
  if (process.env.NODE_ENV?.trim().toLowerCase() === 'production') {
    throw new SandboxCommandDeniedError('Production host command execution requires an isolated executor and is disabled.')
  }
  const trimmed = (command || '').trim()
  if (!trimmed) throw new SandboxCommandDeniedError('bash 命令为空，已拒绝')
  if (policy.unrestricted) return trimmed.split(/\s+/)

  if (FORBIDDEN_COMMAND_CHARS.test(trimmed)) {
    throw new SandboxCommandDeniedError(
      `命令包含 shell 元字符（管道/链式/替换/重定向/引号），沙箱只允许单一简单命令，已拒绝: ${trimmed}`,
    )
  }
  const parts = trimmed.split(/\s+/)
  const executable = basename(parts[0])
  const key = process.platform === 'win32' ? executable.toLowerCase() : executable
  if (DENIED_COMMANDS[key] === true) {
    throw new SandboxCommandDeniedError(`命令 ${executable} 是解释器/包装器，沙箱拒绝执行: ${trimmed}`)
  }
  if (!policy.allowedCommands.has(key)) {
    throw new SandboxCommandDeniedError(
      `命令 ${executable} 不在 COLLAB_SANDBOX_BASH_ALLOWLIST 中，已拒绝: ${trimmed}`,
    )
  }
  return parts
}

/** 终止子进程及其后代（POSIX 用进程组，Windows 用 taskkill /T）。 */
async function killProcessTree(child: ChildProcess): Promise<void> {
  if (child.pid === undefined) return
  if (process.platform === 'win32') {
    try {
      // spawn 成功返回并不代表 taskkill 成功执行；ENOENT 会异步发出 error。
      // 等待 close/error，并给终止命令自身设置上限，避免等待原命令自然退出。
      const killer = spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
        env: buildSandboxEnv(),
      })
      await new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
          killer.kill('SIGKILL')
          resolve()
        }, 3000)
        const done = () => {
          clearTimeout(timer)
          resolve()
        }
        killer.once('error', done)
        killer.once('close', done)
      })
    } catch {
      // taskkill 不可用时，至少终止直接子进程。
    }
    if (child.exitCode === null && child.signalCode === null) {
      try { child.kill('SIGKILL') } catch { /* 进程已退出 */ }
    }
    return
  }
  try {
    process.kill(-child.pid, 'SIGKILL')
  } catch {
    try {
      child.kill('SIGKILL')
    } catch {
      // 进程已退出
    }
  }
}

export interface SandboxBashExecOptions {
  command: string
  cwd: string
  onData: (data: Buffer) => void
  signal?: AbortSignal
  /** 模型请求的超时（毫秒）；与服务端上限取较小值 */
  timeout?: number
  env?: NodeJS.ProcessEnv
}

/**
 * 受限 bash 执行器：先校验命令，再用白名单环境 spawn（不经 shell），
 * 强制超时与输出上限，超限/超时时终止整个进程树。
 */
export async function execSandboxBash(
  root: string,
  policy: SandboxBashPolicy,
  options: SandboxBashExecOptions,
): Promise<{ exitCode: number | null }> {
  const parts = assertCommandAllowed(options.command, policy)
  if (options.signal?.aborted) throw new Error('aborted')

  // cwd 由 SDK 传入（= 会话根目录），仍然做一次包含判断，避免被后续改动带偏。
  const cwd = await resolveWithinRoot(root, options.cwd, { mustExist: true })

  const requested = options.timeout && options.timeout > 0 ? options.timeout : policy.timeoutMs
  const timeoutMs = Math.max(1000, Math.min(requested, policy.timeoutMs))

  const child = spawn(parts[0], parts.slice(1), {
    cwd,
    // shell:false —— 命令不经 shell 解析，元字符/引号注入面进一步收窄；
    // 代价是 .cmd/.bat 包装脚本不被支持。
    shell: false,
    env: buildSandboxEnv(),
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: process.platform !== 'win32',
    windowsHide: true,
  })

  let bytes = 0
  let outputTruncated = false
  let termination: Promise<void> | undefined
  const terminate = () => { termination ??= killProcessTree(child) }
  const onChunk = (chunk: Buffer) => {
    if (bytes >= policy.maxOutputBytes) {
      outputTruncated = true
      terminate()
      return
    }
    const remaining = policy.maxOutputBytes - bytes
    if (chunk.length > remaining) {
      options.onData(chunk.subarray(0, remaining))
      bytes = policy.maxOutputBytes
      outputTruncated = true
      terminate()
      return
    }
    bytes += chunk.length
    options.onData(chunk)
  }
  child.stdout?.on('data', onChunk)
  child.stderr?.on('data', onChunk)

  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    terminate()
  }, timeoutMs)
  const onAbort = () => terminate()
  options.signal?.addEventListener('abort', onAbort, { once: true })

  try {
    const exitCode = await new Promise<number | null>((resolve, reject) => {
      child.once('error', reject)
      child.once('close', (code) => resolve(code))
    })
    if (termination) await termination
    if (options.signal?.aborted) throw new Error('aborted')
    if (outputTruncated) {
      options.onData(
        Buffer.from(
          `\n[沙箱] 输出超过 ${policy.maxOutputBytes} 字节上限，已终止命令。\n`,
          'utf8',
        ),
      )
      return { exitCode: null }
    }
    if (timedOut) {
      options.onData(Buffer.from(`\n[沙箱] 命令超时（${timeoutMs}ms），已终止。\n`, 'utf8'))
      return { exitCode: 124 }
    }
    return { exitCode }
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', onAbort)
    // 兜底：无论走哪条路径都不留下子进程。
    if (child.exitCode === null && child.signalCode === null) {
      terminate()
      await termination
    }
  }
}

/**
 * 为会话根目录装配受控的 read/edit/write/bash 工具定义。
 * 返回的工具与 SDK 内置工具同名，会覆盖内置实现（见 AgentSession 工具注册顺序）。
 */
export function createSandboxTools(
  root: string,
  policy: SandboxBashPolicy = loadSandboxBashPolicy(),
): ToolDefinition[] {
  const read = createReadToolDefinition(root, {
    operations: {
      access: async (absolutePath) => {
        await access(await resolveWithinRoot(root, absolutePath, { mustExist: true }), constants.R_OK)
      },
      readFile: async (absolutePath) => readFile(await resolveWithinRoot(root, absolutePath, { mustExist: true })),
    },
  })
  const write = createWriteToolDefinition(root, {
    operations: {
      mkdir: async (dir) => {
        await mkdir(await resolveWithinRoot(root, dir), { recursive: true })
      },
      writeFile: async (absolutePath, content) => {
        await writeFile(await resolveWithinRoot(root, absolutePath), content, 'utf8')
      },
    },
  })
  const edit = createEditToolDefinition(root, {
    operations: {
      access: async (absolutePath) => {
        await access(await resolveWithinRoot(root, absolutePath, { mustExist: true }), constants.R_OK)
      },
      readFile: async (absolutePath) => readFile(await resolveWithinRoot(root, absolutePath, { mustExist: true })),
      writeFile: async (absolutePath, content) => {
        await writeFile(await resolveWithinRoot(root, absolutePath), content, 'utf8')
      },
    },
  })
  const bash = createBashToolDefinition(root, {
    operations: {
      exec: (command, cwd, execOptions) => execSandboxBash(root, policy, { command, cwd, ...execOptions }),
    },
    exposeSessionEnvironment: false,
  })
  return [read, write, edit, bash] as ToolDefinition[]
}

