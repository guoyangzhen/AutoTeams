/**
 * 会话管理器 — 使用 pi.dev SDK 创建和管理 AI 员工协作会话
 *
 * 核心流程：
 * 1. createSession() — 调用 createAgentSession()，自动发现 AgnesAI 扩展
 * 2. prompt() — 调用 session.prompt()，通过 subscribe() 流式返回事件
 * 3. abort() — 中止当前生成
 * 4. getMessages() — 获取会话消息历史（convertToLlm 转换）
 * 5. dispose() — 清理会话
 *
 * pi.dev SDK 参考：https://pi.dev/docs/latest/sdk
 */
import { randomUUID } from 'crypto'
import { mkdirSync, readFileSync, existsSync } from 'fs'
import { homedir, tmpdir } from 'os'
import { dirname, join, basename } from 'path'
import { fileURLToPath } from 'url'
import {
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  SettingsManager,
  createAgentSession,
  convertToLlm,
  formatSkillsForPrompt,
  loadSkillsFromDir,
} from '@earendil-works/pi-coding-agent'
import type { AgentSession, AgentSessionEvent } from '@earendil-works/pi-coding-agent'
import type { CollaborationSession, SessionMessage } from './types.js'
import { StreamingTextSanitizer, sanitizeAssistantText } from './text-sanitizer.js'
import {
  KNOWLEDGE_DIR_NAME,
  materializeKnowledge,
  getSeedKnowledge,
  fetchLocalPathTree,
  type KnowledgeFile,
} from './knowledge-loader.js'
import { createLocalFsTool } from './runner-executor.js'
import {
  closePersistedSession,
  getPersistedSession,
  listPersistedSessions,
  persistSession,
} from './session-store.js'
import { SANDBOX_TOOL_NAMES, createSandboxTools, loadSandboxBashPolicy } from './sandbox-tools.js'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

// ============================================================
// 会话存储
// ============================================================

interface ManagedSession {
  session: AgentSession
  meta: CollaborationSession
  /** 是否已发送首条消息（首条消息会注入 systemPrompt） */
  firstPromptSent: boolean
  unsubscribe: (() => void) | null
  /** 中止控制器 */
  abortController: AbortController | null
  ownerUserId: string
  enterpriseId: string | null
  systemPrompt: string
  /** 沙箱工作目录（cwd 隔离） */
  cwd: string
  /** 已绑定的企业技能名（首条消息注入提示词） */
  skillNames: string[]
  /** 执行模式：sandbox / local */
  mode: 'sandbox' | 'local'
  /** 本地模式绑定的授权记录 ID */
  grantId?: string | null
  /** 本地模式授权范围 */
  localScope?: string | null
  /** 本地模式授权目录的绝对路径 */
  localPath?: string | null
}

/** 内存会话池 */
const sessions = new Map<string, ManagedSession>()

// ============================================================
// 企业级沙箱工作目录 & Skills
// ============================================================

/**
 * 为每个会话创建独立的工作目录（cwd）。
 *
 * 说明：cwd 本身**不**构成隔离——SDK 内置工具接受目录之外的路径。这里只是把
 * 会话产物分开，真正的边界由 sandbox-tools.ts 的路径守卫/环境白名单/受限 bash
 * 提供（纵深防御），OS 级隔离仍需容器，见 SANDBOX_ISOLATION.md。
 * 目录根可通过 COLLAB_WORKSPACE_DIR 覆盖；旧会话沿用 ~/.autofde/workspace，新的默认 ~/.autoteams/workspace。
 */
function createSandboxCwd(sessionId: string): string {
  // 依次尝试候选根目录，直到某个可写为止：
  //   1. 显式配置的 COLLAB_WORKSPACE_DIR
  //   2. 用户主目录下的 ~/.autoteams/workspace
  //   3. 系统临时目录下的 autoteams-workspace/{userId}（兜底，避免服务以受限用户运行时 EPERM）
  const candidates = [
    process.env.COLLAB_WORKSPACE_DIR,
    join(homedir(), '.autoteams', 'workspace'),
    join(tmpdir(), 'autoteams-workspace'),
  ].filter(Boolean) as string[]
  if (!process.env.COLLAB_WORKSPACE_DIR) {
    const legacy = join(homedir(), '.autofde', 'workspace', sessionId)
    const current = join(homedir(), '.autoteams', 'workspace', sessionId)
    if (existsSync(legacy) && existsSync(current)) {
      throw new Error(`会话工作目录在新旧位置均存在：${sessionId}`)
    }
    if (existsSync(legacy)) return legacy
  }
  for (const candidate of candidates) {
    const dir = join(candidate, sessionId)
    try {
      mkdirSync(dir, { recursive: true })
      return dir
    } catch {
      // 尝试下一个候选目录
    }
  }
  // 全部失败时抛出最后一个错误（由上层转为 500）
  throw new Error(`无法创建会话工作目录（EPERM/EACCES）: ${candidates.join(', ')}`)
}

/** 协作服务自身的 AgnesAI provider 扩展目录（供资源加载器额外发现） */
const AGNES_PROVIDER_DIR = join(__dirname, '..', '.pi', 'extensions', 'agnes-provider')

/** 企业级 Skills 目录（collaboration-service/skills/） */
const SKILLS_DIR = join(__dirname, '..', 'skills')

/** 缓存的注入提示词（企业级 Skills 的 XML 描述），首条消息时拼入系统提示词 */
let enterpriseSkillsMap: Map<string, string> | null = null

/** 加载企业级 Skills 并按名称过滤为系统提示词片段（失败时降级为空字符串） */
function getEnterpriseSkillsPrompt(skillNames: string[]): string {
  if (enterpriseSkillsMap === null) {
    enterpriseSkillsMap = new Map()
    try {
      const { skills } = loadSkillsFromDir({ dir: SKILLS_DIR, source: 'enterprise' })
      for (const skill of skills) {
        const name = (skill as { name?: string }).name || ''
        if (name) {
          enterpriseSkillsMap.set(name, formatSkillsForPrompt([skill]))
        }
      }
    } catch {
      enterpriseSkillsMap = new Map()
    }
  }
  const wanted = new Set(skillNames.filter(Boolean))
  if (wanted.size === 0) {
    // 未指定技能时默认全部启用（保持向后兼容）
    return Array.from(enterpriseSkillsMap.values()).join('\n')
  }
  return Array.from(enterpriseSkillsMap.entries())
    .filter(([name]) => wanted.has(name))
    .map(([, prompt]) => prompt)
    .join('\n')
}

/** 根据岗位标识/名称匹配企业技能，为每位 AI 员工绑定相关技能 */
function selectSkillsForAgent(agentName: string, positionLabel: string): string[] {
  const skills: string[] = []
  const haystack = `${agentName} ${positionLabel}`
  if (/订单|采购|订单处理|order/i.test(haystack)) skills.push('order-processor')
  if (/产品|技术|知识|商品|product|knowledge|采购/i.test(haystack)) skills.push('product-knowledge')
  if (/报价|报价单|销售|quotation|quote/i.test(haystack)) skills.push('quotation-generator')
  // 兜底：未命中任何技能时启用全部，保证能力完整
  if (skills.length === 0) {
    return ['product-knowledge', 'order-processor', 'quotation-generator']
  }
  return skills
}

// ============================================================
// ModelRuntime 初始化（单例）
// ============================================================

let modelRuntimePromise: Promise<ModelRuntime> | null = null

/**
 * 获取单例 ModelRuntime — 用于管理 LLM provider 和 API key
 *
 * pi.dev SDK 的 ModelRuntime 负责：
 * - 加载 provider 配置（包括 .pi/extensions/ 下的扩展）
 * - 管理 API key 认证
 * - 提供模型查找能力
 */
async function getModelRuntime(): Promise<ModelRuntime> {
  if (!modelRuntimePromise) {
    modelRuntimePromise = (async () => {
      const runtime = await ModelRuntime.create()
      // 注入 AgnesAI API Key（运行时注入，不持久化到磁盘）
      const apiKey = process.env.AGNES_API_KEY
      if (apiKey) {
        await runtime.setRuntimeApiKey('agnes', apiKey)
      }
      return runtime
    })()
  }
  return modelRuntimePromise
}

// ============================================================
// 会话管理 API
// ============================================================

/**
 * 创建新的协作会话
 *
 * @param agentId AI 员工 ID
 * @param agentName AI 员工名称
 * @param positionLabel 岗位标签
 * @param systemPrompt 系统提示词（来自 Runtime agent config）
 * @returns 会话元数据
 */
export async function createSession(
  agentId: string,
  agentName: string,
  positionLabel: string,
  systemPrompt: string,
  ownerUserId: string,
  enterpriseId: string | null,
  knowledgeFiles?: KnowledgeFile[],
  options?: {
    /** 执行模式：sandbox=云端沙箱（默认） / local=本地 Runner */
    mode?: 'sandbox' | 'local'
    /** 本地模式绑定的授权记录 ID */
    grantId?: string
    /** 本地模式授权范围 */
    localScope?: string
    /** 本地模式授权目录的绝对路径（用于 AI 正确报告当前工作目录） */
    localPath?: string
  },
): Promise<CollaborationSession> {
  const mode = options?.mode === 'local' ? 'local' : 'sandbox'
  const grantId = mode === 'local' ? options?.grantId : null
  const localScope = mode === 'local' ? options?.localScope : null
  const localPath = mode === 'local' ? options?.localPath : undefined

  const sessionId = randomUUID()
  const now = new Date().toISOString()

  const modelRuntime = await getModelRuntime()

  // 每个会话一个工作目录；沙箱模式下的文件/命令边界由 createSandboxTools() 的
  // 路径守卫 + 环境变量白名单 + 受限 bash 提供（纵深防御，非安全边界）。
  // 本地模式下该目录仍作为会话工作目录保留，但文件操作经 local_fs 路由到本地授权目录。
  const sandboxCwd = createSandboxCwd(sessionId)

  // 绑定该 AI 员工岗位相关的企业技能
  const skillNames = selectSkillsForAgent(agentName, positionLabel)

  // 知识物化：
  // - 沙箱模式：把企业知识库写入沙箱 knowledge/，供内置 read/bash 检索。
  // - 本地模式：知识即本地授权目录中的文件，AI 员工经 local_fs 直接读取，
  //   不再向云端沙箱物化（避免把企业知识写入用户本地目录）。
  const isLocal = mode === 'local'
  let knowledgeCount = 0
  if (!isLocal) {
    const knowledge = knowledgeFiles && knowledgeFiles.length > 0 ? knowledgeFiles : getSeedKnowledge()
    knowledgeCount = materializeKnowledge(sandboxCwd, knowledge)
    if (knowledgeCount > 0) {
      console.log(`[知识库] 会话 ${sessionId} 已物化 ${knowledgeCount} 个知识文档 → ${sandboxCwd}/${KNOWLEDGE_DIR_NAME}`)
    }
  }

  const agentDir = process.env.HOME || process.env.USERPROFILE || './.pi/agent'

  // 创建 cwd 绑定运行时服务：
  // - cwd 指向会话工作目录，用于发现项目本地资源；工具层另有路径守卫兜底
  // - 通过 additionalExtensionPaths 额外发现 AgnesAI provider 扩展，
  //   保证 cwd 切换后仍能加载模型 provider
  const settingsManager = SettingsManager.create(sandboxCwd, agentDir)
  const resourceLoader = new DefaultResourceLoader({
    cwd: sandboxCwd,
    agentDir,
    settingsManager,
    additionalExtensionPaths: [AGNES_PROVIDER_DIR],
  })
  await resourceLoader.reload()

  // 工具装配：
  // - 沙箱模式：注册与内置同名的 read/bash/edit/write，但实现来自 createSandboxTools()
  //   （路径守卫 + 环境白名单 + 默认拒绝的 bash 允许列表），覆盖 SDK 内置实现。
  // - 本地模式：仅启用 local_fs 自定义工具，所有文件/命令操作经 Runner 路由到本地授权目录，
  //   禁用内置文件工具，避免 AI 员工错误地在云端沙箱上操作（那并非用户本地机器）。
  const tools = isLocal ? ['local_fs'] : [...SANDBOX_TOOL_NAMES]
  const customTools = isLocal
    ? grantId ? [createLocalFsTool(grantId, localScope || 'read')] : []
    : createSandboxTools(sandboxCwd, loadSandboxBashPolicy())

  // 创建 pi.dev Agent 会话
  // sessionManager: SessionManager.inMemory() 使用内存会话，不持久化到磁盘
  const { session } = await createAgentSession({
    modelRuntime,
    tools,
    customTools,
    sessionManager: SessionManager.inMemory(),
    cwd: sandboxCwd,
    agentDir,
    resourceLoader,
  })

  // 扩展加载后，查找并切换到 AgnesAI 模型
  const modelId = process.env.AGNES_TEXT_MODEL || 'agnes-2.5-flash'
  let agnesModel = modelRuntime.getModel('agnes', modelId)
  if (!agnesModel) {
    // 尝试刷新模型列表（设置 API key 后可能需要刷新才能看到模型）
    try {
      await modelRuntime.getAvailable('agnes')
      agnesModel = modelRuntime.getModel('agnes', modelId)
    } catch {
      // 刷新失败不阻塞，使用默认模型
    }
  }
  if (agnesModel) {
    await session.setModel(agnesModel)
  }

  const meta: CollaborationSession = {
    sessionId,
    agentId,
    agentName,
    positionLabel,
    createdAt: now,
    lastActiveAt: now,
    messageCount: 0,
    lastMessagePreview: '',
    mode,
    grantId,
    localScope,
  }

    sessions.set(sessionId, {
    session,
    meta,
    firstPromptSent: false,
    unsubscribe: null,
    abortController: null,
    ownerUserId,
    enterpriseId,
    systemPrompt,
    cwd: sandboxCwd,
    skillNames,
    mode,
    grantId,
    localScope,
    localPath,
  })
  await persistSession(meta, ownerUserId, enterpriseId, sandboxCwd, [])
  return meta

}

/**
 * 发送 prompt 并通过回调流式返回事件
 *
 * 首条消息会自动注入 systemPrompt 作为系统指令前缀，
 * 因为 pi.dev SDK 的 createAgentSession 不直接支持自定义 systemPrompt 参数，
 * 系统提示词通过首条用户消息前置注入（兼容所有 LLM）。
 *
 * @param sessionId 会话 ID
 * @param message 用户消息
 * @param callbacks 流式事件回调
 */
export async function prompt(
  sessionId: string,
  message: string,
  callbacks: {
    onAgentStart: () => void
    onTextDelta: (delta: string) => void
    onMessageEnd: () => void
    onAgentEnd: () => void
    onError: (error: string) => void
    onReasoningStart: (turnIndex: number) => void
    onReasoningEnd: (turnIndex: number) => void
    onToolStart: (toolName: string, args: unknown, toolCallId?: string) => void
    onToolUpdate: (toolName: string, partialResult: unknown, toolCallId?: string) => void
    onToolEnd: (toolName: string, result: unknown, isError: boolean, toolCallId?: string) => void
    onArtifact: (path: string, name: string, content?: string) => void
  },
): Promise<void> {
  const managed = sessions.get(sessionId)
  if (!managed) {
    callbacks.onError(`会话 ${sessionId} 不存在`)
    return
  }

  const { session, meta } = managed

  // 清理旧的订阅
  if (managed.unsubscribe) {
    managed.unsubscribe()
    managed.unsubscribe = null
  }

  // 流式清洗器：跨 delta 边界剥离 <tool_call> / thinking 等系统痕迹，只透传自然语言
  const sanitizer = new StreamingTextSanitizer()

  // 订阅事件
  managed.unsubscribe = session.subscribe((event: AgentSessionEvent) => {
    switch (event.type) {
      case 'agent_start':
        callbacks.onAgentStart()
        break
      case 'message_update': {
        // assistantMessageEvent 包含 text_delta / thinking_delta / toolcall_delta 等子事件
        const assistantEvent = (event as { assistantMessageEvent?: { type: string; delta?: string } })
          .assistantMessageEvent
        // 仅透传自然语言文本增量；thinking_delta / toolcall_delta 等系统事件一律忽略
        if (assistantEvent?.type === 'text_delta' && assistantEvent.delta) {
          const clean = sanitizer.push(assistantEvent.delta)
          if (clean) {
            callbacks.onTextDelta(clean)
          }
        }
        break
      }
      case 'message_end':
        // 将缓冲中未被剥离的剩余自然语言一次性回传
        {
          const leftover = sanitizer.flush()
          if (leftover) {
            callbacks.onTextDelta(leftover)
          }
        }
        callbacks.onMessageEnd()
        break
      case 'agent_end':
        callbacks.onAgentEnd()
        // 更新元数据
        meta.messageCount = session.messages.length
        const lastMsg = session.messages[session.messages.length - 1]
        if (lastMsg) {
          const content = extractMessageContent(lastMsg)
          if (content) {
            meta.lastMessagePreview = content.slice(0, 100)
          }
        }
                meta.lastActiveAt = new Date().toISOString()
        // AgentSession 仍在内存中，但用户可见历史在每次完整响应后持久化。
        // 服务重启后不会伪造可恢复的模型上下文，而是保留 interrupted 历史供用户续建会话。
        void persistSession(
          meta,
          managed.ownerUserId,
          managed.enterpriseId,
          managed.cwd,
          getActiveMessages(sessionId, managed.ownerUserId) || [],
        )
        break

      // ---- 多步推理轨迹（turn 粒度） ----
      case 'turn_start':
        callbacks.onReasoningStart((event as unknown as { turnIndex: number }).turnIndex)
        break
      case 'turn_end':
        callbacks.onReasoningEnd((event as unknown as { turnIndex: number }).turnIndex)
        break
      // ---- 工具调用轨迹 ----
      case 'tool_execution_start':
        callbacks.onToolStart(
          (event as { toolName: string; args: unknown; toolCallId: string }).toolName,
          (event as { args: unknown }).args,
          (event as { toolCallId: string }).toolCallId,
        )
        break
      case 'tool_execution_update':
        callbacks.onToolUpdate(
          (event as { toolName: string; partialResult: unknown; toolCallId: string }).toolName,
          (event as { partialResult: unknown }).partialResult,
          (event as { toolCallId: string }).toolCallId,
        )
        break
      case 'tool_execution_end':
        callbacks.onToolEnd(
          (event as { toolName: string; result: unknown; isError: boolean; toolCallId: string }).toolName,
          (event as { result: unknown }).result,
          (event as { isError: boolean }).isError,
          (event as { toolCallId: string }).toolCallId,
        )
        // 交付成果物：write/edit 成功时，从工具输入中提取沙箱内文件路径并读取内容
        {
          const toolName = (event as { toolName: string }).toolName
          const isError = (event as { isError: boolean }).isError
          const result = (event as { result: unknown }).result as
            | { input?: Record<string, unknown>; content?: unknown }
            | undefined
          const input = result?.input
          if (!isError && (toolName === 'write' || toolName === 'edit') && input?.path) {
            const rawPath = String(input.path)
            const absPath = join(managed.cwd, rawPath)
            if (existsSync(absPath)) {
              try {
                const content = readFileSync(absPath, 'utf8')
                callbacks.onArtifact(rawPath, basename(rawPath), content)
              } catch {
                // 读取失败时仍推送路径（无内容预览）
                callbacks.onArtifact(rawPath, basename(rawPath))
              }
            } else {
              callbacks.onArtifact(rawPath, basename(rawPath))
            }
          }
        }
        break
    }
  })

  // 首条消息注入 systemPrompt + 企业级 Skills + 知识库指引
  let promptText = message
  if (!managed.firstPromptSent) {
    const sanitizeHint =
      '请始终用简洁、自然的语言直接回答用户，不要把 thinking、<tool_call> 等内部推理过程或工具调用原文展示给用户；' +
      '执行工具后只向用户汇报结果摘要。'
    const skillsSection = getEnterpriseSkillsPrompt(managed.skillNames)
    const isLocal = managed.mode === 'local'
    let knowledgeHint: string
    if (isLocal) {
      // §9.5：本地模式拉取授权目录的文件树，把文件清单注入首条提示词，
      // 让 AI 员工对授权目录结构有初步认知，避免盲目探索。
      let inventory = ''
      if (managed.grantId) {
        const tree = await fetchLocalPathTree(managed.grantId)
        const files = tree.filter((e) => e.type === 'file').slice(0, 50)
        if (files.length > 0) {
          inventory = '\n授权目录当前文件清单（前 50 个）：\n' + files.map((f) => `- ${f.path}`).join('\n')
        }
      }
      const cwdDesc = managed.localPath
        ? `你当前的工作目录（cwd）是用户授权给你的本地目录：${managed.localPath}。`
        : '你当前的工作目录（cwd）是用户授权给你的本地目录。'
      knowledgeHint =
        '你以本地模式运行，操作的是用户授权给你的本地目录（经 local_fs 工具，使用相对授权根目录的路径）。' +
        cwdDesc +
        '回答有关当前工作目录、本地文件、目录、任务的问题时，先用 local_fs 执行 list 查看目录结构，再 read 读取相关文件后作答；' +
        '资料中没有依据时如实说明，不要编造。' + inventory
    } else {
      knowledgeHint =
        `你的企业知识库位于当前工作目录下的 ${KNOWLEDGE_DIR_NAME}/ 子目录。` +
        '回答产品、报价、订单、任务等问题时，先用 ls / grep / read 检索该目录中的资料，再基于资料作答；' +
        '资料中没有依据时如实说明，不要编造。'
    }
    const focusHint =
      '工作准则：1) 只做完成用户请求所必需的工具调用，不要无目的地反复探索或执行不相关的命令；' +
      '2) 检索到足够信息后立即停止工具调用，直接给出最终答复；' +
      '3) 每次回复都必须以「对用户清晰的最终答复」结束，绝不允许只输出内部过程而无答复。'
    promptText = `[系统指令]\n${managed.systemPrompt}\n\n[企业级技能]\n${skillsSection}\n\n[知识库]\n${knowledgeHint}\n\n[协作规范]\n${sanitizeHint}\n\n${focusHint}\n\n[用户消息]\n${message}`
    managed.firstPromptSent = true
  }

  // 发送 prompt
  try {
    await session.prompt(promptText)
  } catch (err) {
    callbacks.onError(err instanceof Error ? err.message : '发送消息失败')
  }
}

/**
 * 中止当前生成
 */
export async function abort(sessionId: string): Promise<void> {
  const managed = sessions.get(sessionId)
  if (!managed) return
  try {
    await managed.session.abort()
  } catch {
    // 忽略中止错误
  }
}

/**
 * 获取会话消息历史
 *
 * 使用 convertToLlm() 将 AgentMessage[] 转换为 LLM 格式的 Message[]，
 * 然后提取 role/content 返回给前端。
 */
function getActiveMessages(sessionId: string, ownerUserId: string): SessionMessage[] | null {

  const managed = sessions.get(sessionId)
  if (!managed || managed.ownerUserId !== ownerUserId) return null

  try {
    // convertToLlm 将 AgentMessage[]（含自定义类型）转换为 LLM 兼容的 Message[]
    const llmMessages = convertToLlm(managed.session.messages)
    return llmMessages
      .filter((msg) => {
        const role = (msg as { role?: string }).role
        return role === 'user' || role === 'assistant'
      })
      .map((msg) => {
        const m = msg as { role: string; content: unknown }
        const role = m.role as 'user' | 'assistant'
        // 先统一用 extractMessageContent 提取文本（兼容 pi.dev 事件数组 / JSON 字符串化数组），
        // user 消息再剥离注入的系统指令前缀，避免历史记录显示原始 JSON 事件流
        const content =
          role === 'user'
            ? stripInjectedSystemPrefix(extractMessageContent(m))
            : extractMessageContent(m)
        return {
          role,
          content,
          timestamp: new Date().toISOString(),
        }
      })
  } catch {
    // convertToLlm 可能因消息类型不完整而失败，降级为直接提取
    return managed.session.messages
      .filter((msg) => {
        const role = (msg as { role?: string }).role
        return role === 'user' || role === 'assistant'
      })
      .map((msg) => {
        const role = (msg as { role: string }).role as 'user' | 'assistant'
        const content = extractMessageContent(msg)
        return {
          role,
          content: role === 'user' ? stripInjectedSystemPrefix(content) : content,
          timestamp: new Date().toISOString(),
        }
      })
  }
}

/** 获取会话消息；热会话优先，服务重启后回退到持久化历史。 */
export async function getMessages(sessionId: string, ownerUserId: string): Promise<SessionMessage[] | null> {
  const active = getActiveMessages(sessionId, ownerUserId)
  if (active) return active
  const persisted = await getPersistedSession(sessionId, ownerUserId)
  return persisted ? persisted.messages : null
}

/** 列出会话；合并内存热会话和重启后标记为 interrupted 的历史会话。 */
export async function listSessions(ownerUserId: string): Promise<CollaborationSession[]> {
  const active = Array.from(sessions.values())
    .filter((session) => session.ownerUserId === ownerUserId)
    .map((session) => session.meta)
  const persisted = await listPersistedSessions(ownerUserId)
  const activeIds = new Set(active.map((session) => session.sessionId))
  return [...active, ...persisted.filter((session) => !activeIds.has(session.sessionId))]
}

/** 获取单个会话元数据；可返回 interrupted 历史供用户查看。 */
export async function getSession(sessionId: string, ownerUserId: string): Promise<CollaborationSession | null> {
  const managed = sessions.get(sessionId)
  if (managed?.ownerUserId === ownerUserId) return managed.meta
  const persisted = await getPersistedSession(sessionId, ownerUserId)
  return persisted?.meta || null
}

/** 销毁会话，同时关闭持久化记录。 */
export async function disposeSession(sessionId: string, ownerUserId: string): Promise<boolean> {
  const managed = sessions.get(sessionId)
  if (managed && managed.ownerUserId === ownerUserId) {
    if (managed.unsubscribe) managed.unsubscribe()
    try {
      managed.session.dispose()
    } catch {
      // 忽略清理错误
    }
    sessions.delete(sessionId)
  }
  return closePersistedSession(sessionId, ownerUserId)
}

export function ownsSession(sessionId: string, ownerUserId: string): boolean {
  return sessions.get(sessionId)?.ownerUserId === ownerUserId
}

/** 获取会话沙箱工作目录（仅所有者可访问）；不存在返回 null */
export function getSessionCwd(sessionId: string, ownerUserId: string): string | null {
  const managed = sessions.get(sessionId)
  if (!managed || managed.ownerUserId !== ownerUserId) return null
  return managed.cwd
}

// ============================================================
// 辅助函数
// ============================================================

/** 从单个内容块中提取可展示文本（text 类型块取 text 字段，其余系统块忽略） */
function extractBlockText(block: unknown): string {
  if (typeof block === 'string') return block
  if (!block || typeof block !== 'object') return ''
  const b = block as Record<string, unknown>
  if (typeof b.text === 'string') return b.text
  // 兼容 { type: 'text', text: ... } / { text: ... }
  return ''
}

/**
 * 从 AgentMessage 中提取文本内容（兼容多种消息类型），并剥离系统痕迹。
 *
 * 兼容 pi.dev 的事件数组：content 可能为对象数组（[{type:"text",text:...},...]）
 * 或 JSON 字符串化的事件数组（"[{...},{...}]"），均可提取 text 字段。
 */
function extractMessageContent(msg: unknown): string {
  if (!msg || typeof msg !== 'object') return ''
  const m = msg as Record<string, unknown>

  let text = ''
  if (typeof m.content === 'string') {
    // 尝试解析 JSON 字符串化的事件数组（pi.dev 常见形态）
    const trimmed = m.content.trim()
    if (trimmed.startsWith('[')) {
      try {
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) {
          text = parsed.map(extractBlockText).join('')
        } else {
          text = m.content
        }
      } catch {
        text = m.content
      }
    } else {
      text = m.content
    }
  } else if (Array.isArray(m.content)) {
    text = m.content.map(extractBlockText).join('')
  } else if (typeof m.text === 'string') {
    text = m.text
  }

  return sanitizeAssistantText(text)
}

/**
 * 剥离首条用户消息中被注入的系统指令前缀（`[系统指令]…[用户消息]\n`）。
 *
 * 首条消息为注入 systemPrompt/Skills/知识库指引而起，pi.dev SDK 会把它作为
 * 一条用户消息保存，导致对话历史里用户的首条消息暴露出整段系统指令与 JSON 痕迹。
 * 这里仅保留 `[用户消息]` 标记之后的内容，避免系统痕迹回显给用户。
 */
function stripInjectedSystemPrefix(content: string): string {
  if (!content) return content
  const marker = '[用户消息]'
  const idx = content.lastIndexOf(marker)
  if (idx === -1) return content
  return content.slice(idx + marker.length).replace(/^\n+/, '')
}
