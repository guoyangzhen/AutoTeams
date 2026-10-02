/**
 * CollaborationWorkspace — AI 员工协作工作台
 *
 * 设计以 stitch_output/autoteams_ui_prototype.html 的「智能体对话」页为基准：
 * 1. 头部：面包屑 + serif 大标题 + 运行中徽章
 * 2. 左栏（260px）：新建协作 + 搜索 + 会话列表（按 今天/昨天/更早 分组）
 * 3. 中栏（flex-1）：消息流（AI 浅灰气泡 / 用户深蓝右对齐）+ 输入框
 * 4. 右栏（280px）：Agent 信息卡（方形头像 + 状态 + 3 列统计 + Self-RAG 流程图 + 工具使用）
 *
 * 数据流：
 * - 用户选择 Agent → getAgent(id) 获取 system_prompt → createSession() 创建协作会话
 * - 用户输入消息 → WebSocket sendPrompt() → 流式接收 text_delta
 * - 切换会话 → getMessages() 恢复消息历史
 *
 * 后端：collaboration-service (Node.js pi.dev SDK + AgnesAI)
 *
 * Hooks 顺序说明：
 * refreshSessions 必须在 useCollaborationWs 之前定义，
 * 因为 onMessageComplete 回调中引用了 refreshSessions。
 * 使用 useLatestRef 模式确保回调始终调用最新版本。
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Send, Square, Plus, Trash2, Bot, MessagesSquare,
  RefreshCw, CheckCircle2, Search, Copy, ThumbsUp,
  ThumbsDown, AlertCircle, X, Paperclip, Database, Sparkles,
  Wrench, Loader2, ListOrdered, ChevronDown, ChevronRight,
  Terminal as TerminalIcon, FileText, FileCode2, PanelRight,
} from 'lucide-react'
import { MarkdownRenderer } from '@/components/MarkdownRenderer'
import { useCollaborationWs, type ConnectionStatus, type ToolCallEvent, type ReasoningStep, type Artifact } from '@/hooks/useCollaborationWs'
import {
  createSession, listSessions, deleteSession, getMessages, uploadAttachment,
  type CollabSession,
} from '@/api/collabWorkspace'
import { positionToLabel, formatRelativeTime } from '@/utils/fieldMappings'
import LocalConnectionPanel from '@/components/LocalConnectionPanel'
import { listLocalPaths, type LocalPathGrant } from '@/api/localPaths'
import type { AgentRunMetrics } from '@/types'

// ============================================================
// 类型 & 常量
// ============================================================

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  streaming?: boolean
  /** 消息反馈状态 */
  feedback?: 'liked' | 'disliked' | null
  /** 是否已复制 */
  copied?: boolean
}

const SUGGESTED_PROMPTS = [
  '介绍你自己',
  '查询产品参数',
  '生成报价单',
  '今天有哪些任务？',
]

const MAX_INPUT_LENGTH = 4000

/** 需要整体剥离内容的系统标签名（小写），与协作服务 text-sanitizer.ts 保持一致 */
const SYSTEM_BLOCK_TAGS = [
  'thinking', 'tool_call', 'toolcall', 'parameter', 'result',
  'query', 'search', 'reasoning', 'thought', 'scratchpad',
] as const

/**
 * 剥离回复中的系统痕迹（thinking / <tool_call> / JSON 工具命令等），仅保留自然语言。
 * 服务端已做清洗，这里作为前端双保险，避免任何系统痕迹出现在对话气泡中。
 */
function sanitizeChatText(text: string): string {
  if (!text) return ''
  let out = text
  // 成对出现的系统块（含可选参数，如 <tool_call name="x">）
  for (const tag of SYSTEM_BLOCK_TAGS) {
    const pairRe = new RegExp(`<${tag}(?:\\s[^>]*)?>[\\s\\S]*?<\\/${tag}\\s*>`, 'gi')
    out = out.replace(pairRe, '')
    const anyRe = new RegExp(`</?${tag}(?:\\s[^>]*)?>`, 'gi')
    out = out.replace(anyRe, '')
  }
  // 兼容 [thinking]...[/thinking] 方括号标记
  out = out.replace(
    /\[\s*(thinking|tool_call|toolcall|parameter|result|query|search|reasoning)\s*\][\s\S]*?\[\s*\/\s*\1\s*\]/gi,
    '',
  )
  // 清理多余空行与首尾空白
  out = out.replace(/[ \t]+\r?\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
  return out
}

/** 生命周期阶段 → 中文状态标签 */
const STAGE_LABEL: Record<string, string> = {
  recruit: '招募中',
  training: '培训中',
  production: '在岗',
  evaluation: '评估中',
  continuous_learning: '持续学习',
  promotion: '晋升中',
  retired: '已退休',
}

// ============================================================
// 辅助 Hook：useLatestRef
// ============================================================

/** 保持 ref 始终指向最新值，避免闭包捕获旧值 */
function useLatestRef<T>(value: T): React.MutableRefObject<T> {
  const ref = useRef(value)
  ref.current = value
  return ref
}

// ============================================================
// 会话分组工具
// ============================================================

/** 会话按最后活跃时间分组：今天 / 昨天 / 更早 */
function groupSessions(sessions: CollabSession[]): {
  today: CollabSession[]
  yesterday: CollabSession[]
  earlier: CollabSession[]
} {
  const groups = { today: [] as CollabSession[], yesterday: [] as CollabSession[], earlier: [] as CollabSession[] }
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfYesterday = startOfToday - 86400000
  for (const s of sessions) {
    const t = new Date(s.lastActiveAt).getTime()
    if (t >= startOfToday) groups.today.push(s)
    else if (t >= startOfYesterday) groups.yesterday.push(s)
    else groups.earlier.push(s)
  }
  // 组内按最近活跃时间降序，最新会话置顶
  const byLatest = (a: CollabSession, b: CollabSession) =>
    new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime()
  groups.today.sort(byLatest)
  groups.yesterday.sort(byLatest)
  groups.earlier.sort(byLatest)
  return groups
}

/** 会话列表时间：今天 → HH:MM；昨天 → "昨天 HH:MM"；更早 → MM/DD */
function formatSessionTime(dateStr: string): string {
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfYesterday = startOfToday - 86400000
  const t = d.getTime()
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (t >= startOfToday) return hhmm
  if (t >= startOfYesterday) return `昨天 ${hhmm}`
  return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`
}

/** 消息时间戳：HH:MM */
function formatMessageTime(dateStr: string): string {
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// ============================================================
// 主组件
// ============================================================

interface CollaborationWorkspaceProps {
  /** 可协作的 AI 员工列表（除"已退休"外均可） */
  agents: AgentRunMetrics[]
}

export function CollaborationWorkspace({ agents }: CollaborationWorkspaceProps) {
  // ---- 状态 ----
    const [sessions, setSessions] = useState<CollabSession[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [sessionsError, setSessionsError] = useState<string | null>(null)
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null)
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [creatingSession, setCreatingSession] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showAgentSelector, setShowAgentSelector] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  // 移动端右栏抽屉：小屏（<lg）下右栏默认隐藏，通过头部按钮以抽屉形式打开（保证本地连接等功能入口可见）
  const [showDetailDrawer, setShowDetailDrawer] = useState(false)
  // ---- 会话执行模式（§2 双模式：sandbox=云端沙箱 / local=本地 Runner）----
  const [sessionMode, setSessionMode] = useState<'sandbox' | 'local'>('sandbox')
  const [sessionGrantId, setSessionGrantId] = useState<string | null>(null)
  const [connectedGrants, setConnectedGrants] = useState<LocalPathGrant[]>([])
  const [grantsLoading, setGrantsLoading] = useState(false)

  // ---- 消息区滚动与请求生命周期 ref ----
  const messagesViewportRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const followMessagesRef = useRef(true)
  const scrollFrameRef = useRef<number | null>(null)
  const sessionListRequestRef = useRef(0)
  const sessionLoadRequestRef = useRef(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // ---- 附件上传 ----
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadingAttachment, setUploadingAttachment] = useState(false)
  const [uploadedFile, setUploadedFile] = useState<string | null>(null)

  // ============================================================
  // 会话管理（必须在 useCollaborationWs 之前定义）
  // ============================================================

  /** 刷新会话列表：仅接受最近一次请求，避免慢响应覆盖用户刚完成的创建或删除。 */
  const refreshSessions = useCallback(async () => {
    const requestId = ++sessionListRequestRef.current
    setSessionsLoading(true)
    setSessionsError(null)
    try {
      const list = await listSessions()
      if (requestId === sessionListRequestRef.current) {
        setSessions(list)
      }
    } catch (err) {
      if (requestId === sessionListRequestRef.current) {
        setSessionsError(err instanceof Error ? err.message : '无法加载协作记录，请检查协作服务后重试')
      }
    } finally {
      if (requestId === sessionListRequestRef.current) {
        setSessionsLoading(false)
      }
    }
  }, [])

  /** 删除会话：提供明确进行中与失败反馈，避免误以为操作已生效。 */
  const handleDeleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (deletingSessionId) return
    setDeletingSessionId(sessionId)
    setSessionsError(null)
    try {
      await deleteSession(sessionId)
      setSessions((prev) => prev.filter((s) => s.sessionId !== sessionId))
      if (activeSessionId === sessionId) {
        sessionLoadRequestRef.current += 1
        setActiveSessionId(null)
        setMessages([])
      }
    } catch (err) {
      setSessionsError(err instanceof Error ? err.message : '删除协作失败，请稍后重试')
    } finally {
      setDeletingSessionId(null)
    }
  }, [activeSessionId, deletingSessionId])

  // 使用 ref 保持最新引用，供 WebSocket 回调使用
  const refreshSessionsRef = useLatestRef(refreshSessions)

  // ---- WebSocket Hook ----
  // 回调中使用 ref 调用最新的 refreshSessions，避免闭包捕获问题和 TDZ
  const ws = useCollaborationWs({
    onMessageComplete: useCallback((_sessionId: string, fullText: string) => {
      // 流式完成：将临时 streaming 消息标记为完成，并补充完整文本
      setMessages((prev) =>
        prev.map((m, i) => {
          if (m.streaming && i === prev.length - 1) {
            return { ...m, content: fullText, streaming: false }
          }
          return m
        }),
      )
      // 刷新会话列表（更新 lastMessagePreview）— 通过 ref 调用最新版本
      refreshSessionsRef.current()
    }, [refreshSessionsRef]),
    onError: useCallback((err: string) => {
      setError(err)
      // 将 streaming 消息标记为错误
      setMessages((prev) =>
        prev.map((m, i) =>
          m.streaming && i === prev.length - 1
            ? { ...m, content: m.content || '生成失败，请重试', streaming: false }
            : m,
        ),
      )
    }, []),
  })

  // ---- 活跃会话 ----
  const activeSession = useMemo(
    () => sessions.find((s) => s.sessionId === activeSessionId) || null,
    [sessions, activeSessionId],
  )

  // ---- 活跃会话对应的 Agent ----
  const activeAgent = useMemo(
    () => agents.find((a) => a.agent_id === activeSession?.agentId) || null,
    [agents, activeSession],
  )

  // ---- 搜索过滤后的会话列表 ----
  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions
    const q = searchQuery.toLowerCase()
    return sessions.filter((s) =>
      s.agentName.toLowerCase().includes(q) ||
      (s.lastMessagePreview || '').toLowerCase().includes(q) ||
      (s.positionLabel || '').toLowerCase().includes(q),
    )
  }, [sessions, searchQuery])

  // ---- 分组后的会话 ----
  const sessionGroups = useMemo(() => groupSessions(filteredSessions), [filteredSessions])

  // ============================================================
  // 会话创建 & 切换
  // ============================================================

  /** 创建新协作会话 */
  const handleCreateSession = useCallback(async (agent: AgentRunMetrics) => {
    setCreatingSession(true)
    setError(null)
    try {
      // 1. 创建协作会话（本地模式需绑定一个已连接的授权）
      const isLocal = sessionMode === 'local'
      const session = await createSession({
        agentId: agent.agent_id,
        positionLabel: positionToLabel(agent.position, agent.agent_name),
        mode: isLocal ? 'local' : 'sandbox',
        grantId: isLocal && sessionGrantId ? sessionGrantId : undefined,
        localScope: isLocal && sessionGrantId
          ? connectedGrants.find((g) => g.id === sessionGrantId)?.scope
          : undefined,
      })

      // 3. 更新会话列表 & 切换到新会话
      setSessions((prev) => [session, ...prev])
      setActiveSessionId(session.sessionId)
      setMessages([])
      setShowAgentSelector(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建协作会话失败，请确认协作服务已启动')
    } finally {
      setCreatingSession(false)
    }
  }, [sessionMode, sessionGrantId, connectedGrants])

  /** 打开 Agent 选择器时：若选择本地模式，加载已连接授权供选择 */
  const handleToggleAgentSelector = useCallback(() => {
    setShowAgentSelector((v) => {
      const next = !v
      if (next && sessionMode === 'local') {
        setGrantsLoading(true)
        listLocalPaths()
          .then((gs) => {
            const connected = gs.filter((g) => g.status === 'connected')
            setConnectedGrants(connected)
            // 默认选中第一个已连接授权
            setSessionGrantId((prev) => prev ?? (connected[0]?.id ?? null))
          })
          .catch(() => setConnectedGrants([]))
          .finally(() => setGrantsLoading(false))
      }
      return next
    })
  }, [sessionMode])

  /** 切换执行模式时重置授权选择 */
  const handleModeChange = useCallback((mode: 'sandbox' | 'local') => {
    setSessionMode(mode)
    setSessionGrantId(null)
    if (mode === 'local') {
      setGrantsLoading(true)
      listLocalPaths()
        .then((gs) => {
          const connected = gs.filter((g) => g.status === 'connected')
          setConnectedGrants(connected)
          setSessionGrantId(connected[0]?.id ?? null)
        })
        .catch(() => setConnectedGrants([]))
        .finally(() => setGrantsLoading(false))
    }
  }, [])

  /** 切换到已有会话：使用序号忽略过期响应，避免快速切换时消息串到错误会话。 */
  const handleSelectSession = useCallback(async (sessionId: string) => {
    if (sessionId === activeSessionId) return
    const requestId = ++sessionLoadRequestRef.current
    followMessagesRef.current = true
    setActiveSessionId(sessionId)
    setLoadingMessages(true)
    setError(null)
    try {
      const msgs = await getMessages(sessionId)
      if (requestId !== sessionLoadRequestRef.current) return
      setMessages(
        msgs.map((m) => ({
          role: m.role,
          content: m.content,
          timestamp: m.timestamp,
        })),
      )
    } catch (err) {
      if (requestId !== sessionLoadRequestRef.current) return
      setMessages([])
      setError(err instanceof Error ? err.message : '无法加载此协作记录，请重试')
    } finally {
      if (requestId === sessionLoadRequestRef.current) {
        setLoadingMessages(false)
      }
    }
  }, [activeSessionId])

  // ============================================================
  // 消息发送
  // ============================================================

  /** 发送消息 */
  const handleSend = useCallback(() => {
    const trimmed = input.trim()
    if (!trimmed || !activeSessionId || ws.isStreaming) return

        // 发送新消息时恢复跟随，确保用户能立即看到自己的输入和生成结果。
    followMessagesRef.current = true
    // 添加用户消息到列表
    const userMsg: ChatMessage = {

      role: 'user',
      content: trimmed,
      timestamp: new Date().toISOString(),
    }
    // 添加占位 assistant 消息（streaming）
    const assistantMsg: ChatMessage = {
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      streaming: true,
    }
    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setInput('')

    // 通过 WebSocket 发送
    ws.sendPrompt(activeSessionId, trimmed)
  }, [input, activeSessionId, ws])

    /** 消息区滚动：用户正在阅读历史内容时不抢走视图，接近底部时才持续跟随新消息。 */
  const handleMessagesScroll = useCallback(() => {
    const viewport = messagesViewportRef.current
    if (!viewport) return
    followMessagesRef.current = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 72
  }, [])

  /** 中止生成 */
  const handleAbort = useCallback(() => {

    if (activeSessionId) {
      ws.abort(activeSessionId)
    }
  }, [activeSessionId, ws])

  /** 键盘快捷键：Enter 发送，Shift+Enter 换行 */
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }, [handleSend])

  // ============================================================
  // 附件上传
  // ============================================================

  /** 触发文件选择（回形针按钮） */
  const handleAttachClick = useCallback(() => {
    if (!activeSessionId || ws.isStreaming) return
    fileInputRef.current?.click()
  }, [activeSessionId, ws.isStreaming])

  /** 文件选择后上传到会话沙箱知识目录（本地模式跳过：AI 直接读本地授权目录） */
  const handleFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file || !activeSessionId) return
    if (activeSession?.mode === 'local') {
      setUploadedFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }
    setUploadingAttachment(true)
    setError(null)
    try {
      await uploadAttachment(activeSessionId, file)
      setUploadedFile(file.name)
    } catch (err) {
      setError(err instanceof Error ? err.message : '附件上传失败，请确认协作服务已启动')
    } finally {
      setUploadingAttachment(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }, [activeSessionId, activeSession])

  // ============================================================
  // 消息操作
  // ============================================================

  /** 复制消息内容 */
  const handleCopyMessage = useCallback((index: number) => {
    const msg = messages[index]
    if (!msg) return
    navigator.clipboard.writeText(msg.content).catch(() => {})
    setMessages((prev) =>
      prev.map((m, i) => (i === index ? { ...m, copied: true } : m)),
    )
    setTimeout(() => {
      setMessages((prev) =>
        prev.map((m, i) => (i === index ? { ...m, copied: false } : m)),
      )
    }, 2000)
  }, [messages])

  /** 消息反馈（点赞/点踩） */
  const handleFeedback = useCallback((index: number, feedback: 'liked' | 'disliked') => {
    setMessages((prev) =>
      prev.map((m, i) =>
        i === index
          ? { ...m, feedback: m.feedback === feedback ? null : feedback }
          : m,
      ),
    )
  }, [])

  // ============================================================
  // 实时更新 streaming 消息
  // ============================================================

  // 实时更新 streaming 消息。
  // 修复「Maximum update depth exceeded」：effect 依赖 messages 又在内部 setMessages
  // 会形成「渲染 → setState → 重渲染 → effect → setState」的无限循环。
  // 现改为仅依赖 ws.streamingText，且用函数式更新在「无可更新项」时返回原引用，
  // 从而打破更新链。
  useEffect(() => {
    if (!ws.streamingText) return
    setMessages((prev) => {
      if (prev.length === 0) return prev
      const lastMsg = prev[prev.length - 1]
      if (!lastMsg.streaming || lastMsg.content === ws.streamingText) return prev
      return prev.map((m, i) =>
        i === prev.length - 1 && m.streaming ? { ...m, content: ws.streamingText } : m,
      )
    })
  }, [ws.streamingText])

  // ============================================================
  // 自动滚动到底部
  // ============================================================

  // 流式文本可能在单帧内产生多次更新；用 animation frame 合并滚动，避免反复 smooth
  // 动画造成页面抖动。用户主动滚离底部后不再强制拉回，直到其发送或切换新会话。
  useEffect(() => {
    if (!followMessagesRef.current || scrollFrameRef.current !== null) return
    scrollFrameRef.current = requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: ws.isStreaming ? 'auto' : 'smooth' })
      scrollFrameRef.current = null
    })
  }, [messages, ws.isStreaming, ws.streamingText])

  useEffect(() => () => {
    if (scrollFrameRef.current !== null) {
      cancelAnimationFrame(scrollFrameRef.current)
    }
  }, [])

  // ============================================================
  // 初始化：加载会话列表
  // ============================================================

  useEffect(() => {
    refreshSessions()
  }, [refreshSessions])

  // ============================================================
  // 渲染
  // ============================================================

  return (
    <div className="flex h-[calc(100vh-150px)] min-h-[600px] border border-border-default rounded-lg overflow-hidden bg-surface shadow-soft">
      {/* ============================================================ */}
      {/* 左栏：会话列表                                                  */}
      {/* ============================================================ */}
      <aside className="w-[260px] flex-shrink-0 border-r border-border-default bg-surface flex flex-col">
        {/* 新建协作按钮 */}
        <div className="p-3 border-b border-border-subtle">
          <div className="relative">
            <button
              onClick={handleToggleAgentSelector}
              disabled={creatingSession || agents.length === 0}
              className="w-full inline-flex items-center justify-center gap-1.5 bg-brand-500 text-white rounded-md py-2 px-3 text-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Plus className="w-4 h-4" aria-hidden="true" />
              {creatingSession ? '创建中...' : '新建协作'}
            </button>

            {/* Agent 选择器下拉 */}
            <AnimatePresence>
              {showAgentSelector && (
                <motion.div
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.15 }}
                  className="absolute top-full left-0 right-0 mt-1 bg-surface border border-border-default rounded-md shadow-lift z-10 max-h-[26rem] overflow-y-auto"
                >
                  {/* 执行模式切换（§2 双模式：云端沙箱 / 本地 Runner） */}
                  <div className="px-3 pt-2.5 pb-2 border-b border-border-subtle">
                    <div className="flex gap-1.5">
                      {([
                        { value: 'sandbox', label: '云端沙箱' },
                        { value: 'local', label: '本地执行' },
                      ] as const).map((opt) => (
                        <button
                          key={opt.value}
                          onClick={() => handleModeChange(opt.value)}
                          className={`flex-1 text-xs rounded-md py-1.5 border transition-colors ${
                            sessionMode === opt.value
                              ? 'bg-brand-500 text-white border-brand-500'
                              : 'bg-elevated text-text-secondary border-border-default hover:border-brand-300'
                          }`}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                    <p className="text-[10px] text-text-muted mt-1.5">
                      {sessionMode === 'local'
                        ? 'AI 员工在本地授权目录内直接读写文件（需已连接本地 Runner）'
                        : 'AI 员工在云端沙箱内执行（无需本地连接）'}
                    </p>
                  </div>

                  {/* 本地模式：选择已连接授权 */}
                  {sessionMode === 'local' && (
                    <div className="px-3 pt-2 pb-1.5 border-b border-border-subtle">
                      <label className="block text-[10px] font-medium text-text-secondary mb-1">
                        已连接本地路径
                      </label>
                      {grantsLoading ? (
                        <div className="flex items-center gap-1 text-xs text-text-muted py-1">
                          <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" />
                          加载中…
                        </div>
                      ) : connectedGrants.length === 0 ? (
                        <p className="text-xs text-amber-600 bg-amber-50 border border-amber-100 rounded-md px-2 py-1.5">
                          暂无已连接的本地路径，请先在右侧「本地连接」中完成连接。
                        </p>
                      ) : (
                        <div className="space-y-1">
                          {connectedGrants.map((g) => (
                            <button
                              key={g.id}
                              onClick={() => setSessionGrantId(g.id)}
                              className={`w-full flex items-center justify-between gap-2 text-left text-xs rounded-md px-2 py-1.5 border transition-colors ${
                                sessionGrantId === g.id
                                  ? 'bg-brand-50 border-brand-200 text-brand-700'
                                  : 'bg-elevated border-border-default text-text-secondary hover:border-brand-300'
                              }`}
                            >
                              <span className="truncate">{g.label || g.local_path}</span>
                              <span className="flex-shrink-0 text-[10px] text-text-muted">
                                {g.scope === 'read_write' ? '读写' : '只读'}
                              </span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {agents.length === 0 ? (
                    <p className="text-sm text-text-tertiary px-3 py-4 text-center">
                      暂无可协作的 AI 员工
                    </p>
                  ) : (
                    agents.map((agent) => (
                      <button
                        key={agent.agent_id}
                        onClick={() => handleCreateSession(agent)}
                        disabled={sessionMode === 'local' && !sessionGrantId}
                        className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-elevated transition-colors text-left border-b border-border-subtle last:border-b-0 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <div className="w-7 h-7 rounded-md bg-brand-500 text-white flex items-center justify-center text-xs font-medium flex-shrink-0">
                          {agent.agent_name?.charAt(0) || '?'}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-text-primary truncate">{agent.agent_name}</p>
                          <p className="text-xs text-text-tertiary truncate">
                            {positionToLabel(agent.position, agent.agent_name)}
                          </p>
                        </div>
                      </button>
                    ))
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
                </div>

        {sessionsError && (
          <div className="mx-3 mb-2 flex items-start gap-2 rounded-lg border border-error/30 bg-error/10 px-2.5 py-2 text-xs text-error" role="alert">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 flex-1 leading-5">{sessionsError}</span>
            <button type="button" onClick={() => void refreshSessions()} className="shrink-0 font-medium underline underline-offset-2 hover:no-underline">
              重试
            </button>
          </div>
        )}

        {/* 搜索框 */}

        {sessions.length > 0 && (
          <div className="px-3 py-2 border-b border-border-subtle">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted" aria-hidden="true" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索协作…"
                className="w-full bg-elevated border border-border-default rounded-md pl-8 pr-3 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500/15"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary"
                  aria-label="清除搜索"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
          </div>
        )}

        {/* 会话列表（按 今天/昨天/更早 分组） */}
        <div className="flex-1 overflow-y-auto p-2 space-y-3">
                    {sessionsLoading && sessions.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 px-3 py-8 text-center">
              <Loader2 className="h-5 w-5 animate-spin text-brand-500" aria-hidden="true" />
              <p className="text-xs text-text-tertiary">正在读取协作记录…</p>
            </div>
          ) : sessions.length === 0 ? (
            <div className="text-center py-8 px-3">

              <MessagesSquare className="w-8 h-8 text-text-muted mx-auto mb-2" aria-hidden="true" />
              <p className="text-xs text-text-tertiary">暂无协作记录</p>
              <p className="text-xs text-text-muted mt-1">点击上方按钮开始</p>
            </div>
          ) : filteredSessions.length === 0 ? (
            <div className="text-center py-8 px-3">
              <Search className="w-6 h-6 text-text-muted mx-auto mb-2" aria-hidden="true" />
              <p className="text-xs text-text-tertiary">未找到匹配的协作</p>
            </div>
          ) : (
            <>{[
              { key: 'today', label: '今天', list: sessionGroups.today },
              { key: 'yesterday', label: '昨天', list: sessionGroups.yesterday },
              { key: 'earlier', label: '更早', list: sessionGroups.earlier },
            ].map((group) =>
              group.list.length > 0 ? (
                <div key={group.key}>
                  <div className="text-xs font-semibold text-text-muted uppercase tracking-wide px-1 mb-1.5">
                    {group.label}
                  </div>
                  <div className="space-y-1">
                    {group.list.map((session) => (
                      <div
                        key={session.sessionId}
                        onClick={() => handleSelectSession(session.sessionId)}
                        className={`group w-full flex items-start gap-2 rounded-md px-2.5 py-2 transition-colors text-left cursor-pointer border ${
                          activeSessionId === session.sessionId
                            ? 'bg-brand-50 border-brand-100 text-brand-700'
                            : 'border-transparent hover:bg-elevated text-text-secondary'
                        }`}
                      >
                        <Bot className="w-4 h-4 flex-shrink-0 mt-0.5 text-brand-500" aria-hidden="true" />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <p className="text-sm font-medium truncate">{session.agentName}</p>
                            {session.mode === 'local' && (
                              <span className="flex-shrink-0 text-[9px] px-1 py-px rounded bg-violet-50 text-violet-600 border border-violet-200" title="本地执行模式">
                                本地
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-text-tertiary truncate">
                            {session.lastMessagePreview || session.positionLabel || '新协作'}
                          </p>
                          <p className="text-[10px] text-text-muted mt-0.5">
                            {formatSessionTime(session.lastActiveAt)}
                          </p>
                        </div>
                                                <button
                          type="button"
                          onClick={(e) => handleDeleteSession(session.sessionId, e)}
                          disabled={deletingSessionId !== null}
                          className={`h-8 w-8 shrink-0 items-center justify-center rounded text-text-muted transition-all hover:text-error disabled:cursor-wait disabled:opacity-70 ${
                            deletingSessionId === session.sessionId ? 'flex' : 'hidden group-hover:flex focus:flex'
                          }`}
                          title={deletingSessionId === session.sessionId ? '正在删除协作' : '删除协作'}
                          aria-label={deletingSessionId === session.sessionId ? '正在删除协作' : '删除协作'}
                        >
                          {deletingSessionId === session.sessionId ? (
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                          ) : (
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                          )}
                        </button>

                      </div>
                    ))}
                  </div>
                </div>
              ) : null,
            )}</>
          )}
        </div>

        {/* 连接状态 */}
        <ConnectionBadge status={ws.connectionStatus} runnerOnline={ws.runnerOnline} />
      </aside>

      {/* ============================================================ */}
      {/* 中栏：消息流 + 输入框                                           */}
      {/* ============================================================ */}
      <main className="flex-1 flex flex-col min-w-0 bg-elevated/30">
        {activeSession ? (
          <>
            {/* 顶部标题栏：面包屑 + serif 标题 + 运行中徽章 */}
            <div className="h-14 border-b border-border-default bg-surface flex items-center justify-between px-4 flex-shrink-0">
              <div className="min-w-0">
                <nav className="text-[10px] text-text-muted mb-0.5">智能体协作 / 协作工作台</nav>
                <div className="flex items-center gap-2 min-w-0">
                  <h3 className="font-serif-display text-base font-semibold text-text-primary truncate">
                    {activeSession.agentName}
                  </h3>
                  {activeSession.mode === 'local' && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-violet-600 bg-violet-50 border border-violet-200 px-1.5 py-0.5 rounded-full flex-shrink-0" title="本地执行模式：AI 员工在本地授权目录内执行">
                      <TerminalIcon className="w-2.5 h-2.5" aria-hidden="true" />
                      本地执行
                    </span>
                  )}
                  <span className="inline-flex items-center gap-1 text-[10px] text-success bg-success/10 px-1.5 py-0.5 rounded-full flex-shrink-0">
                    <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
                    运行中
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                <span className="text-xs text-text-tertiary bg-elevated px-2 py-1 rounded-md flex-shrink-0 hidden sm:inline">
                  {activeSession.positionLabel}
                </span>
                                  <button
                    type="button"
                    onClick={() => void refreshSessions()}
                    disabled={sessionsLoading}
                    className="p-1.5 text-text-tertiary hover:text-brand-500 hover:bg-elevated rounded transition-colors flex-shrink-0 disabled:cursor-wait disabled:opacity-60"
                    title={sessionsLoading ? '正在刷新协作记录' : '刷新协作记录'}
                    aria-label={sessionsLoading ? '正在刷新协作记录' : '刷新协作记录'}
                  >
                    <RefreshCw className={`w-4 h-4 ${sessionsLoading ? 'animate-spin' : ''}`} aria-hidden="true" />

                </button>
                {/* 移动端：打开右栏抽屉（本地连接/执行轨迹等） */}
                <button
                  onClick={() => setShowDetailDrawer(true)}
                  className="lg:hidden p-1.5 text-text-tertiary hover:text-brand-500 hover:bg-elevated rounded transition-colors flex-shrink-0"
                  title="打开详情面板"
                  aria-label="打开详情面板（本地连接等）"
                >
                  <PanelRight className="w-4 h-4" aria-hidden="true" />
                </button>
              </div>
            </div>

                        {/* 消息流 */}
            <div ref={messagesViewportRef} onScroll={handleMessagesScroll} className="flex-1 overflow-y-auto p-4 space-y-4">

              {error && (
                <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-error/10 border border-error/30 text-sm text-error">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" />
                  <span className="flex-1">{error}</span>
                  <button
                    onClick={() => setError(null)}
                    className="text-error/70 hover:text-error text-xs"
                    aria-label="关闭错误提示"
                  >
                    ×
                  </button>
                </div>
              )}
              {loadingMessages ? (
                <div className="flex items-center justify-center py-12">
                  <RefreshCw className="w-5 h-5 text-text-muted animate-spin" aria-hidden="true" />
                </div>
              ) : messages.length === 0 ? (
                <EmptyConversation
                  agentName={activeSession.agentName}
                  positionLabel={activeSession.positionLabel}
                  onPickPrompt={setInput}
                />
              ) : (
                <>
                  {messages.map((msg, idx) => (
                    <MessageBubble
                      key={idx}
                      message={msg}
                      agentName={activeAgent?.agent_name || activeSession.agentName}
                      onCopy={() => handleCopyMessage(idx)}
                      onFeedback={(f) => handleFeedback(idx, f)}
                    />
                  ))}
                  <div ref={messagesEndRef} />
                </>
              )}
            </div>

            {/* 输入区 */}
            <div className="border-t border-border-subtle bg-surface p-3 flex-shrink-0">
              {/* 建议提示词（仅无消息时显示） */}
              {messages.length === 0 && !ws.isStreaming && (
                <div className="flex items-center gap-2 flex-wrap mb-2">
                  <span className="text-xs text-text-muted">试试：</span>
                  {SUGGESTED_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      onClick={() => setInput(prompt)}
                      className="text-xs px-2.5 py-1 rounded-full bg-elevated text-text-secondary border border-border-default hover:bg-brand-50 hover:text-brand-500 hover:border-brand-200 transition-colors"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              )}

              {/* 输入框 */}
              <div className="flex items-end gap-2 bg-elevated border border-border-default rounded-lg p-2 focus-within:border-brand-500 focus-within:ring-1 focus-within:ring-brand-500/15 transition-all">
                <button
                  onClick={handleAttachClick}
                  disabled={!activeSessionId || ws.isStreaming || uploadingAttachment || activeSession.mode === 'local'}
                  className="p-1.5 text-text-tertiary hover:text-brand-500 disabled:opacity-40 disabled:cursor-not-allowed"
                  title={activeSession.mode === 'local' ? '本地执行模式：AI 员工直接读取已授权本地目录中的文件' : '上传附件（纳入本会话知识库）'}
                  aria-label="上传附件"
                  type="button"
                >
                  {uploadingAttachment ? (
                    <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Paperclip className="w-4 h-4" aria-hidden="true" />
                  )}
                </button>
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value.slice(0, MAX_INPUT_LENGTH))}
                  onKeyDown={handleKeyDown}
                  placeholder={`向 ${activeSession.agentName} 发送消息…`}
                  rows={1}
                  className="flex-1 bg-transparent text-sm resize-none focus:outline-none py-1.5 text-text-primary placeholder:text-text-muted max-h-32"
                  style={{ minHeight: '42px' }}
                  disabled={ws.isStreaming}
                />
                <span className="text-[10px] text-text-muted font-mono self-center">
                  {input.length}/{MAX_INPUT_LENGTH}
                </span>
                {ws.isStreaming ? (
                  <button
                    onClick={handleAbort}
                    className="bg-error text-white p-2 rounded-md hover:bg-error/90 transition-colors self-center"
                    title="停止生成"
                    aria-label="停止生成"
                  >
                    <Square className="w-4 h-4" aria-hidden="true" />
                  </button>
                ) : (
                  <button
                    onClick={handleSend}
                    disabled={!input.trim()}
                    className="bg-brand-500 text-white p-2 rounded-md hover:bg-brand-600 transition-colors disabled:opacity-40 disabled:cursor-not-allowed self-center"
                    title="发送"
                    aria-label="发送消息"
                  >
                    <Send className="w-4 h-4" aria-hidden="true" />
                  </button>
                )}
              </div>
              {uploadedFile && (
                <div className="flex items-center gap-1.5 mt-2 px-1 text-xs text-success">
                  <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />
                  <span className="truncate">已上传附件：{uploadedFile}（已纳入本会话知识库）</span>
                  <button
                    onClick={() => setUploadedFile(null)}
                    className="text-text-muted hover:text-text-primary ml-auto flex-shrink-0"
                    aria-label="关闭提示"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={handleFileChange}
                aria-hidden="true"
              />
              <p className="text-center mt-1.5 text-[10px] text-text-muted">
                AI 生成内容可能不准确，请核实重要信息
              </p>
            </div>
          </>
        ) : (
          <NoActiveSession agents={agents} onCreateSession={handleCreateSession} creatingSession={creatingSession} />
        )}
      </main>

      {/* ============================================================ */}
      {/* 右栏：执行轨迹 + 交付成果物（小屏时以抽屉形式打开）            */}
      {/* ============================================================ */}
      {/* 移动端遮罩：点击关闭抽屉 */}
      {showDetailDrawer && (
        <div
          className="lg:hidden fixed inset-0 z-30 bg-black/30"
          onClick={() => setShowDetailDrawer(false)}
          aria-hidden="true"
        />
      )}
      <aside
        className={`w-[340px] flex-shrink-0 bg-surface overflow-y-auto flex-col ${
          showDetailDrawer
            ? 'fixed inset-y-0 right-0 z-40 border-l border-border-default shadow-lift flex'
            : 'hidden'
        } lg:static lg:z-auto lg:shadow-none lg:flex lg:border-l lg:border-border-default`}
      >
        {/* 移动端抽屉头部（含关闭按钮） */}
        <div className="lg:hidden sticky top-0 z-10 flex items-center justify-between px-3 py-2 border-b border-border-subtle bg-surface">
          <span className="text-xs font-medium text-text-secondary">详情面板</span>
          <button
            onClick={() => setShowDetailDrawer(false)}
            className="p-1.5 text-text-tertiary hover:text-brand-500 hover:bg-elevated rounded transition-colors"
            title="关闭"
            aria-label="关闭详情面板"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
        {activeAgent ? (
          <>
            {/* 本地连接置顶：本地工具桥接是核心能力，需常驻可见、避免被下滑忽略 */}
            <LocalConnectionPanel />
            <TrajectoryPanel toolCalls={ws.toolCalls} reasoningSteps={ws.reasoningSteps} isStreaming={ws.isStreaming} />
            <ArtifactsPanel artifacts={ws.artifacts} />
            <AgentInfoPanel agent={activeAgent} session={activeSession} />
          </>
        ) : (
          <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
            <Bot className="w-12 h-12 text-text-muted mb-3" aria-hidden="true" />
            <p className="text-sm text-text-tertiary">选择一位 AI 员工</p>
            <p className="text-xs text-text-muted mt-1">查看详细信息与能力</p>
          </div>
        )}
      </aside>
    </div>
  )
}

// ============================================================
// 子组件：消息气泡（含操作栏）
// ============================================================

interface MessageBubbleProps {
  message: ChatMessage
  agentName: string
  onCopy: () => void
  onFeedback: (feedback: 'liked' | 'disliked') => void
}

function MessageBubble({ message, agentName, onCopy, onFeedback }: MessageBubbleProps) {
  const isUser = message.role === 'user'
  const time = formatMessageTime(message.timestamp)
  // 前端双保险：剥离任何系统痕迹（thinking / tool_call / JSON 命令），仅显示干净的自然语言
  const cleanContent = sanitizeChatText(message.content)

  if (isUser) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
        className="flex gap-2.5 flex-row-reverse"
      >
        <div className="w-7 h-7 rounded-md bg-brand-300 text-white flex items-center justify-center text-xs font-medium flex-shrink-0">
          我
        </div>
        <div className="flex-1 min-w-0 flex flex-col items-end">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs text-text-muted">{time}</span>
            <span className="text-sm font-medium text-text-primary">我</span>
          </div>
          <div className="bg-brand-500 text-white rounded-md rounded-tr-none px-3 py-2.5 text-sm leading-relaxed max-w-[80%]">
            <div className="whitespace-pre-wrap break-words">{cleanContent}</div>
          </div>
        </div>
      </motion.div>
    )
  }

  // AI 消息
  const isTyping = message.streaming && !message.content
  const showActions = !message.streaming && message.content

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className="flex gap-2.5"
    >
      {/* AI 头像 */}
      <div className="w-7 h-7 rounded-md bg-brand-500 text-white flex items-center justify-center text-xs font-medium flex-shrink-0">
        {agentName?.charAt(0) || 'AI'}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-medium text-text-primary">{agentName}</span>
          <span className="text-xs text-text-muted">{time}</span>
          {message.streaming && (
            <span className="text-xs text-success">正在输入</span>
          )}
        </div>
        <div className={`bg-elevated rounded-md rounded-tl-none px-3 py-2.5 text-sm leading-relaxed text-text-primary ${message.streaming ? 'typing-cursor' : ''}`}>
          {isTyping ? (
            <div className="flex items-center gap-1 h-5">
              <span className="w-1.5 h-1.5 bg-text-muted rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 bg-text-muted rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 bg-text-muted rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          ) : (
            <MarkdownRenderer content={cleanContent} />
          )}
        </div>

        {/* 消息操作栏（仅完成的 AI 消息显示） */}
        {showActions && (
          <div className="flex items-center gap-3 mt-2 text-xs text-text-muted">
            <button
              onClick={onCopy}
              className="flex items-center gap-1 hover:text-brand-500 transition-colors"
              title={message.copied ? '已复制' : '复制'}
            >
              {message.copied ? <CheckCircle2 className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
              {message.copied ? '已复制' : '复制'}
            </button>
            <button
              onClick={() => onFeedback('liked')}
              className={`flex items-center gap-1 transition-colors ${
                message.feedback === 'liked' ? 'text-success' : 'hover:text-success'
              }`}
            >
              <ThumbsUp className="w-3.5 h-3.5" />
              有用
            </button>
            <button
              onClick={() => onFeedback('disliked')}
              className={`flex items-center gap-1 transition-colors ${
                message.feedback === 'disliked' ? 'text-error' : 'hover:text-error'
              }`}
            >
              <ThumbsDown className="w-3.5 h-3.5" />
              无用
            </button>
          </div>
        )}
      </div>
    </motion.div>
  )
}

// ============================================================
// 子组件：空协作引导
// ============================================================

function EmptyConversation({
  agentName,
  positionLabel,
  onPickPrompt,
}: {
  agentName: string
  positionLabel: string
  onPickPrompt: (prompt: string) => void
}) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <div className="w-14 h-14 rounded-xl bg-brand-50 flex items-center justify-center mb-4">
        <Bot className="w-7 h-7 text-brand-500" aria-hidden="true" />
      </div>
      <h3 className="font-serif-display text-lg font-semibold text-text-primary mb-1">
        与 {agentName} 协作
      </h3>
      <p className="text-sm text-text-tertiary">{positionLabel}</p>
      <p className="text-xs text-text-muted mt-3 max-w-xs">
        在下方输入框中发送消息，或点击上方建议提示词快速开始
      </p>
      <div className="flex flex-wrap gap-2 justify-center mt-4">
        {SUGGESTED_PROMPTS.slice(0, 3).map((prompt) => (
          <button
            key={prompt}
            onClick={() => onPickPrompt(prompt)}
            className="text-xs px-3 py-1.5 rounded-full bg-elevated text-text-secondary border border-border-default hover:bg-brand-50 hover:text-brand-500 hover:border-brand-200 transition-colors"
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  )
}

// ============================================================
// 子组件：未选择会话
// ============================================================

function NoActiveSession({
  agents,
  onCreateSession,
  creatingSession,
}: {
  agents: AgentRunMetrics[]
  onCreateSession: (agent: AgentRunMetrics) => void
  creatingSession: boolean
}) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
      <div className="w-16 h-16 rounded-xl bg-brand-50 flex items-center justify-center mb-5">
        <MessagesSquare className="w-8 h-8 text-brand-500" aria-hidden="true" />
      </div>
      <h3 className="font-serif-display text-xl font-semibold text-text-primary mb-2">
        智能体协作工作台
      </h3>
      <p className="text-sm text-text-tertiary max-w-sm mb-6 leading-relaxed">
        选择一位 AI 员工开始协作。通过 pi.dev SDK 驱动的实时流式通信，
        高效完成工作任务。
      </p>

      {/* 可协作员工快速选择 */}
      {agents.length > 0 && (
        <div className="w-full max-w-md space-y-2">
          <p className="text-xs font-medium text-text-secondary uppercase tracking-wider mb-3">
            可协作的 AI 员工
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {agents.slice(0, 6).map((agent) => (
              <button
                key={agent.agent_id}
                onClick={() => onCreateSession(agent)}
                disabled={creatingSession}
                className="flex items-center gap-2.5 px-3 py-2.5 rounded-md border border-border-default hover:border-brand-400 hover:bg-brand-50/30 transition-all text-left disabled:opacity-50"
              >
                <div className="w-8 h-8 rounded-md bg-brand-500 text-white flex items-center justify-center text-xs font-medium flex-shrink-0">
                  {agent.agent_name?.charAt(0) || '?'}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-text-primary truncate">{agent.agent_name}</p>
                  <p className="text-xs text-text-tertiary truncate">
                    {positionToLabel(agent.position, agent.agent_name)}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ============================================================
// 子组件：Agent 信息面板
// ============================================================

function AgentInfoPanel({
  agent,
  session,
}: {
  agent: AgentRunMetrics
  session: CollabSession | null
}) {
  // 工具使用前 3 项
  const topTools = useMemo(() => {
    const entries = Object.entries(agent.tool_usage || {})
    return entries.sort((a, b) => b[1] - a[1]).slice(0, 3)
  }, [agent.tool_usage])

  const stageLabel = STAGE_LABEL[agent.lifecycle_stage] || agent.lifecycle_stage
  const satisfaction = agent.avg_satisfaction > 0 ? agent.avg_satisfaction : null
  const responseMs = agent.avg_response_time_ms > 0 ? Math.round(agent.avg_response_time_ms) : null

  return (
    <div className="p-4 space-y-5">
      {/* Agent 卡片 */}
      <div className="flex items-center gap-3">
        <div className="w-12 h-12 rounded-md bg-brand-500 text-white flex items-center justify-center text-lg font-semibold flex-shrink-0">
          {agent.agent_name?.charAt(0) || '?'}
        </div>
        <div className="min-w-0">
          <h3 className="font-semibold text-text-primary truncate">{agent.agent_name}</h3>
          <p className="text-xs text-text-tertiary truncate">
            AI · {stageLabel}
          </p>
        </div>
      </div>
      <p className="text-xs text-text-secondary leading-relaxed">
        {positionToLabel(agent.position, agent.agent_name)}
      </p>

      {/* 3 列统计 */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="p-1.5 rounded bg-elevated">
          <div className="text-sm font-bold text-brand-500">{agent.tasks_total}</div>
          <div className="text-[10px] text-text-tertiary">任务</div>
        </div>
        <div className="p-1.5 rounded bg-elevated">
          <div className="text-sm font-bold text-success">{satisfaction !== null ? `${satisfaction.toFixed(2)}` : '—'}</div>
          <div className="text-[10px] text-text-tertiary">满意</div>
        </div>
        <div className="p-1.5 rounded bg-elevated">
          <div className="text-sm font-bold text-info">{responseMs !== null ? `${responseMs}ms` : '—'}</div>
          <div className="text-[10px] text-text-tertiary">响应</div>
        </div>
      </div>

      {/* Self-RAG 检索流程（产品能力示意） */}
      <div className="border-t border-border-subtle pt-4">
        <h4 className="text-sm font-semibold text-text-primary mb-2 flex items-center gap-1.5">
          <Database className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
          检索流程
        </h4>
        <p className="text-xs text-text-tertiary mb-2">Self-RAG · 检索 → 评估 → 生成</p>
        <svg viewBox="0 0 280 80" className="w-full h-16 mb-2" aria-hidden="true">
          <line x1="20" y1="40" x2="90" y2="40" stroke="#1E3A5F" strokeWidth="1.5" className="flow-dash" />
          <line x1="110" y1="40" x2="180" y2="40" stroke="#1E3A5F" strokeWidth="1.5" className="flow-dash" />
          <line x1="200" y1="40" x2="260" y2="40" stroke="#1E3A5F" strokeWidth="1.5" className="flow-dash" />
          <g><circle cx="10" cy="40" r="8" fill="#F0F4F9" stroke="#1E3A5F" strokeWidth="1.5" /><text x="10" y="44" textAnchor="middle" fontSize="8" fill="#1E3A5F">问</text></g>
          <g><circle cx="100" cy="40" r="8" fill="#F0F4F9" stroke="#1E3A5F" strokeWidth="1.5" /><text x="100" y="44" textAnchor="middle" fontSize="8" fill="#1E3A5F">检</text></g>
          <g><circle cx="190" cy="40" r="8" fill="#F0F4F9" stroke="#1E3A5F" strokeWidth="1.5" /><text x="190" y="44" textAnchor="middle" fontSize="8" fill="#1E3A5F">评</text></g>
          <g><circle cx="270" cy="40" r="8" fill="#DCFCE7" stroke="#16A34A" strokeWidth="1.5" /><text x="270" y="44" textAnchor="middle" fontSize="8" fill="#16A34A">答</text></g>
        </svg>
      </div>

      {/* 工具使用 */}
      {topTools.length > 0 && (
        <div className="border-t border-border-subtle pt-4">
          <h4 className="text-sm font-semibold text-text-primary mb-2 flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
            常用工具
          </h4>
          <div className="space-y-1.5">
            {topTools.map(([tool, count]) => (
              <div key={tool} className="flex justify-between items-center text-xs">
                <span className="text-text-tertiary truncate">{tool}</span>
                <span className="font-medium text-text-primary flex-shrink-0">{count as number}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 会话信息 */}
      {session && (
        <div className="text-[10px] text-text-muted text-center pt-2 border-t border-border-subtle">
          <p>协作始于 {formatRelativeTime(session.createdAt)}</p>
        </div>
      )}
    </div>
  )
}

// ============================================================
// 子组件：执行轨迹面板（多步推理 + 可展开工具详情）
// ============================================================

/** 工具名 → 中文标签 */
const TOOL_LABEL: Record<string, string> = {
  read: '读取文件',
  write: '写入文件',
  edit: '编辑文件',
  bash: '执行命令',
  grep: '搜索文本',
  ls: '列出目录',
}

function summarizeArgs(args: unknown): string {
  if (args == null) return ''
  try {
    const text = typeof args === 'string' ? args : JSON.stringify(args)
    return text.length > 60 ? `${text.slice(0, 60)}…` : text
  } catch {
    return String(args)
  }
}

/** 从工具参数中提取可读摘要（bash→命令，write/edit→路径，其余→JSON） */
function summarizeToolAction(tc: ToolCallEvent): string {
  if (tc.args == null) return ''
  if (typeof tc.args === 'string') return tc.args
  const obj = tc.args as Record<string, unknown>
  if (tc.toolName === 'bash' && typeof obj.command === 'string') return `$ ${obj.command}`
  if ((tc.toolName === 'write' || tc.toolName === 'edit') && typeof obj.path === 'string') return obj.path
  return summarizeArgs(tc.args)
}

/** 工具详情视图：bash→终端输出，edit→diff，write/edit→文件内容 */
function ToolDetailView({ tc }: { tc: ToolCallEvent }) {
  const output = tc.output || ''
  const isBash = tc.toolName === 'bash'

  // write 工具：参数里直接含完整 content，作为成果预览
  let writeContent: string | undefined
  if (tc.toolName === 'write' && tc.args && typeof tc.args === 'object') {
    const c = (tc.args as Record<string, unknown>).content
    if (typeof c === 'string') writeContent = c
  }

  const body = output || writeContent

  if (isBash || body) {
    const cls = isBash
      ? 'bg-canvas text-emerald-400 font-mono'
      : 'bg-canvas text-text-secondary font-mono'
    return (
      <pre
        className={`mt-2 max-h-56 overflow-auto rounded-md p-2.5 text-[11px] leading-relaxed whitespace-pre-wrap break-all ${cls}`}
      >
        {body || (isBash ? '（无输出）' : '（无内容）')}
      </pre>
    )
  }
  return null
}

function TrajectoryPanel({
  toolCalls,
  reasoningSteps,
  isStreaming,
}: {
  toolCalls: ToolCallEvent[]
  reasoningSteps: ReasoningStep[]
  isStreaming: boolean
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const hasActivity = toolCalls.length > 0 || reasoningSteps.length > 0

  return (
    <div className="border-b border-border-subtle">
      <div className="p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-semibold text-text-primary flex items-center gap-1.5">
            <ListOrdered className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
            执行轨迹
          </h4>
          {isStreaming && (
            <span className="inline-flex items-center gap-1 text-[10px] text-brand-500">
              <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" />
              运行中
            </span>
          )}
        </div>

        {!hasActivity ? (
          <p className="text-xs text-text-muted">
            发送任务后，这里会实时展示 AI 员工的多步推理、工具调用（命令输出 / 文件 diff）与交付成果物。
          </p>
        ) : (
          <div className="space-y-3">
            {/* 多步推理进度 */}
            {reasoningSteps.length > 0 && (
              <div>
                <p className="text-[10px] font-medium text-text-muted uppercase tracking-wide mb-1.5">
                  推理步骤
                </p>
                <div className="space-y-1">
                  {reasoningSteps.map((step) => (
                    <div key={step.turnIndex} className="flex items-center gap-2 text-xs">
                      {step.status === 'running' ? (
                        <Loader2 className="w-3 h-3 text-warning animate-spin flex-shrink-0" aria-hidden="true" />
                      ) : (
                        <CheckCircle2 className="w-3 h-3 text-success flex-shrink-0" aria-hidden="true" />
                      )}
                      <span className={step.status === 'running' ? 'text-text-secondary' : 'text-text-tertiary'}>
                        第 {step.turnIndex + 1} 步推理
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 工具调用轨迹（可展开） */}
            {toolCalls.length > 0 && (
              <div>
                <p className="text-[10px] font-medium text-text-muted uppercase tracking-wide mb-1.5">
                  {toolCalls.length} 次工具调用
                </p>
                <div className="space-y-1.5">
                  {toolCalls.map((tc) => {
                    const expanded = expandedId === tc.toolCallId
                    const Icon = tc.toolName === 'bash' ? TerminalIcon : Wrench
                    return (
                      <div
                        key={tc.toolCallId}
                        className={`rounded-md border bg-elevated/50 transition-colors ${
                          expanded ? 'border-brand-300' : 'border-border-default'
                        }`}
                      >
                        <button
                          type="button"
                          onClick={() => setExpandedId(expanded ? null : tc.toolCallId)}
                          className="w-full text-left p-2 flex items-center gap-1.5"
                        >
                          <Icon className="w-3 h-3 text-brand-500 flex-shrink-0" aria-hidden="true" />
                          <span className="text-xs font-medium text-text-primary flex-shrink-0">
                            {TOOL_LABEL[tc.toolName] || tc.toolName}
                          </span>
                          <span className="text-[11px] text-text-tertiary truncate flex-1">
                            {summarizeToolAction(tc)}
                          </span>
                          {tc.status === 'running' ? (
                            <Loader2 className="w-2.5 h-2.5 text-warning animate-spin flex-shrink-0" aria-hidden="true" />
                          ) : tc.status === 'success' ? (
                            <CheckCircle2 className="w-3 h-3 text-success flex-shrink-0" aria-hidden="true" />
                          ) : (
                            <X className="w-3 h-3 text-error flex-shrink-0" aria-hidden="true" />
                          )}
                          {expanded ? (
                            <ChevronDown className="w-3 h-3 text-text-muted flex-shrink-0" aria-hidden="true" />
                          ) : (
                            <ChevronRight className="w-3 h-3 text-text-muted flex-shrink-0" aria-hidden="true" />
                          )}
                        </button>
                        {expanded && <div className="px-2 pb-2"><ToolDetailView tc={tc} /></div>}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================
// 子组件：交付成果物面板（agent 在沙箱内生成的文件）
// ============================================================

function ArtifactsPanel({ artifacts }: { artifacts: Artifact[] }) {
  const [previewPath, setPreviewPath] = useState<string | null>(null)

  return (
    <div className="border-b border-border-subtle">
      <div className="p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-semibold text-text-primary flex items-center gap-1.5">
            <FileText className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
            交付成果物
            {artifacts.length > 0 && (
              <span className="text-[10px] text-text-muted font-normal">({artifacts.length})</span>
            )}
          </h4>
        </div>

        {artifacts.length === 0 ? (
          <p className="text-xs text-text-muted">
            AI 员工在沙箱内生成的文件（报价单、报告等）会汇总在这里。
          </p>
        ) : (
          <div className="space-y-1.5">
            {artifacts.map((artifact) => {
              const active = previewPath === artifact.path
              return (
                <div
                  key={artifact.path}
                  className={`rounded-md border bg-elevated/50 overflow-hidden ${
                    active ? 'border-brand-300' : 'border-border-default'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => setPreviewPath(active ? null : artifact.path)}
                    className="w-full text-left px-2.5 py-2 flex items-center gap-2"
                  >
                    <FileCode2 className="w-3.5 h-3.5 text-info flex-shrink-0" aria-hidden="true" />
                    <span className="text-xs font-medium text-text-primary truncate flex-1">{artifact.name}</span>
                    <span className="text-[10px] text-text-muted truncate max-w-[120px]">{artifact.path}</span>
                    {active ? (
                      <ChevronDown className="w-3 h-3 text-text-muted flex-shrink-0" aria-hidden="true" />
                    ) : (
                      <ChevronRight className="w-3 h-3 text-text-muted flex-shrink-0" aria-hidden="true" />
                    )}
                  </button>
                  {active && (
                    <pre className="max-h-56 overflow-auto border-t border-border-subtle bg-canvas p-2.5 text-[11px] text-text-secondary font-mono whitespace-pre-wrap break-all">
                      {artifact.content || '（无法预览内容）'}
                    </pre>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================
// 子组件：连接状态徽章
// ============================================================

function ConnectionBadge({ status, runnerOnline }: { status: ConnectionStatus; runnerOnline: number }) {
  const config: Record<ConnectionStatus, { label: string; color: string; dot: string }> = {
    connected: { label: '已连接', color: 'text-success', dot: 'bg-success' },
    connecting: { label: '连接中...', color: 'text-warning', dot: 'bg-warning animate-pulse' },
    disconnected: { label: '未连接', color: 'text-text-muted', dot: 'bg-text-muted' },
    error: { label: '连接错误', color: 'text-error', dot: 'bg-error' },
  }
  const cfg = config[status]

  return (
    <div className="p-2 border-t border-border-subtle space-y-1">
      <div className={`flex items-center justify-center gap-1.5 text-[10px] ${cfg.color}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
        {cfg.label}
      </div>
      {/* 本地 Runner 在线状态（§7 断线重连提示） */}
      <div className={`flex items-center justify-center gap-1.5 text-[10px] ${runnerOnline > 0 ? 'text-violet-600' : 'text-text-muted'}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${runnerOnline > 0 ? 'bg-violet-500' : 'bg-text-muted'}`} />
        {runnerOnline > 0 ? `${runnerOnline} 台本地 Runner 在线` : '本地 Runner 未连接'}
      </div>
    </div>
  )
}