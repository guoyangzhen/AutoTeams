import { useState, useEffect, useRef, useCallback, Fragment } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Send, Square, Plus, Sparkles, Menu, X,
  Copy, Check, ThumbsUp, ThumbsDown, RefreshCw,
  FileText, ChevronRight, ChevronDown, BookOpen,
  Edit, Trash2, Paperclip, Database,
  MessageSquare, ArrowUpRight, Zap, Search,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { MarkdownRenderer } from '@/components/MarkdownRenderer'
import { ChatAgentPanel } from '@/components/chat/ChatAgentPanel'
import { RetrievalTrace } from '@/components/instrument'
import { formatRelevance, toRelevanceValue } from '@/utils/format'
import { getAgent, updateAgent } from '@/api/agents'
import { getSkills } from '@/api/skills'
import { batchUploadFiles } from '@/api/files'
import { getConversations, createConversation, getMessages, updateSatisfaction, updateConversation, deleteConversation, recordImplicitFeedback } from '@/api/conversations'
import { getLoopStats, type LoopStats } from '@/api/loop'
import { useSSE } from '@/hooks/useSSE'
import { useConfirmDialog } from '@/hooks/useConfirmDialog'
import { Agent, Conversation, Message, Skill, Source } from '@/types'
import { Button } from '@/components/ui/Button'

// ============================================================
// 常量
// ============================================================
const EXAMPLE_QUESTIONS = [
  '你好，请介绍一下你自己',
  '你能帮我解决什么问题？',
  '知识库里有哪些类型的信息？',
  '给我举个例子说明你的能力',
]

const MAX_INPUT_LENGTH = 4000

/**
 * 模型选择器清单（§4.4.2 对话工作台 — 第一交互面）。
 *
 * 后端 chat 端点读取 `agent.config.model`（P1-6.4，LiteLLM `provider/model` 格式，
 * 见 backend/app/api/agents/chat.py），切换器写入该字段后立即生效，
 * 并由后端 AgentVersion 快照留痕。与 ai/registry.py 的已注册模型对齐：
 * - DeepSeek：主力对话（性价比最高，registry deepseek 默认链）
 * - Kimi：128k 长上下文（长文档/长会话）
 * - GLM-4：复杂推理（registry strong 档）
 */
export const MODEL_OPTIONS = [
  {
    id: 'deepseek/deepseek-chat',
    label: 'DeepSeek-V4.1-Flash',
    tag: '主力',
    tagClass: 'bg-brand-50 text-brand-500',
    description: '日常对话主力，响应快、性价比高',
  },
  {
    id: 'moonshot/moonshot-v1-128k',
    label: 'Kimi（长上下文）',
    tag: '128k',
    tagClass: 'bg-warning/10 text-warning',
    description: '长文档阅读与超长会话记忆',
  },
  {
    id: 'zhipu/glm-4',
    label: 'GLM-4',
    tag: '推理',
    tagClass: 'bg-elevated text-text-secondary',
    description: '复杂推理与结构化分析任务',
  },
] as const

// ============================================================
// 类型定义
// ============================================================

/** D2-S9: SSE 流中的检索元数据（D3 stream_chat 已透传） */
interface RetrievalMetadata {
  query_type?: string
  retrieval_rounds?: number
  refined_queries?: string[]
  sources?: Source[]
  self_rag_score?: number | null
}

/** 流式消息（SSE 累积的临时消息） */
interface StreamMessage {
  role: string
  content: string
  sources?: Source[]
  /**
   * 后端真实 message_id（SSE done 帧下发）。
   *
   * 此前该字段被丢弃，流式消息落库时被赋予 `local-ai-${Date.now()}`
   * 这类伪 ID，导致点赞/点踩调用 PUT /messages/{id}/satisfaction
   * 必然 404 —— 用户点了没反应，反馈数据也从未真正收集到。
   */
  messageId?: string
}

// ============================================================
// 工具函数
// ============================================================
function formatTime(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

function formatDate(dateStr: string): string {
  try {
    const date = new Date(dateStr)
    const today = new Date()
    const yesterday = new Date(today)
    yesterday.setDate(yesterday.getDate() - 1)
    if (date.toDateString() === today.toDateString()) return '今天'
    if (date.toDateString() === yesterday.toDateString()) return '昨天'
    return date.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })
  } catch {
    return ''
  }
}

function getStatusLabel(status: Agent['status']): { label: string; dotClass: string; textClass: string } {
  switch (status) {
    case 'ready':
      return { label: '运行中', dotClass: 'dot-success', textClass: 'text-success' }
    case 'processing':
      return { label: '处理中', dotClass: 'dot-warning', textClass: 'text-warning' }
    case 'error':
      return { label: '异常', dotClass: 'dot-error', textClass: 'text-error' }
    default:
      return { label: '未知', dotClass: 'dot-muted', textClass: 'text-text-tertiary' }
  }
}

/** D2-S9: 格式化 Self-RAG 评分为百分比显示 */
function formatSelfRagScore(score: number | null | undefined): string {
  if (score === null || score === undefined) return '—'
  return `${Math.round(Math.max(0, Math.min(1, score)) * 100)}%`
}

// ============================================================
// 消息气泡组件
// ============================================================
interface MessageBubbleProps {
  message: Message
  isStreaming?: boolean
  retrievalMetadata?: RetrievalMetadata | null
  /** 触发本次检索的用户提问（供 RetrievalTrace 展示第一轮查询） */
  retrievalQuery?: string
  onSatisfaction?: (messageId: string, satisfaction: string) => void
  onRegenerate?: () => void
}

function MessageBubble({
  message,
  isStreaming = false,
  retrievalMetadata = null,
  retrievalQuery,
  onSatisfaction,
  onRegenerate,
}: MessageBubbleProps) {
  const [copied, setCopied] = useState(false)
  const [sourcesOpen, setSourcesOpen] = useState(false)
  /** §4.4.2-3：当前展开全文的来源下标（null = 全部收起） */
  const [expandedSourceIdx, setExpandedSourceIdx] = useState<number | null>(null)
  const isUser = message.role === 'user'

  // 反馈按钮可用性：仅当消息具备后端真实 ID 时启用。
  // 本地临时 ID（stream- / local-ai- 前缀）调用后端 PUT /messages/{id}/satisfaction 必然 404。
  const canFeedback = !message.id.startsWith('stream-') && !message.id.startsWith('local-ai-')

  // ---- 隐式满意度上报：dwell（停留时长） ----
  // 仅对 AI 消息且非流式状态启用：mount 时记录开始时间，unmount 时计算 dwell_seconds，
  // 若 < 3 秒（用户快速切走）则上报 implicit:unsatisfied:dwell。
  // 用 ref 缓存必要数据避免闭包陈旧；fire-and-forget，失败静默。
  const mountedAtRef = useRef<number>(Date.now())
  const messageIdRef = useRef<string>(message.id)
  const canFeedbackRef = useRef<boolean>(canFeedback)
  const dwellReportedRef = useRef<boolean>(false)

  useEffect(() => {
    mountedAtRef.current = Date.now()
    messageIdRef.current = message.id
    canFeedbackRef.current = canFeedback
    dwellReportedRef.current = false
    return () => {
      if (dwellReportedRef.current) return
      const dwellSeconds = (Date.now() - mountedAtRef.current) / 1000
      // 同样要求真实 ID：伪 ID 上报只会产生一串 404，白白浪费请求
      if (
        dwellSeconds < 3 &&
        !isUser &&
        !isStreaming &&
        messageIdRef.current &&
        canFeedbackRef.current
      ) {
        dwellReportedRef.current = true
        recordImplicitFeedback(messageIdRef.current, {
          signal: 'dwell',
          dwell_seconds: Math.max(1, Math.floor(dwellSeconds)),
        }).catch(() => {
          /* 静默失败：不影响 UI */
        })
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [message.id])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      // 隐式满意度上报：copy → implicit:satisfied:copy
      // fire-and-forget，不阻塞复制流程；仅对具备真实 ID 的消息上报
      if (!isUser && message.id && canFeedback) {
        recordImplicitFeedback(message.id, { signal: 'copy' }).catch(() => {
          /* 静默失败 */
        })
      }
    } catch {
      // 剪贴板 API 可能在非 HTTPS 下不可用
    }
  }

  // ---- 重新生成（附带隐式反馈：regenerate） ----
  const handleRegenerateClick = () => {
    if (!onRegenerate) return
    // 仅对具备真实 ID 的消息上报，避免伪 ID 触发 404
    if (message.id && canFeedback) {
      recordImplicitFeedback(message.id, { signal: 'regenerate' }).catch(() => {
        /* 静默失败 */
      })
    }
    onRegenerate()
  }

  // ---- 用户消息 ----
  if (isUser) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
        className="group flex justify-end"
      >
        <div className="flex flex-col items-end" style={{ maxWidth: '80%' }}>
          <div className="bg-brand-500 text-white rounded-2xl rounded-br-md px-4 py-3 text-sm leading-relaxed transition-colors hover:bg-brand-600">
            <div className="whitespace-pre-wrap break-words">{message.content}</div>
          </div>
          <div className="flex items-center gap-2 mt-1">
            <button
              onClick={handleCopy}
              className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity w-8 h-8 flex items-center justify-center rounded-lg text-text-tertiary hover:text-text-primary hover:bg-elevated focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              title="复制"
              aria-label="复制消息"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
            <span className="text-xs text-text-tertiary font-mono">{formatTime(message.created_at)}</span>
          </div>
        </div>
      </motion.div>
    )
  }

  // ---- AI 消息 ----
  const hasSources = message.sources && message.sources.length > 0
  const hasRetrievalInfo =
    retrievalMetadata &&
    (retrievalMetadata.retrieval_rounds !== undefined ||
      retrievalMetadata.self_rag_score !== undefined ||
      (retrievalMetadata.refined_queries && retrievalMetadata.refined_queries.length > 0))

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className="group flex justify-start"
    >
      <div className="flex gap-2.5" style={{ maxWidth: '85%' }}>
        {/* AI 头像 */}
        <div className="w-8 h-8 rounded-full bg-brand-50 text-brand-500 flex items-center justify-center text-xs font-semibold flex-shrink-0">
          AI
        </div>

        <div className="flex flex-col min-w-0" style={{ maxWidth: '32rem' }}>
          {/* 气泡内容 */}
          <div
            className={`bg-surface border border-border-default rounded-2xl rounded-bl-md px-4 py-3 text-sm leading-relaxed text-text-primary transition-all hover:border-brand-200 hover:shadow-soft ${
              isStreaming ? 'typing-cursor' : ''
            }`}
          >
            <MarkdownRenderer content={message.content || '…'} />

            {/* Self-RAG 检索轨迹（多轮迭代 + 改写查询 + 自评分 + 命中片段） */}
            {hasRetrievalInfo && !isStreaming && (
              <RetrievalTrace
                className="mt-3"
                queryType={retrievalMetadata!.query_type}
                rounds={retrievalMetadata!.retrieval_rounds}
                refinedQueries={retrievalMetadata!.refined_queries}
                selfRagScore={retrievalMetadata!.self_rag_score}
                sources={retrievalMetadata!.sources}
                query={retrievalQuery}
              />
            )}

          {/* 引用来源（带展开动画，替代原生 <details> 的瞬间跳变）。
              §4.4.2-3：来源引用可点击、可追溯 —— 全文展开/收起 + 相关度高亮。 */}
            {hasSources && !isStreaming && (
              <div className="mt-3 bg-elevated rounded-md border border-border-subtle">
                <button
                  type="button"
                  onClick={() => setSourcesOpen((v) => !v)}
                  aria-expanded={sourcesOpen}
                  className="w-full flex items-center gap-1.5 text-xs font-semibold text-text-tertiary px-3 py-2 text-left"
                >
                  <BookOpen className="w-3.5 h-3.5" aria-hidden="true" />
                  <span>引用来源 ({message.sources!.length})</span>
                  <ChevronRight
                    className={`w-3 h-3 ml-auto transition-transform ${sourcesOpen ? 'rotate-90' : ''}`}
                    aria-hidden="true"
                  />
                </button>
                <AnimatePresence initial={false}>
                  {sourcesOpen && (
                    <motion.div
                      key="sources"
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
                      className="overflow-hidden"
                    >
                      <div className="px-3 pb-3 space-y-1">
                        {message.sources!.map((source, index) => {
                          // §4.4.2-3 可追溯：单条来源可展开查看完整原文片段
                          const isExpanded = expandedSourceIdx === index
                          const excerpt = source.content ?? ''
                          const isTruncatable = excerpt.length > 120
                          // 相关度高亮：与 formatRelevance 显示值同一坐标系
                          // （distance → 1/(1+d) → 0-100），避免「读数 56% 却标绿」
                          // 的口径错位；缺失距离（显示 —）保持中性色。
                          const relScore = toRelevanceValue(source.distance)
                          const relClass =
                            relScore >= 80
                              ? 'text-success'
                              : relScore >= 50
                                ? 'text-brand-500'
                                : 'text-text-tertiary'
                          return (
                            <div
                              key={index}
                              className="flex items-start gap-2 text-xs text-text-primary py-1.5 px-1.5 rounded hover:bg-surface-2 transition-colors"
                            >
                              <FileText className="w-3.5 h-3.5 text-text-tertiary flex-shrink-0 mt-0.5" />
                              <div className="flex-1 min-w-0">
                                <div className="font-medium break-words">
                                  {source.source || '未知来源'}
                                </div>
                                {excerpt && (
                                  <p
                                    className={`mt-0.5 text-text-tertiary leading-relaxed whitespace-pre-wrap break-words ${
                                      isExpanded ? '' : 'line-clamp-2'
                                    }`}
                                  >
                                    {excerpt}
                                  </p>
                                )}
                                {isTruncatable && (
                                  <button
                                    type="button"
                                    onClick={() =>
                                      setExpandedSourceIdx(isExpanded ? null : index)
                                    }
                                    className="mt-1 text-[11px] text-brand-500 hover:text-brand-600 font-medium"
                                    aria-expanded={isExpanded}
                                  >
                                    {isExpanded ? '收起全文' : '查看全文'}
                                  </button>
                                )}
                              </div>
                              {/* 相关度统一走 formatRelevance：原 `1 - distance`
                                  对余弦距离（0-2）与 L2 距离（无上界）都会算出负值；
                                  §4.4.2-3：高分来源用色彩高亮，可扫读定位最可信引用 */}
                              <span
                                className={`font-mono text-xs flex-shrink-0 ${relClass}`}
                                title="向量检索相关度"
                              >
                                {formatRelevance(source.distance)}
                              </span>
                            </div>
                          )
                        })}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}
          </div>

          {/* 操作按钮栏（hover 时显示）— FIX-09: 28px 容器 / 14px icon */}
          {!isStreaming && (
            <div className="flex items-center gap-1 mt-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
              <button
                onClick={handleCopy}
                className="w-8 h-8 flex items-center justify-center rounded-lg text-text-tertiary hover:text-text-primary hover:bg-elevated transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                title="复制"
                aria-label="复制消息"
              >
                {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
              </button>
              {onSatisfaction && (
                <>
                  {/* 仅当消息具备后端真实 ID 时才可反馈：
                      本地临时 ID（stream-/local-ai- 前缀）调用后端必然 404，
                      与其让用户点击后静默失败，不如明确禁用并说明原因 */}
                  <button
                    onClick={() => onSatisfaction(message.id, 'satisfied')}
                    disabled={!canFeedback}
                    className={`w-8 h-8 flex items-center justify-center rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                      message.satisfaction === 'satisfied'
                        ? 'text-success'
                        : 'text-text-tertiary hover:text-success hover:bg-success/10'
                    }`}
                    title={canFeedback ? '满意' : '该消息尚未保存，暂无法反馈'}
                    aria-label="满意"
                  >
                    <ThumbsUp className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => onSatisfaction(message.id, 'unsatisfied')}
                    disabled={!canFeedback}
                    className={`w-8 h-8 flex items-center justify-center rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                      message.satisfaction === 'unsatisfied'
                        ? 'text-error'
                        : 'text-text-tertiary hover:text-error hover:bg-error/10'
                    }`}
                    title={canFeedback ? '不满意' : '该消息尚未保存，暂无法反馈'}
                    aria-label="不满意"
                  >
                    <ThumbsDown className="h-3.5 w-3.5" />
                  </button>
                </>
              )}
              {onRegenerate && (
                <button
                  onClick={handleRegenerateClick}
                  className="w-8 h-8 flex items-center justify-center rounded-lg text-text-tertiary hover:text-text-primary hover:bg-elevated transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  title="重新生成"
                  aria-label="重新生成"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                </button>
              )}
              <span className="text-xs text-text-tertiary font-mono ml-1">
                {formatTime(message.created_at)}
              </span>
            </div>
          )}

          {isStreaming && (
            <div className="flex items-center gap-1 mt-1">
              <span className="text-xs text-text-tertiary font-mono">生成中…</span>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  )
}

// ============================================================
// 检索思考指示器（D2-S9: 流式开始前展示检索状态）
// ============================================================
interface RetrievalThinkingIndicatorProps {
  metadata: RetrievalMetadata | null
}

function RetrievalThinkingIndicator({ metadata }: RetrievalThinkingIndicatorProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="flex justify-start"
    >
      <div className="flex gap-2.5">
        <div className="w-8 h-8 rounded-full bg-brand-50 text-brand-500 flex items-center justify-center text-xs font-semibold flex-shrink-0">
          AI
        </div>
        <div className="bg-surface border border-border-default rounded-2xl rounded-bl-md px-4 py-3.5 flex flex-col gap-2">
          <div className="flex items-center gap-1.5">
            <span className="typing-dot" />
            <span className="typing-dot" />
            <span className="typing-dot" />
          </div>
          {metadata && (
            <div className="flex items-center gap-3 text-xs text-text-tertiary">
              <span className="flex items-center gap-1">
                <Search className="w-3 h-3" />
                {metadata.retrieval_rounds && metadata.retrieval_rounds > 1
                  ? `检索中（${metadata.retrieval_rounds} 轮）`
                  : '检索知识库中'}
              </span>
              {metadata.self_rag_score !== undefined &&
                metadata.self_rag_score !== null && (
                  <span className="font-mono">
                    评分 {formatSelfRagScore(metadata.self_rag_score)}
                  </span>
                )}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  )
}

// ============================================================
// 主组件
// ============================================================
interface ChatProps {
  /** 显式传入的 agentId（嵌入式使用时）；未传则从 useParams 解析 */
  agentId?: string
  /** 嵌入式模式：跳过 <Layout> 外壳，并调整高度以适配宿主页面 */
  embedded?: boolean
}

export default function Chat({ agentId: propAgentId, embedded = false }: ChatProps = {}) {
  const params = useParams<{ agentId: string }>()
  const agentId = propAgentId || params.agentId
  const navigate = useNavigate()
  const confirmDialog = useConfirmDialog()

  // ---- 状态 ----
  const [agent, setAgent] = useState<Agent | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [currentConversation, setCurrentConversation] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [showSidebar, setShowSidebar] = useState(false)
  // 协作工作台：真实运行指标（来自 /loop/{agentId}/stats）
  const [loopStats, setLoopStats] = useState<LoopStats | null>(null)
  // 协作重命名/删除状态
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renamingValue, setRenamingValue] = useState('')
  const [deletingConvId, setDeletingConvId] = useState<string | null>(null)
  // 附件上传（写入该 Agent 的知识库）
  const attachInputRef = useRef<HTMLInputElement>(null)
  const [attachUploading, setAttachUploading] = useState(false)
  const [attachNotice, setAttachNotice] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)

  // D2-S9: SSE 流式状态（直接使用 useSSE 以支持 retrieval_metadata 事件）
  const [streamMessages, setStreamMessages] = useState<StreamMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [retrievalMetadata, setRetrievalMetadata] = useState<RetrievalMetadata | null>(null)
  // 触发本轮检索的用户提问，供 RetrievalTrace 显示「原始提问 → 改写查询」轨迹
  const [lastUserQuery, setLastUserQuery] = useState<string>('')
  const accumulatedContentRef = useRef<string>('')

  // ---- Refs ----
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const currentConversationRef = useRef<Conversation | null>(null)
  const prevStreamingRef = useRef(false)

  // ---- SSE 流式（直接使用 useSSE 以解析 retrieval_metadata 事件）----
  const { connect, disconnect } = useSSE({
    onMessage: (data) => {
      if (typeof data === 'object' && data !== null) {
        const parsed = data as {
          content?: string
          done: boolean
          message_id?: string
          conversation_id?: string
          error?: string
          sources?: Source[]
          retrieval_metadata?: RetrievalMetadata
        }

        // D2-S9: 解析检索元数据（在流式内容开始前到达）
        if (parsed.retrieval_metadata) {
          setRetrievalMetadata(parsed.retrieval_metadata)
        }

        if (parsed.done) {
          setIsStreaming(false)
          // BE-REL-03: 后端明确返回错误/保存失败时提示用户
          if (parsed.error || parsed.message_id === 'error') {
            setStreamError(parsed.error || '消息保存失败，历史记录可能缺失')
            return
          }
          // 捕获后端真实 message_id 与检索来源，挂载到当前助手消息。
          // message_id 是满意度反馈的必要条件，此前被丢弃导致点赞/点踩失效。
          setStreamMessages((prev) => {
            const last = prev[prev.length - 1]
            if (last && last.role === 'assistant') {
              return [
                ...prev.slice(0, -1),
                {
                  ...last,
                  messageId: parsed.message_id,
                  sources:
                    parsed.sources && parsed.sources.length > 0
                      ? parsed.sources
                      : last.sources,
                },
              ]
            }
            return prev
          })
          return
        }
        if (parsed.error) {
          setStreamError(parsed.error)
          setIsStreaming(false)
          return
        }
        const content = parsed.content
        if (content) {
          accumulatedContentRef.current += content
          setStreamMessages((prev) => {
            const last = prev[prev.length - 1]
            if (last && last.role === 'assistant') {
              return [
                ...prev.slice(0, -1),
                { ...last, content: accumulatedContentRef.current },
              ]
            }
            return [...prev, { role: 'assistant', content, sources: undefined }]
          })
        }
      }
    },
    onError: (error) => {
      setStreamError(error.message)
      setIsStreaming(false)
    },
    onClose: () => {
      setIsStreaming(false)
    },
  })

  // ---- 发送流式消息 ----
  const sendStreamMessage = useCallback(
    (content: string) => {
      if (!agentId) return
      accumulatedContentRef.current = ''
      setStreamMessages([])
      setStreamError(null)
      setRetrievalMetadata(null)
      setLastUserQuery(content)
      setIsStreaming(true)
      connect(`/api/v1/agents/${agentId}/chat/stream`, {
        conversation_id: currentConversationRef.current?.id,
        content,
      })
    },
    [agentId, connect],
  )

  // ---- 停止生成 ----
  const handleStopStreaming = useCallback(() => {
    disconnect()
    setIsStreaming(false)
  }, [disconnect])

  // ---- 清空流式消息 ----
  const clearStreamMessages = useCallback(() => {
    setStreamMessages([])
    accumulatedContentRef.current = ''
    setRetrievalMetadata(null)
  }, [])

  // ---- 选择协作 ----
  const selectConversation = useCallback(
    async (conversation: Conversation) => {
      setCurrentConversation(conversation)
      currentConversationRef.current = conversation
      clearStreamMessages()
      try {
        const messagesData = await getMessages(conversation.id)
        setMessages(messagesData)
      } catch (error) {
        console.error('获取消息失败:', error)
        setMessages([])
      }
    },
    [clearStreamMessages],
  )

  // ---- 加载数据 ----
  const loadData = useCallback(async () => {
    if (!agentId) return
    try {
      setLoading(true)
      const [agentData, conversationsData, skillsData, statsData] = await Promise.all([
        getAgent(agentId),
        getConversations(agentId),
        getSkills(agentId).catch(() => [] as Skill[]),
        getLoopStats(agentId, 1).catch(() => null as LoopStats | null),
      ])
      setAgent(agentData)
      setConversations(conversationsData)
      setSkills(skillsData)
      setLoopStats(statsData)

      if (conversationsData.length > 0) {
        await selectConversation(conversationsData[0])
      }
    } catch (error) {
      console.error('加载失败:', error)
    } finally {
      setLoading(false)
    }
  }, [agentId, selectConversation])

  useEffect(() => {
    loadData()
  }, [loadData])

  useEffect(() => {
    currentConversationRef.current = currentConversation
  }, [currentConversation])

  // ---- 新建协作 ----
  const handleNewConversation = useCallback(async (): Promise<Conversation | null> => {
    try {
      const newConversation = await createConversation(agentId!, '新协作')
      setConversations((prev) => [newConversation, ...prev])
      setCurrentConversation(newConversation)
      currentConversationRef.current = newConversation
      setMessages([])
      clearStreamMessages()
      return newConversation
    } catch (error) {
      console.error('创建协作失败:', error)
      return null
    }
  }, [agentId, clearStreamMessages])

  // ---- 发送消息 ----
  const handleSend = useCallback(
    async (overrideMessage?: string) => {
      const messageText = (overrideMessage ?? input).trim()
      if (!messageText || isStreaming) return

      setInput('')

      let conv = currentConversationRef.current
      if (!conv) {
        conv = await handleNewConversation()
        if (!conv) return
      }

      // 立即在本地添加用户消息（无需等待后端回显）
      const userMessage: Message = {
        id: `local-user-${Date.now()}`,
        conversation_id: conv.id,
        role: 'user',
        content: messageText,
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, userMessage])

      sendStreamMessage(messageText)
    },
    [input, isStreaming, sendStreamMessage, handleNewConversation],
  )

  // ---- 按键处理 ----
  const handleKeyPress = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // ---- 满意度反馈 ----
  const handleSatisfaction = useCallback(
    async (messageId: string, satisfaction: string) => {
      try {
        await updateSatisfaction(messageId, satisfaction)
        setMessages((prev) =>
          prev.map((msg) => (msg.id === messageId ? { ...msg, satisfaction } : msg)),
        )
      } catch (error) {
        console.error('更新满意度失败:', error)
      }
    },
    [],
  )

  // ---- 重新生成 ----
  const handleRegenerate = useCallback(() => {
    if (isStreaming) return
    const lastUserMsg = [...messages].reverse().find((m) => m.role === 'user')
    if (lastUserMsg) {
      sendStreamMessage(lastUserMsg.content)
    }
  }, [messages, isStreaming, sendStreamMessage])

  // ---- 清空协作（新建一个协作） ----
  const handleClearConversation = useCallback(async () => {
    await handleNewConversation()
    setShowSidebar(false)
  }, [handleNewConversation])

  // ---- 附件上传：写入当前 Agent 的知识库 ----
  const handleAttachFiles = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const selected = Array.from(e.target.files || [])
      // 先清空 input.value，保证同一文件连续选择两次也能触发 change
      e.target.value = ''
      if (!selected.length || !agentId) return
      setAttachUploading(true)
      setAttachNotice(null)
      try {
        const result = await batchUploadFiles(agentId, selected)
        setAttachNotice({
          tone: result.failed > 0 ? 'err' : 'ok',
          text:
            result.failed > 0
              ? `已上传 ${result.successful} 个，${result.failed} 个失败`
              : `已上传 ${result.successful} 个文件，索引完成后即可被检索`,
        })
        // 知识条目数会变化，刷新 Agent 以更新「知识库已连接」等状态
        getAgent(agentId).then(setAgent).catch(() => {})
      } catch {
        setAttachNotice({ tone: 'err', text: '上传失败，请稍后重试' })
      } finally {
        setAttachUploading(false)
      }
    },
    [agentId],
  )

  // 上传提示 4 秒后自动消失
  useEffect(() => {
    if (!attachNotice) return
    const timer = setTimeout(() => setAttachNotice(null), 4000)
    return () => clearTimeout(timer)
  }, [attachNotice])

  // ---- 模型切换（§4.4.2：写入 agent.config.model，后端 P1-6.4 读取后即时生效） ----
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const [switchingModel, setSwitchingModel] = useState(false)
  const [modelNotice, setModelNotice] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)

  // 当前生效模型：config.model 缺失时回退主力模型；
  // 若后端配置了清单外的模型，直接展示原始模型名（不伪装成清单内选项）
  const rawModelId = (agent?.config?.model as string | undefined) || MODEL_OPTIONS[0].id
  const activeModelOption = MODEL_OPTIONS.find((m) => m.id === rawModelId)
  const currentModelLabel = activeModelOption
    ? activeModelOption.label
    : rawModelId.includes('/')
      ? rawModelId.split('/').pop()!
      : rawModelId

  const handleModelSelect = useCallback(
    async (modelId: string) => {
      setModelMenuOpen(false)
      if (!agentId || !agent || switchingModel) return
      if (modelId === (agent.config?.model as string | undefined)) return
      const label = MODEL_OPTIONS.find((m) => m.id === modelId)?.label ?? modelId
      setSwitchingModel(true)
      setModelNotice(null)
      try {
        // 后端 PUT /agents/{id} 对 config 做合并更新（crud.py），且会
        // 自动创建 AgentVersion 快照，便于审计与回滚
        const updated = await updateAgent(agentId, {
          config: { model: modelId },
          changelog: `切换对话模型为 ${modelId}`,
        })
        setAgent(updated)
        setModelNotice({ tone: 'ok', text: `已切换到 ${label}，下一条消息生效` })
      } catch (err) {
        console.error('切换模型失败:', err)
        // axios 错误：区分权限（require_admin）与网络/服务异常，避免误导归因
        const status = (err as { response?: { status?: number } } | null)?.response?.status
        setModelNotice(
          status === 403
            ? { tone: 'err', text: '权限不足：切换模型需要管理员账号' }
            : status === 404
              ? { tone: 'err', text: '智能体不存在或已被删除' }
              : { tone: 'err', text: '切换失败，请稍后重试' },
        )
      } finally {
        setSwitchingModel(false)
      }
    },
    [agentId, agent, switchingModel],
  )

  // 点击菜单外区域 / 按 Escape 收起（toggle 按钮已 stopPropagation）
  useEffect(() => {
    if (!modelMenuOpen) return
    const close = () => setModelMenuOpen(false)
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    document.addEventListener('click', close)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('click', close)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [modelMenuOpen])

  // 模型切换提示 4 秒后自动消失
  useEffect(() => {
    if (!modelNotice) return
    const timer = setTimeout(() => setModelNotice(null), 4000)
    return () => clearTimeout(timer)
  }, [modelNotice])

  // ---- 协作重命名（激活 PUT /conversations/{id}） ----
  const handleStartRename = useCallback((conv: Conversation) => {
    setRenamingId(conv.id)
    setRenamingValue(conv.title || '新协作')
  }, [])

  const handleCancelRename = useCallback(() => {
    setRenamingId(null)
    setRenamingValue('')
  }, [])

  const handleConfirmRename = useCallback(async (conv: Conversation) => {
    const trimmed = renamingValue.trim()
    if (!trimmed) {
      setRenamingId(null)
      return
    }
    try {
      const updated = await updateConversation(conv.id, trimmed)
      setConversations((prev) =>
        prev.map((c) => (c.id === conv.id ? { ...c, title: updated.title, updated_at: updated.updated_at } : c)),
      )
      if (currentConversation?.id === conv.id) {
        setCurrentConversation((prev) => (prev ? { ...prev, title: updated.title, updated_at: updated.updated_at } : prev))
      }
    } catch (err) {
      console.error('重命名协作失败:', err)
    } finally {
      setRenamingId(null)
      setRenamingValue('')
    }
  }, [renamingValue, currentConversation])

  // ---- 协作删除（激活 DELETE /conversations/{id}） ----
  const handleDeleteConversation = useCallback(async (conv: Conversation) => {
    const ok = await confirmDialog.ask({
      title: '删除协作',
      description: `确定删除协作「${conv.title || '新协作'}」吗？此操作不可恢复。`,
      confirmText: '删除',
      cancelText: '取消',
      variant: 'danger',
    })
    if (!ok) return
    setDeletingConvId(conv.id)
    try {
      await deleteConversation(conv.id)
      setConversations((prev) => prev.filter((c) => c.id !== conv.id))
      // 若删除的是当前协作，切到第一个或新建
      if (currentConversation?.id === conv.id) {
        setCurrentConversation(null)
        setMessages([])
        const remaining = conversations.filter((c) => c.id !== conv.id)
        if (remaining.length > 0) {
          selectConversation(remaining[0])
        }
      }
    } catch (err) {
      console.error('删除协作失败:', err)
    } finally {
      setDeletingConvId(null)
    }
  }, [conversations, currentConversation, selectConversation, confirmDialog])

  // ---- 合并历史消息 + 流式消息 ----
  const allMessages: Message[] = [
    ...messages,
    ...streamMessages.map((msg, index) => ({
      // 优先用后端真实 ID，流式进行中尚未下发时才回退到临时键
      id: msg.messageId || `stream-${index}`,
      conversation_id: currentConversation?.id || '',
      role: msg.role as 'user' | 'assistant' | 'system',
      content: msg.content,
      sources: msg.sources,
      created_at: new Date().toISOString(),
    })),
  ]

  // ---- 流式结束后，将流式消息持久化到历史消息 ----
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming && streamMessages.length > 0) {
      const persisted = streamMessages.map((msg, index) => ({
        // 使用后端真实 message_id，使满意度反馈可用；
        // 缺失时（异常/旧后端）才退回本地键，此时反馈按钮会被禁用
        id: msg.messageId || `local-ai-${Date.now()}-${index}`,
        conversation_id: currentConversationRef.current?.id || '',
        role: msg.role as 'user' | 'assistant' | 'system',
        content: msg.content,
        sources: msg.sources,
        created_at: new Date().toISOString(),
      }))
      setMessages((prev) => [...prev, ...persisted])
      clearStreamMessages()
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming, streamMessages, clearStreamMessages])

  // ---- 自动滚动到底部 ----
  useEffect(() => {
    if (allMessages.length > 0) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [allMessages.length, isStreaming, streamMessages])

  // ---- 是否正在流式生成最后一条消息 ----
  const isLastMessageStreaming =
    isStreaming &&
    streamMessages.length > 0 &&
    streamMessages[streamMessages.length - 1].role === 'assistant'

  // ---- 示例问题点击 ----
  const handleExampleClick = (question: string) => {
    handleSend(question)
  }

  // ---- 跨天日期分隔 ----
  // 原实现只按「最后一条消息」渲染唯一一个分隔条，导致历史里所有跨天消息
  // 都被标成同一天（今天）。改为逐条比对相邻消息的日期，仅在换天处插入分隔。
  const dayKeys = allMessages.map((m) => {
    const d = new Date(m.created_at)
    return Number.isNaN(d.getTime()) ? '' : d.toDateString()
  })

  // ============================================================
  // 加载/错误状态
  // ============================================================
  if (loading) {
    return (
      <Layout>
        <div className={`flex items-center justify-center ${embedded ? 'h-[60vh]' : 'min-h-[60vh]'}`}>
          <div className="skeleton h-8 w-8 rounded-full" />
        </div>
      </Layout>
    )
  }

  if (!agent) {
    return (
      <Layout>
        <div className={`flex items-center justify-center ${embedded ? 'h-[60vh]' : 'min-h-[60vh]'}`}>
          <div className="text-center">
            <h2 className="text-h3 text-text-primary mb-3">智能体不存在</h2>
            <Button onClick={() => navigate('/')}>返回首页</Button>
          </div>
        </div>
      </Layout>
    )
  }

  const statusInfo = getStatusLabel(agent.status)

  // ============================================================
  // 侧边栏内容（移动端与桌面端共用）
  // ============================================================
  const sidebarContent = (
    <>
      {/* 可滚动区域 */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {/* 新建协作按钮 */}
        <div className="p-3 border-b border-border-subtle">
          <button
            onClick={handleNewConversation}
                        className="ui-control w-full bg-brand-500 text-white rounded-xl px-3 text-sm font-semibold shadow-[0_8px_18px_rgba(30,58,95,0.14)] hover:bg-brand-600 flex items-center justify-center gap-1.5"

          >
            <Plus className="w-3.5 h-3.5" />
            新建协作
          </button>
        </div>

        {/* 已启用技能（紧凑展示） */}
        {skills.length > 0 && (
          <div className="p-3 border-b border-border-subtle">
            <div className="text-xs font-semibold text-text-tertiary mb-2">已启用技能</div>
            <div className="flex flex-wrap gap-1">
              {skills.map((skill) => (
                <span
                  key={skill.id}
                  className="bg-brand-50 text-brand-500 rounded px-1.5 py-0.5 text-xs font-medium"
                >
                  {skill.name}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* 历史协作 */}
        <div className="p-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold text-text-tertiary">历史协作</span>
          </div>
          <div className="space-y-0.5">
            {conversations.map((conv) => {
              const isRenaming = renamingId === conv.id
              const isDeleting = deletingConvId === conv.id
              const isActive = currentConversation?.id === conv.id
              return (
                                <div
                  key={conv.id}
                  className={`ui-nav-item w-full p-2 group ${
                    isActive
                      ? 'ui-nav-item--active text-brand-500'
                      : 'text-text-secondary hover:bg-[var(--surface-tint)]'
                  } ${isDeleting ? 'opacity-50 pointer-events-none' : ''}`}
                >

                  {isRenaming ? (
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      <input
                        autoFocus
                        type="text"
                        value={renamingValue}
                        onChange={(e) => setRenamingValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleConfirmRename(conv)
                          else if (e.key === 'Escape') handleCancelRename()
                        }}
                        className="flex-1 bg-surface border border-brand-400 rounded px-2 py-1 text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-brand-400"
                        maxLength={100}
                      />
                      <button
                        onClick={() => handleConfirmRename(conv)}
                        className="text-brand-500 hover:text-brand-600 p-1"
                        title="确认"
                        aria-label="确认重命名"
                      >
                        <Check className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={handleCancelRename}
                        className="text-text-tertiary hover:text-text-primary p-1"
                        title="取消"
                        aria-label="取消重命名"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ) : (
                    <>
                                            <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            selectConversation(conv)
                            setShowSidebar(false)
                          }}
                          aria-current={isActive ? 'page' : undefined}
                          className="flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-md"
                          title={conv.title || '新协作'}
                        >
                          <MessageSquare className="h-3.5 w-3.5 flex-shrink-0 opacity-60" aria-hidden="true" />
                          <span className="text-sm font-medium truncate flex-1">
                            {conv.title || '新协作'}
                          </span>
                        </button>
                        {/* hover 时浮现操作按钮 */}

                        <span className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              handleStartRename(conv)
                            }}
                            className="text-text-tertiary hover:text-brand-500 p-1 rounded hover:bg-elevated"
                            title="重命名"
                            aria-label="重命名协作"
                          >
                            <Edit className="w-3 h-3" />
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              handleDeleteConversation(conv)
                            }}
                            disabled={isDeleting}
                            className="text-text-tertiary hover:text-error p-1 rounded hover:bg-elevated disabled:opacity-50"
                            title="删除"
                            aria-label="删除协作"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </span>
                      </div>
                      <p className="text-xs text-text-tertiary mt-0.5 ml-5">
                        {new Date(conv.updated_at).toLocaleDateString('zh-CN')}
                      </p>
                    </>
                  )}
                </div>
              )
            })}
            {conversations.length === 0 && (
              <p className="text-xs text-text-tertiary text-center py-4">暂无协作</p>
            )}
          </div>
        </div>
      </div>

      {/* 底部操作 */}
      <div className="p-4 border-t border-border-subtle space-y-2 flex-shrink-0">
                <button
          onClick={handleClearConversation}
          className="ui-control w-full border border-border-default rounded-xl text-sm text-text-secondary hover:text-text-primary hover:bg-[var(--surface-tint)] flex items-center justify-center gap-1.5"

        >
          <Trash2 className="w-3.5 h-3.5" />
          清空协作
        </button>
        <Link
          to="/canvas"
                    className="ui-control w-full bg-brand-500 text-white rounded-xl text-sm font-semibold flex items-center justify-center gap-1.5 hover:bg-brand-600"

        >
          <Edit className="w-3.5 h-3.5" />
          编辑智能体
        </Link>
      </div>
    </>
  )

  // ============================================================
  // 主渲染
  // ============================================================
  return (
    <Layout>
      {/* AI 生成中顶部进度条 */}
      {isStreaming && <div className="top-progress-bar w-full" />}

            <div className={`${embedded ? 'h-[calc(100vh-22rem)] min-h-[480px]' : 'h-[calc(100dvh-9rem)] min-h-[36rem]'} flex`}>

        {/* 主面板：侧边栏 + 协作区 */}
                <div className="ui-card w-full flex rounded-2xl border border-border-default overflow-hidden bg-[var(--surface-raised)]">

          {/* ===== 移动端侧边栏遮罩 ===== */}
          <AnimatePresence>
            {showSidebar && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 top-16 z-30 bg-black/50 lg:hidden"
                onClick={() => setShowSidebar(false)}
              />
            )}
          </AnimatePresence>

          {/* ===== 侧边栏（桌面端固定，移动端抽屉） ===== */}
          <aside
            className={`
              ${showSidebar ? 'flex' : 'hidden'} lg:flex
              fixed top-16 bottom-0 left-0 z-40 lg:static lg:top-auto lg:bottom-auto lg:left-auto lg:z-auto
                            w-72 bg-[var(--surface-sunken)] border-r border-border-subtle flex-col flex-shrink-0

            `}
          >
            {sidebarContent}
          </aside>

          {/* ===== 协作区域 ===== */}
          <section className="flex-1 flex flex-col min-w-0 bg-canvas">
            {/* 协作标题栏 */}
                        <div className="h-16 bg-[var(--surface-raised)] border-b border-border-subtle flex items-center justify-between px-4 sm:px-6 flex-shrink-0">

              <div className="flex items-center gap-2 min-w-0">
                {/* 移动端侧边栏切换 */}
                <button
                  className="lg:hidden p-1.5 rounded-md hover:bg-elevated text-text-secondary flex-shrink-0"
                  onClick={() => setShowSidebar(!showSidebar)}
                  aria-label="切换侧边栏"
                >
                  {showSidebar ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
                </button>
                <span className="font-semibold text-text-primary truncate">{agent.name}</span>
                <span className="hidden sm:inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium bg-brand-50 text-brand-500 flex-shrink-0">
                  <Zap className="w-2.5 h-2.5" />
                  协作工作台
                </span>
                <span className={`dot ${statusInfo.dotClass}`} />
                <span className={`text-sm ${statusInfo.textClass} flex-shrink-0`}>
                  {statusInfo.label === '运行中' ? '在线' : statusInfo.label}
                </span>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={handleNewConversation}
                  className="text-sm text-brand-500 hover:text-brand-600 flex items-center gap-1 transition-colors"
                  title="新协作"
                  aria-label="新协作"
                >
                  <Plus className="w-3.5 h-3.5" aria-hidden="true" />
                  <span className="hidden sm:inline">新协作</span>
                </button>
                <Link
                  to="/canvas"
                  className="text-sm text-brand-500 hover:text-brand-600 flex items-center gap-1 transition-colors"
                  title="查看智能体画布"
                >
                  <ArrowUpRight className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">画布</span>
                </Link>
              </div>
            </div>

            {/* 消息流 */}
            <div
              ref={messagesContainerRef}
              className="flex-1 overflow-y-auto scrollbar-thin px-4 sm:px-6 py-6 space-y-5"
            >
              {/* 空状态 */}
              {allMessages.length === 0 && !isStreaming && (
                <motion.div
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3 }}
                  className="flex flex-col items-center justify-center h-full text-center"
                >
                  <div className="w-16 h-16 bg-brand-50 rounded-full flex items-center justify-center mx-auto mb-5">
                    <Sparkles className="h-8 w-8 text-brand-500" />
                  </div>
                                    <h3 className="text-2xl font-semibold tracking-[-0.04em] text-text-primary mb-2 text-balance">

                    开始与智能体协作
                  </h3>
                  <p className="text-sm text-text-secondary mb-8 max-w-md">
                    向 <span className="font-medium text-text-primary">{agent.name}</span> 提问，获取知识库中的信息。试试以下示例问题：
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-lg w-full">
                    {EXAMPLE_QUESTIONS.map((question) => (
                      <button
                        key={question}
                        onClick={() => handleExampleClick(question)}
                                                className="ui-card ui-card--interactive text-left text-sm text-text-secondary rounded-xl px-4 py-3 hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"

                      >
                        {question}
                      </button>
                    ))}
                  </div>
                </motion.div>
              )}

              {/* 消息列表（跨天处插入日期分隔条） */}
              <AnimatePresence initial={false}>
                {allMessages.map((msg, index) => {
                  const isLast = index === allMessages.length - 1
                  const showDay = dayKeys[index] !== '' && dayKeys[index] !== dayKeys[index - 1]
                  return (
                    <Fragment key={msg.id}>
                      {showDay && (
                        <div className="flex items-center justify-center">
                          <span className="text-xs text-text-tertiary bg-elevated rounded-full px-3 py-1">
                            {formatDate(msg.created_at)}
                          </span>
                        </div>
                      )}
                      <MessageBubble
                        message={msg}
                        isStreaming={isLastMessageStreaming && isLast}
                        retrievalMetadata={isLast ? retrievalMetadata : null}
                        retrievalQuery={isLast ? lastUserQuery : undefined}
                        onSatisfaction={msg.role === 'assistant' ? handleSatisfaction : undefined}
                        onRegenerate={
                          msg.role === 'assistant' && !isStreaming ? handleRegenerate : undefined
                        }
                      />
                    </Fragment>
                  )
                })}
              </AnimatePresence>

              {/* D2-S9: 检索思考指示器（流式开始前展示检索状态） */}
              {isStreaming && streamMessages.length === 0 && (
                <RetrievalThinkingIndicator metadata={retrievalMetadata} />
              )}

              {/* 流式错误提示 */}
              {streamError && !isStreaming && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="flex justify-start"
                >
                  <div className="bg-error/5 border border-error/20 text-error rounded-2xl rounded-bl-md px-4 py-3 text-sm">
                    {streamError}
                  </div>
                </motion.div>
              )}

              {/* 滚动锚点 */}
              <div ref={messagesEndRef} />
            </div>

            {/* 输入区域 */}
                        <div className="bg-[var(--surface-raised)] border-t border-border-subtle p-4 flex-shrink-0">
              <div className="input-focus flex items-end gap-2 bg-[var(--surface-sunken)] rounded-2xl border border-border-default px-3 py-2.5 transition-[border-color,box-shadow] focus-within:border-brand-500">

                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value.slice(0, MAX_INPUT_LENGTH))}
                  onKeyDown={handleKeyPress}
                  placeholder="输入消息… (Enter 发送, Shift+Enter 换行)"
                  rows={1}
                  disabled={isStreaming}
                  className="flex-1 bg-transparent resize-none text-sm outline-none placeholder:text-text-tertiary text-text-primary leading-relaxed disabled:opacity-50 max-h-32"
                />

                {/* 附件：上传文件到该智能体的知识库。
                    原为纯装饰按钮（点击无任何反应），按 UI v4 §6 战场四要求
                    「要么实现，要么移除」，此处接入真实的 /files/batch-upload。 */}
                <input
                  ref={attachInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={handleAttachFiles}
                  disabled={attachUploading}
                />
                <button
                  onClick={() => attachInputRef.current?.click()}
                  disabled={attachUploading}
                                    className="ui-control w-9 min-h-0 h-9 flex items-center justify-center rounded-xl text-text-tertiary hover:text-text-primary hover:bg-[var(--surface-tint)] flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"

                  title="上传文件到知识库"
                  aria-label="上传文件到知识库"
                  type="button"
                >
                  {attachUploading ? (
                    <RefreshCw className="w-4 h-4 animate-spin" />
                  ) : (
                    <Paperclip className="w-4 h-4" />
                  )}
                </button>

                {/* 发送 / 停止按钮 */}
                <AnimatePresence mode="wait">
                  {isStreaming ? (
                    <motion.button
                      key="stop"
                      initial={{ scale: 0.9, opacity: 0 }}
                      animate={{ scale: 1, opacity: 1 }}
                      exit={{ scale: 0.9, opacity: 0 }}
                      transition={{ duration: 0.15 }}
                      onClick={handleStopStreaming}
                                            className="ui-control bg-error text-white rounded-xl p-2 hover:bg-red-600 flex-shrink-0"

                      title="停止生成"
                      aria-label="停止生成"
                      type="button"
                    >
                      <Square className="w-4 h-4" />
                    </motion.button>
                  ) : (
                    <motion.button
                      key="send"
                      initial={{ scale: 0.9, opacity: 0 }}
                      animate={{ scale: 1, opacity: 1 }}
                      exit={{ scale: 0.9, opacity: 0 }}
                      transition={{ duration: 0.15 }}
                      onClick={() => handleSend()}
                      disabled={!input.trim()}
                                            className="ui-control bg-brand-500 text-white rounded-xl p-2 hover:bg-brand-600 flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"

                      title="发送"
                      aria-label="发送消息"
                      type="button"
                    >
                      <Send className="w-4 h-4" />
                    </motion.button>
                  )}
                </AnimatePresence>
              </div>

              {/* 输入区底部信息 */}
              <div className="flex items-center justify-between mt-2 px-1">
                <div className="flex items-center gap-3 text-xs text-text-tertiary">
                {/* 模型选择器（§4.4.2）：写入 agent.config.model，后端 P1-6.4 即时生效 */}
                <span className="relative flex items-center">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      setModelMenuOpen((v) => !v)
                    }}
                    disabled={switchingModel}
                    className="flex items-center gap-1 rounded-md px-1 py-0.5 -mx-1 hover:text-text-primary hover:bg-elevated transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    title="切换对话模型"
                    aria-haspopup="listbox"
                    aria-expanded={modelMenuOpen}
                    aria-label="切换对话模型"
                  >
                    <Zap className="w-3 h-3" />
                    模型{' '}
                    <span className="font-mono text-xs">{switchingModel ? '切换中…' : currentModelLabel}</span>
                    <ChevronDown
                      className={`w-3 h-3 transition-transform ${modelMenuOpen ? 'rotate-180' : ''}`}
                      aria-hidden="true"
                    />
                  </button>
                  <AnimatePresence>
                    {modelMenuOpen && (
                      <motion.div
                        initial={{ opacity: 0, y: 4, scale: 0.98 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: 4, scale: 0.98 }}
                        transition={{ duration: 0.15 }}
                        role="listbox"
                        aria-label="模型列表"
                        className="absolute bottom-full left-0 mb-2 z-20 w-64 bg-surface border border-border-default rounded-xl shadow-lg overflow-hidden"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {MODEL_OPTIONS.map((option) => {
                          const isActive = option.id === rawModelId
                          return (
                            <button
                              key={option.id}
                              type="button"
                              role="option"
                              aria-selected={isActive}
                              onClick={() => handleModelSelect(option.id)}
                              className={`w-full text-left px-3 py-2.5 flex items-start gap-2 transition-colors hover:bg-elevated ${
                                isActive ? 'bg-brand-50/60' : ''
                              }`}
                            >
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-1.5">
                                  <span className="text-sm font-medium text-text-primary">{option.label}</span>
                                  <span
                                    className={`text-[10px] font-semibold rounded px-1 py-px ${option.tagClass}`}
                                  >
                                    {option.tag}
                                  </span>
                                </div>
                                <p className="text-xs text-text-tertiary mt-0.5">{option.description}</p>
                              </div>
                              {isActive && <Check className="w-3.5 h-3.5 text-brand-500 flex-shrink-0 mt-1" />}
                            </button>
                          )
                        })}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </span>
                {modelNotice && (
                  <span
                    role="status"
                    aria-live="polite"
                    className={modelNotice.tone === 'ok' ? 'text-success' : 'text-error'}
                  >
                    {modelNotice.text}
                  </span>
                )}
                {agent.knowledge_count > 0 && (
                  <span className="flex items-center gap-1">
                    <Database className="w-3 h-3" />
                    知识库已连接
                  </span>
                )}
                <span className="flex items-center gap-1">
                  <MessageSquare className="w-3 h-3" />
                  {allMessages.length} 条消息
                </span>
                {attachNotice && (
                  <span
                    role="status"
                    aria-live="polite"
                    className={`flex items-center gap-1 ${
                      attachNotice.tone === 'ok' ? 'text-success' : 'text-error'
                    }`}
                  >
                    <Paperclip className="w-3 h-3" aria-hidden="true" />
                    {attachNotice.text}
                  </span>
                )}
              </div>
                <span className="text-xs text-text-tertiary font-mono">
                  {input.length} / {MAX_INPUT_LENGTH}
                </span>
              </div>
            </div>
          </section>

          {/* ===== 右栏：Agent 信息 & 检索来源面板（对标 prototype） ===== */}
          <ChatAgentPanel
            agent={agent}
            loopStats={loopStats}
            retrievalMetadata={retrievalMetadata}
            isStreaming={isStreaming}
            statusDotClass={statusInfo.dotClass}
            statusLabel={statusInfo.label}
          />
        </div>
      </div>
      {confirmDialog.dialog}
    </Layout>
  )
}
