/**
 * TraceExplorer — 全链路 Trace 观测（蓝图 10，近黑暗调）。
 *
 * 回答的问题：每一步发生了什么。
 *
 * 主导区 = 执行时序瀑布流（细条 + 竖向发丝网格）
 * 次级区 = Span 详情（右栏）+ 合规校验 3 行 + 不可篡改审计留痕
 *
 * 数据全部来自后端既有接口，无任何前端造数：
 * - GET /conversations          一次会话执行 = 一条 Trace
 * - GET /conversations/{id}     消息序列 = Span 序列（含 token_count / model_used / sources）
 * - GET /audit-logs             该 Trace 关联的审计留痕
 * - GET /audit-logs/verify      HMAC 链式防篡改校验
 *
 * 耗时口径：Span 的起止时间由消息落库时间戳推算（上一条消息 → 本条消息），
 * 无独立计时的子步骤（如知识检索）按标记点呈现，不臆造耗时。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, ShieldCheck, ShieldAlert } from 'lucide-react'
import { toast } from 'sonner'
import Layout from '@/components/Layout'
import {
  NIGHT,
  PrimaryButton,
  TextLink,
  SectionTitle,
  StatusLabel,
  type StatusTone,
} from '@/components/editorial'
import apiClient from '@/api/client'
import { getConversations } from '@/api/conversations'
import type { Conversation, Source } from '@/types'
import {
  getAuditLogs,
  verifyAuditChain,
  exportAuditLogs,
  type AuditLog,
  type AuditChainVerifyResult,
} from '@/api/auditLogs'

/** 每千 token 折算单价（元），与后端 services/metrics_service.py 同口径。 */
const COST_PER_1K_TOKENS_YUAN = 0.008

/** Trace 中的一条消息 = 一个 Span。 */
interface TraceMessage {
  id: string
  role: string
  content: string
  sources: Source[]
  satisfaction: string | null
  token_count: number
  model_used: string | null
  created_at: string
}

/** 一条 Trace 的完整明细。 */
interface TraceDetail {
  id: string
  agent_id: string
  title: string
  created_at: string
  updated_at: string
  messages: TraceMessage[]
}

/** 瀑布流中的一个 Span 节点。 */
interface SpanNode {
  id: string
  parentId: string | null
  /** 主导区行内标签 */
  label: string
  /** 详情区显示的契约名 */
  contract: string
  startMs: number
  durationMs: number
  tokens: number
  model: string | null
  tone: StatusTone
  message: TraceMessage
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return '未知错误'
}

/** 后端 Source[] → 归一化（丢弃空字段，缺失时给安全默认）。 */
function normalizeSources(value: unknown): Source[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const raw = item as { content?: unknown; source?: unknown; file_type?: unknown; distance?: unknown }
    return [
      {
        content: typeof raw.content === 'string' ? raw.content : '',
        source: typeof raw.source === 'string' ? raw.source : '未命名来源',
        file_type: typeof raw.file_type === 'string' ? raw.file_type : undefined,
        distance: typeof raw.distance === 'number' ? raw.distance : null,
      },
    ]
  })
}

/** 未知负载 → TraceDetail（逐字段收敛，不做整体断言）。 */
function normalizeTraceDetail(value: unknown): TraceDetail {
  const raw = (typeof value === 'object' && value !== null ? value : {}) as {
    id?: unknown
    agent_id?: unknown
    title?: unknown
    created_at?: unknown
    updated_at?: unknown
    messages?: unknown
  }
  const messages: TraceMessage[] = Array.isArray(raw.messages)
    ? raw.messages.flatMap((m) => {
        if (typeof m !== 'object' || m === null) return []
        const msg = m as {
          id?: unknown
          role?: unknown
          content?: unknown
          sources?: unknown
          satisfaction?: unknown
          token_count?: unknown
          model_used?: unknown
          created_at?: unknown
        }
        if (typeof msg.id !== 'string' || typeof msg.created_at !== 'string') return []
        return [
          {
            id: msg.id,
            role: typeof msg.role === 'string' ? msg.role : 'unknown',
            content: typeof msg.content === 'string' ? msg.content : '',
            sources: normalizeSources(msg.sources),
            satisfaction: typeof msg.satisfaction === 'string' ? msg.satisfaction : null,
            token_count: typeof msg.token_count === 'number' ? msg.token_count : 0,
            model_used: typeof msg.model_used === 'string' ? msg.model_used : null,
            created_at: msg.created_at,
          },
        ]
      })
    : []
  return {
    id: typeof raw.id === 'string' ? raw.id : '',
    agent_id: typeof raw.agent_id === 'string' ? raw.agent_id : '',
    title: typeof raw.title === 'string' ? raw.title : '未命名执行',
    created_at: typeof raw.created_at === 'string' ? raw.created_at : new Date().toISOString(),
    updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : new Date().toISOString(),
    messages,
  }
}

async function fetchTraceDetail(conversationId: string): Promise<TraceDetail> {
  const resp = await apiClient.get(`/conversations/${conversationId}`)
  return normalizeTraceDetail(resp.data.data)
}

/** 毫秒 → 1 位小数的秒字符串。 */
function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

export default function TraceExplorer() {
  const [traces, setTraces] = useState<Conversation[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [selectedSpanId, setSelectedSpanId] = useState('')
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([])
  const [chain, setChain] = useState<AuditChainVerifyResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const list = await getConversations().catch(() => [] as Conversation[])
        if (cancelled) return
        setTraces(list || [])
        setSelectedId((prev) => prev || list?.[0]?.id || '')
      } catch (err: unknown) {
        toast.error('获取执行 Trace 列表失败：' + errorMessage(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  /** 选中 Trace → 拉取 Span 序列与审计留痕。 */
  const loadDetail = useCallback(async (conversationId: string) => {
    if (!conversationId) return
    setDetailLoading(true)
    try {
      const [next, logs] = await Promise.all([
        fetchTraceDetail(conversationId).catch(() => null),
        getAuditLogs({ resource_type: 'conversation', keyword: conversationId, limit: 20 }).catch(() => null),
      ])
      setDetail(next)
      setAuditLogs(logs?.logs ?? [])
      setChain(null)
    } catch (err: unknown) {
      toast.error('加载 Trace 明细失败：' + errorMessage(err))
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedId) return
    void loadDetail(selectedId)
  }, [selectedId, loadDetail])

  /**
   * 把消息序列折算成 Span 树：
   * - 入站消息：时间点标记（无独立计时）
   * - 模型生成：上一条消息 → 本条消息的实测间隔
   * - 知识检索：挂在模型生成下方的引用命中标记
   */
  const { spans, totalMs, tokensTotal, ttftMs } = useMemo(() => {
    const messages = detail?.messages ?? []
    if (messages.length === 0) {
      return { spans: [] as SpanNode[], totalMs: 0, tokensTotal: 0, ttftMs: null as number | null }
    }
    const origin = new Date(messages[0].created_at).getTime()
    const nodes: SpanNode[] = []
    let prevTime = origin
    let ttft: number | null = null
    let tokens = 0

    messages.forEach((message, index) => {
      const at = new Date(message.created_at).getTime()
      if (message.role === 'assistant') {
        const latency = Math.max(at - prevTime, 0)
        if (ttft === null) ttft = latency
        tokens += message.token_count
        nodes.push({
          id: message.id,
          parentId: null,
          label: '模型生成',
          contract: `ModelGeneration#${index + 1}`,
          startMs: Math.max(prevTime - origin, 0),
          durationMs: latency,
          tokens: message.token_count,
          model: message.model_used,
          tone: latency > 30_000 ? 'warning' : 'success',
          message,
        })
        if (message.sources.length > 0) {
          nodes.push({
            id: `${message.id}:retrieval`,
            parentId: message.id,
            label: `知识检索 · ${message.sources.length} 处引用`,
            contract: 'KnowledgeRetrieval',
            startMs: Math.max(prevTime - origin, 0),
            durationMs: 0,
            tokens: 0,
            model: null,
            tone: 'muted',
            message,
          })
        }
      } else if (message.role === 'user') {
        nodes.push({
          id: message.id,
          parentId: null,
          label: '入站消息',
          contract: `Inbound#${index + 1}`,
          startMs: Math.max(at - origin, 0),
          durationMs: 0,
          tokens: 0,
          model: null,
          tone: 'muted',
          message,
        })
      }
      prevTime = at
    })

    const lastTime = new Date(messages[messages.length - 1].created_at).getTime()
    return { spans: nodes, totalMs: Math.max(lastTime - origin, 0), tokensTotal: tokens, ttftMs: ttft }
  }, [detail])

  const selectedSpan = spans.find((s) => s.id === selectedSpanId) ?? spans[0] ?? null
  const rootSpans = spans.filter((s) => s.parentId === null)
  const childCount = spans.length - rootSpans.length
  const traceId = selectedId ? `TRC-${selectedId.slice(0, 8).toUpperCase()}` : '—'
  const costYuan = (tokensTotal / 1000) * COST_PER_1K_TOKENS_YUAN

  const handleVerifyChain = async () => {
    try {
      const res = await verifyAuditChain(0)
      setChain(res)
      if (res.valid) toast.success(`审计链完整，已校验 ${res.checked} 条`)
      else toast.error(`审计链在 ${res.broken_at ?? '未知位置'} 断裂`)
    } catch (err: unknown) {
      toast.error('审计链校验失败：' + errorMessage(err))
    }
  }

  const handleExport = async () => {
    if (!selectedId) return
    setExporting(true)
    try {
      await exportAuditLogs({ resource_type: 'conversation', keyword: selectedId })
      toast.success('审计包已导出')
    } catch (err: unknown) {
      toast.error('导出失败：' + errorMessage(err))
    } finally {
      setExporting(false)
    }
  }

  /** 百分比定位（时间轴 0 → totalMs）。 */
  const percent = (ms: number) => (totalMs > 0 ? Math.min((ms / totalMs) * 100, 100) : 0)

  const complianceRows: Array<{ label: string; tone: StatusTone; fact: string }> = [
    {
      label: '引用可溯源',
      tone: rootSpans.some((s) => s.message.sources.length > 0) ? 'success' : 'warning',
      fact: `${rootSpans.reduce((sum, s) => sum + s.message.sources.length, 0)} 处知识引用`,
    },
    {
      label: '满意度回执',
      tone: rootSpans.some((s) => s.message.satisfaction?.startsWith('satisfied'))
        ? 'success'
        : 'muted',
      fact: rootSpans.find((s) => s.message.satisfaction)?.message.satisfaction ?? '未回执',
    },
    {
      label: '审计留痕',
      tone: auditLogs.length > 0 ? 'success' : 'warning',
      fact: `${auditLogs.length} 条关联审计记录`,
    },
  ]

  const axisTicks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
    left: `${ratio * 100}%`,
    label: seconds(totalMs * ratio),
  }))

  return (
    <Layout fluid>
      <div className="flex h-full min-h-0 flex-col" style={{ background: NIGHT.canvas }}>
        {/* ============ 顶栏 ============ */}
        <div
          className="flex flex-wrap items-center justify-between gap-4 border-b px-8 py-5"
          style={{ borderColor: NIGHT.hair, background: NIGHT.canvas }}
        >
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <h1 className="text-[30px] font-bold leading-[38px] tracking-tight" style={{ color: NIGHT.text }}>
                Trace 观测
              </h1>
              <span
                className="rounded-[6px] px-2 py-0.5 font-mono text-[12px]"
                style={{ background: NIGHT.panel, border: `1px solid ${NIGHT.hair}`, color: NIGHT.muted }}
              >
                {detailLoading ? 'LOADING' : rootSpans.length > 0 ? 'TRACE_CAPTURED' : 'NO_SPAN'}
              </span>
            </div>
            <p className="mt-1 text-[13px] leading-5" style={{ color: NIGHT.muted }}>
              {detail
                ? `${detail.title} · ${new Date(detail.created_at).toLocaleString('zh-CN', { hour12: false })} · ${seconds(totalMs)} · ${tokensTotal.toLocaleString('en-US')} tokens · ¥${costYuan.toFixed(4)}`
                : '选择左侧一条执行 Trace 查看完整调用链。'}
            </p>
          </div>
          <PrimaryButton onClick={handleExport} disabled={!selectedId || exporting}>
            <Download className="h-4 w-4" aria-hidden="true" />
            {exporting ? '导出中…' : '导出审计包'}
          </PrimaryButton>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* ============ 左：Trace 列表 ============ */}
          <nav
            className="hidden w-[260px] shrink-0 flex-col overflow-y-auto border-r lg:flex"
            style={{ borderColor: NIGHT.hair, background: NIGHT.panel }}
            aria-label="执行 Trace 列表"
          >
            <div className="px-5 py-4">
              <SectionTitle title="执行 Trace" meta={`${traces.length}`} scope="night" />
            </div>
            {loading ? (
              <p className="px-5 text-[13px]" style={{ color: NIGHT.muted }}>
                正在加载…
              </p>
            ) : traces.length === 0 ? (
              <p className="px-5 text-[13px] leading-5" style={{ color: NIGHT.muted }}>
                尚无执行记录。与任一数字员工的会话会产生一条 Trace。
              </p>
            ) : (
              <div className="flex-1">
                {traces.map((t) => {
                  const active = t.id === selectedId
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => setSelectedId(t.id)}
                      className="flex w-full flex-col items-start gap-1 border-l-2 px-5 py-3 text-left transition-colors"
                      style={{
                        borderColor: NIGHT.hair,
                        borderLeftColor: active ? NIGHT.accent : 'transparent',
                        background: active ? NIGHT.canvas : undefined,
                      }}
                    >
                      <span
                        className="w-full truncate text-[14px] font-medium"
                        style={{ color: active ? NIGHT.text : NIGHT.muted }}
                      >
                        {t.title || '未命名执行'}
                      </span>
                      <span className="font-mono text-[12px]" style={{ color: NIGHT.subtle }}>
                        {new Date(t.updated_at).toLocaleString('zh-CN', { hour12: false })}
                      </span>
                    </button>
                  )
                })}
              </div>
            )}
          </nav>

          {/* ============ 中：瀑布流 ============ */}
          <section className="flex min-w-0 flex-1 flex-col overflow-y-auto border-r" style={{ borderColor: NIGHT.hair }}>
            <div
              className="flex items-center justify-between gap-6 border-b px-8 py-4"
              style={{ borderColor: NIGHT.hair, background: NIGHT.panel }}
            >
              <div>
                <h2 className="text-[17px] font-semibold leading-6" style={{ color: NIGHT.text }}>
                  执行时序瀑布流
                </h2>
                <p className="mt-0.5 text-[13px]" style={{ color: NIGHT.muted }}>
                  {rootSpans.length} 个根节点 · {childCount} 个嵌套分支
                </p>
              </div>
              <div className="hidden items-center gap-6 text-[12px] md:flex" style={{ color: NIGHT.subtle }}>
                <span className="flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: NIGHT.hair }} />
                  实测耗时
                </span>
                <span className="flex items-center gap-1.5" style={{ color: NIGHT.muted }}>
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: NIGHT.accent }} />
                  选中节点
                </span>
              </div>
            </div>

            <div className="flex-1 px-8 py-6">
              {rootSpans.length === 0 ? (
                <p className="text-[13px]" style={{ color: NIGHT.muted }}>
                  该 Trace 暂无可展示的 Span。
                </p>
              ) : (
                <>
                  {/* 时间轴 */}
                  <div
                    className="relative mb-4 h-6 border-b font-mono text-[12px]"
                    style={{ borderColor: NIGHT.hair, color: NIGHT.subtle }}
                  >
                    {axisTicks.map((tick) => (
                      <span
                        key={tick.left}
                        className="absolute"
                        style={{ left: tick.left, transform: tick.left === '0%' ? undefined : 'translateX(-50%)' }}
                      >
                        {tick.label}
                      </span>
                    ))}
                  </div>

                  <div className="relative space-y-3 pb-8">
                    {/* 竖向发丝网格 */}
                    <div className="pointer-events-none absolute inset-0 flex justify-between" aria-hidden="true">
                      {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
                        <span key={ratio} className="h-full w-px" style={{ background: NIGHT.hair, opacity: 0.5 }} />
                      ))}
                    </div>

                    {spans.map((span) => {
                      const isChild = span.parentId !== null
                      const active = selectedSpan?.id === span.id
                      const width = span.durationMs > 0 ? Math.max(percent(span.durationMs), 0.8) : 0
                      return (
                        <button
                          key={span.id}
                          type="button"
                          onClick={() => setSelectedSpanId(span.id)}
                          className="relative flex h-9 w-full items-center text-left"
                          style={{ paddingLeft: isChild ? 32 : 0 }}
                        >
                          <span
                            className="absolute flex items-center justify-between rounded-[6px] px-2"
                            style={{
                              left: `${percent(span.startMs)}%`,
                              width: span.durationMs > 0 ? `${width}%` : 6,
                              minWidth: 6,
                              height: 28,
                              background: active ? NIGHT.canvas : NIGHT.inset,
                              border: `1px solid ${active ? NIGHT.accent : NIGHT.hair}`,
                            }}
                          >
                            {span.durationMs > 0 && (
                              <span
                                className="truncate font-mono text-[12px]"
                                style={{ color: active ? NIGHT.accent : NIGHT.muted }}
                              >
                                {seconds(span.durationMs)}
                              </span>
                            )}
                          </span>
                          <span
                            className="absolute truncate text-[13px]"
                            style={{
                              left: `calc(${percent(span.startMs)}% + ${span.durationMs > 0 ? `${width}%` : '6px'} + 10px)`,
                              maxWidth: '46%',
                              color: active ? NIGHT.text : NIGHT.muted,
                              fontWeight: active ? 600 : 400,
                            }}
                          >
                            {span.label}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </>
              )}
            </div>

            {/* ============ 底部指标 ============ */}
            <div
              className="grid shrink-0 grid-cols-2 divide-x md:grid-cols-4"
              style={{ borderColor: NIGHT.hair, background: NIGHT.panel }}
            >
              {[
                { label: '端到端耗时', value: seconds(totalMs) },
                { label: 'TTFT 首字延迟', value: ttftMs === null ? '—' : seconds(ttftMs) },
                { label: '提示词 Token 消耗', value: tokensTotal.toLocaleString('en-US') },
                { label: '折算成本', value: `¥${costYuan.toFixed(4)}` },
              ].map((metric) => (
                <div key={metric.label} className="p-5">
                  <div className="text-[12px]" style={{ color: NIGHT.subtle }}>
                    {metric.label}
                  </div>
                  <div className="mt-1 text-[22px] font-semibold leading-tight" style={{ color: NIGHT.text }}>
                    {metric.value}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* ============ 右：Span 详情 ============ */}
          <aside
            className="hidden w-[300px] shrink-0 flex-col overflow-y-auto xl:flex"
            style={{ background: NIGHT.panel }}
          >
            <div className="flex-1 space-y-6 p-5">
              <div className="border-b pb-4" style={{ borderColor: NIGHT.hair }}>
                <div className="flex items-center justify-between">
                  <h3 className="text-[17px] font-semibold leading-6" style={{ color: NIGHT.text }}>
                    Span 详情
                  </h3>
                  <span className="font-mono text-[12px]" style={{ color: NIGHT.subtle }}>
                    {selectedSpan ? `#${selectedSpan.id.slice(0, 8)}` : '—'}
                  </span>
                </div>
                <div className="mt-2 text-[14px] font-medium" style={{ color: NIGHT.text }}>
                  {selectedSpan ? `${selectedSpan.label} (${selectedSpan.contract})` : '未选中 Span'}
                </div>
              </div>

              {selectedSpan && (
                <>
                  <div className="space-y-1.5">
                    <span className="text-[12px]" style={{ color: NIGHT.subtle }}>
                      入参 Payload
                    </span>
                    <div
                      className="break-all rounded-[6px] p-2.5 font-mono text-[12px] leading-5"
                      style={{ background: NIGHT.canvas, border: `1px solid ${NIGHT.hair}`, color: NIGHT.muted }}
                    >
                      {selectedSpan.message.content.slice(0, 240) || '（空内容）'}
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <span className="text-[12px]" style={{ color: NIGHT.subtle }}>
                      Token / 模型
                    </span>
                    <div
                      className="rounded-[6px] p-2.5 font-mono text-[12px] leading-5"
                      style={{ background: NIGHT.canvas, border: `1px solid ${NIGHT.hair}`, color: NIGHT.muted }}
                    >
                      {selectedSpan.tokens.toLocaleString('en-US')} tk · {selectedSpan.model ?? '未记录模型'}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-[12px]" style={{ color: NIGHT.subtle }}>
                        命中引用
                      </span>
                      <span className="font-mono text-[12px]" style={{ color: NIGHT.subtle }}>
                        {selectedSpan.message.sources.length} 处命中
                      </span>
                    </div>
                    {selectedSpan.message.sources.length === 0 ? (
                      <p className="text-[12px]" style={{ color: NIGHT.muted }}>
                        该 Span 未挂载知识引用。
                      </p>
                    ) : (
                      selectedSpan.message.sources.map((source, index) => (
                        <div
                          key={`${source.source}-${index}`}
                          className="rounded-[6px] p-2.5"
                          style={{ background: NIGHT.canvas, border: `1px solid ${NIGHT.hair}` }}
                        >
                          <div className="truncate text-[13px] leading-5" style={{ color: NIGHT.text }}>
                            {source.source}
                          </div>
                          <div
                            className="mt-1.5 flex items-center justify-between font-mono text-[12px]"
                            style={{ color: NIGHT.subtle }}
                          >
                            <span>REF-{String(index + 1).padStart(2, '0')}</span>
                            <span>
                              {typeof source.distance === 'number'
                                ? `相似度 ${source.distance.toFixed(3)}`
                                : '未记录相似度'}
                            </span>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </>
              )}

              <div className="space-y-2 border-t pt-4" style={{ borderColor: NIGHT.hair }}>
                <span className="text-[12px]" style={{ color: NIGHT.subtle }}>
                  合规校验
                </span>
                {complianceRows.map((row) => (
                  <StatusLabel key={row.label} tone={row.tone} scope="night">
                    {row.label} · {row.fact}
                  </StatusLabel>
                ))}
              </div>

              <div className="space-y-2 border-t pt-4" style={{ borderColor: NIGHT.hair }}>
                <div className="flex items-center justify-between">
                  <span className="text-[12px]" style={{ color: NIGHT.subtle }}>
                    不可篡改审计日志
                  </span>
                  <TextLink
                    onClick={handleVerifyChain}
                    title="校验 HMAC-SHA256 链式签名是否断裂"
                  >
                    校验审计链
                  </TextLink>
                </div>
                {chain && (
                  <div
                    className="flex items-center gap-2 rounded-[6px] p-2.5 text-[12px]"
                    style={{ background: NIGHT.canvas, border: `1px solid ${NIGHT.hair}` }}
                  >
                    {chain.valid ? (
                      <ShieldCheck className="h-3.5 w-3.5" style={{ color: NIGHT.success }} aria-hidden="true" />
                    ) : (
                      <ShieldAlert className="h-3.5 w-3.5" style={{ color: NIGHT.danger }} aria-hidden="true" />
                    )}
                    <span style={{ color: NIGHT.muted }}>
                      {chain.valid ? `链完整 · 已校验 ${chain.checked} 条` : `链断裂 · ${chain.message}`}
                    </span>
                  </div>
                )}
                {auditLogs.length === 0 ? (
                  <p className="text-[12px]" style={{ color: NIGHT.muted }}>
                    该 Trace 暂无关联审计记录。
                  </p>
                ) : (
                  <div className="space-y-3">
                    {auditLogs.slice(0, 5).map((log) => (
                      <div key={log.id} className="text-[12px] leading-5" style={{ color: NIGHT.subtle }}>
                        <span style={{ color: NIGHT.muted }}>
                          {new Date(log.created_at).toLocaleTimeString('zh-CN', { hour12: false })}
                        </span>{' '}
                        · {log.action}
                        {log.resource_type ? ` · ${log.resource_type}` : ''}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div
              className="flex items-center justify-between border-t px-5 py-4 text-[12px]"
              style={{ borderColor: NIGHT.hair, color: NIGHT.subtle }}
            >
              <span>状态: {rootSpans.length > 0 ? '追踪完整' : '无 Span 数据'}</span>
              <span className="font-mono">TraceID: {traceId}</span>
            </div>
          </aside>
        </div>
      </div>
    </Layout>
  )
}
