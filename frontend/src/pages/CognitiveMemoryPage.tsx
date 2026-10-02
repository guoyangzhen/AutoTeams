/**
 * CognitiveMemoryPage — 认知记忆中枢（AutoTeams 5.0 战役 2）。
 *
 * 三段式：左「情景剧集」列表 / 中「时间序列回放 + 因果链条」/ 右「经验基因卡 + 混合检索」。
 * 全部数据来自 backend/app/api/cognitive_memory.py（/api/v1/memory），不做任何前端编造。
 */
import { useCallback, useEffect, useState } from 'react'
import { BrainCircuit, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import Layout from '@/components/Layout'
import {
  PAPER,
  SectionTitle,
  StatusLabel,
  Dot,
  PrimaryButton,
  SecondaryButton,
  Field,
  HairlineBox,
  EmptyState,
  type StatusTone,
} from '@/components/editorial'
import {
  listEpisodicTraces,
  listProceduralGenes,
  replayCausalChain,
  queryHybridMemory,
  consolidateProceduralMemory,
  getMemoryGraph,
  type EpisodicTrace,
  type CausalChainReplay,
  type ProceduralGene,
  type HybridMemoryResponse,
  type MemoryGraph,
  type CausalStep,
  type MemoryHit,
  type RetrievalChannel,
} from '@/api/cognitiveMemory'

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return '未知错误'
}

// ---------------------------------------------------------------------------
// 归一化：未知负载 → 契约类型（逐字段收敛，不做整体断言）
// ---------------------------------------------------------------------------

/** 任意负载 → 可索引对象（后续一律逐字段读取，不做整体断言）。 */
function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null) return {}
  return value as Record<string, unknown>
}

/** 未知负载 → string | null。 */
function optString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

/** 未知负载 → 有限数值（非数一律 0）。 */
function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

/** 未知负载 → 字符串数组（丢弃非字符串元素）。 */
function strArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => (typeof item === 'string' ? [item] : []))
}

/** 未知负载 → CausalStep[]。 */
function normalizeSteps(value: unknown): CausalStep[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item, index) => {
    if (typeof item !== 'object' || item === null) return []
    const raw = item as { step?: unknown; action?: unknown; actor?: unknown; note?: unknown }
    return [
      {
        step: num(raw.step),
        action: optString(raw.action) ?? `第 ${index + 1} 步`,
        actor: optString(raw.actor) ?? undefined,
        note: optString(raw.note) ?? undefined,
      },
    ]
  })
}

/** 未知负载 → EpisodicTrace[]（丢弃无 id 的行）。 */
function normalizeTraces(value: unknown): EpisodicTrace[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const raw = item as Record<string, unknown>
    if (typeof raw.id !== 'string') return []
    return [
      {
        id: raw.id,
        enterprise_id: optString(raw.enterprise_id) ?? '',
        badge: optString(raw.badge),
        caused_by_event_id: optString(raw.caused_by_event_id),
        task_summary: optString(raw.task_summary) ?? '未命名情景',
        causal_chain: normalizeSteps(raw.causal_chain),
        reflection_notes: optString(raw.reflection_notes),
        outcome_score: num(raw.outcome_score),
        context_tags: strArray(raw.context_tags),
        steps_detail: raw.steps_detail ?? null,
        created_at: optString(raw.created_at) ?? '',
      },
    ]
  })
}

/** 未知负载 → CausalChainNode[]。 */
function normalizeChainNodes(value: unknown): CausalChainReplay['nodes'] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const raw = item as Record<string, unknown>
    if (typeof raw.trace_id !== 'string') return []
    const direction = raw.direction
    return [
      {
        trace_id: raw.trace_id,
        badge: optString(raw.badge),
        task_summary: optString(raw.task_summary) ?? '未命名情景',
        outcome_score: num(raw.outcome_score),
        depth: num(raw.depth),
        direction: direction === 'ancestor' || direction === 'descendant' || direction === 'self' ? direction : 'self',
        created_at: optString(raw.created_at) ?? '',
      },
    ]
  })
}

/** 未知负载 → CausalChainReplay。 */
function normalizeReplay(value: unknown): CausalChainReplay {
  const raw = asRecord(value)
  return {
    root_trace_id: optString(raw.root_trace_id) ?? '',
    nodes: normalizeChainNodes(raw.nodes),
    max_depth_reached: num(raw.max_depth_reached),
    truncated: raw.truncated === true,
  }
}

/** 未知负载 → ProceduralGene[]。 */
function normalizeGenes(value: unknown): ProceduralGene[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const raw = item as Record<string, unknown>
    if (typeof raw.gene_id !== 'string') return []
    return [
      {
        gene_id: raw.gene_id,
        enterprise_id: optString(raw.enterprise_id) ?? '',
        badge: optString(raw.badge),
        trigger_pattern: optString(raw.trigger_pattern) ?? '未命名触发模式',
        successful_sop_patch: optString(raw.successful_sop_patch) ?? '',
        confidence_rating: num(raw.confidence_rating),
        support_count: num(raw.support_count),
        source_trace_ids: strArray(raw.source_trace_ids),
        created_at: optString(raw.created_at) ?? '',
        updated_at: optString(raw.updated_at) ?? '',
      },
    ]
  })
}

const CHANNELS: RetrievalChannel[] = ['dense', 'sparse', 'graph']

/** 未知负载 → MemoryHit[]。 */
function normalizeHits(value: unknown): MemoryHit[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const raw = item as Record<string, unknown>
    if (typeof raw.trace_id !== 'string') return []
    const channelList = Array.isArray(raw.channels) ? (raw.channels as unknown[]) : []
    const channels = channelList.flatMap((c): RetrievalChannel[] =>
      CHANNELS.includes(c as RetrievalChannel) ? [c as RetrievalChannel] : [],
    )
    const distance = raw.causal_distance
    return [
      {
        trace_id: raw.trace_id,
        badge: optString(raw.badge),
        task_summary: optString(raw.task_summary) ?? '未命名情景',
        outcome_score: num(raw.outcome_score),
        created_at: optString(raw.created_at) ?? '',
        score: num(raw.score),
        channels,
        causal_distance: typeof distance === 'number' && Number.isFinite(distance) ? distance : null,
      },
    ]
  })
}

/** 未知负载 → HybridMemoryResponse。 */
function normalizeQueryResponse(value: unknown): HybridMemoryResponse {
  const raw = asRecord(value)
  const counts = asRecord(raw.channel_counts)
  return {
    query: optString(raw.query) ?? '',
    hits: normalizeHits(raw.hits),
    channel_counts: {
      dense: num(counts.dense),
      sparse: num(counts.sparse),
      graph: num(counts.graph),
    },
    fusion: 'rrf',
  }
}

/** 未知负载 → MemoryGraph（只保留统计口径需要的字段）。 */
function normalizeGraph(value: unknown): MemoryGraph | null {
  if (typeof value !== 'object' || value === null) return null
  const stats = asRecord(asRecord(value).stats)
  return {
    nodes: [],
    edges: [],
    stats: {
      trace_count: num(stats.trace_count),
      gene_count: num(stats.gene_count),
      edge_count: num(stats.edge_count),
    },
  }
}

// ---------------------------------------------------------------------------
// 展示用小工具
// ---------------------------------------------------------------------------

/** 结果分 → 状态点色调。 */
function scoreTone(score: number): StatusTone {
  if (score >= 0.8) return 'success'
  if (score >= 0.5) return 'warning'
  return 'danger'
}

/** 因果方向 → 中文 + 色调。 */
function directionLabel(direction: CausalChainReplay['nodes'][number]['direction']): { text: string; tone: StatusTone } {
  if (direction === 'ancestor') return { text: '祖先', tone: 'muted' }
  if (direction === 'descendant') return { text: '后代', tone: 'subtle' }
  return { text: '当前', tone: 'success' }
}

const CHANNEL_LABEL: Record<RetrievalChannel, string> = {
  dense: '稠密',
  sparse: '稀疏',
  graph: '图',
}

/** ISO 时间 → 月-日 时:分（无法解析时回落到原文）。 */
function shortTime(iso: string): string {
  if (!iso) return '—'
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`
}

/** 定宽等宽小标签。 */
function Tag({ children, tone = 'muted' }: { children: React.ReactNode; tone?: StatusTone }) {
  return (
    <span
      className="inline-flex items-center rounded-[4px] px-1.5 py-0.5 font-mono text-[11px] leading-4"
      style={{ background: PAPER.alt, color: PAPER.muted, border: `1px solid ${PAPER.hair}` }}
    >
      <span className="mr-1"><Dot tone={tone} /></span>
      {children}
    </span>
  )
}

export default function CognitiveMemoryPage() {
  const [traces, setTraces] = useState<EpisodicTrace[]>([])
  const [genes, setGenes] = useState<ProceduralGene[]>([])
  const [graph, setGraph] = useState<MemoryGraph | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [replay, setReplay] = useState<CausalChainReplay | null>(null)
  const [query, setQuery] = useState('')
  const [searchResult, setSearchResult] = useState<HybridMemoryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [replayLoading, setReplayLoading] = useState(false)
  const [consolidating, setConsolidating] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const [tracePayload, genePayload, graphPayload] = await Promise.all([
          listEpisodicTraces().catch(() => [] as unknown),
          listProceduralGenes().catch(() => [] as unknown),
          getMemoryGraph().catch(() => null),
        ])
        if (cancelled) return
        const nextTraces = normalizeTraces(tracePayload)
        setTraces(nextTraces)
        setGenes(normalizeGenes(genePayload))
        setGraph(normalizeGraph(graphPayload))
        setSelectedId((prev) => prev || nextTraces[0]?.id || '')
      } catch (err: unknown) {
        toast.error('加载认知记忆失败：' + errorMessage(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  /** 选中情景 → 回放因果链。 */
  const loadReplay = useCallback(async (traceId: string) => {
    if (!traceId) return
    setReplayLoading(true)
    try {
      const payload = await replayCausalChain(traceId)
      setReplay(normalizeReplay(payload))
    } catch (err: unknown) {
      toast.error('回放因果链失败：' + errorMessage(err))
    } finally {
      setReplayLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedId) return
    void loadReplay(selectedId)
  }, [selectedId, loadReplay])

  const selected = traces.find((t) => t.id === selectedId) ?? null

  /** 混合检索：稠密 / 稀疏 / 图三路，RRF 融合。 */
  const runQuery = useCallback(async () => {
    const text = query.trim()
    if (!text) {
      toast.error('请输入检索词')
      return
    }
    setReplayLoading(true)
    try {
      const payload = await queryHybridMemory(text)
      setSearchResult(normalizeQueryResponse(payload))
    } catch (err: unknown) {
      toast.error('混合检索失败：' + errorMessage(err))
    } finally {
      setReplayLoading(false)
    }
  }, [query])

  /** 沉淀规程：离线从高分成因链蒸馏程序性基因卡。 */
  const runConsolidate = useCallback(async () => {
    setConsolidating(true)
    try {
      const result = await consolidateProceduralMemory()
      setGenes((prev) => {
        const next = normalizeGenes(result.genes)
        if (next.length === 0) return prev
        const fresh = next.map((g) => g.gene_id)
        return [...next, ...prev.filter((g) => !fresh.includes(g.gene_id))]
      })
      toast.success(`巩固完成，蒸馏出 ${result.distilled} 张经验基因卡`)
    } catch (err: unknown) {
      toast.error('巩固程序性记忆失败：' + errorMessage(err))
    } finally {
      setConsolidating(false)
    }
  }, [])

  const loadTone: StatusTone = loading ? 'warning' : 'success'
  const loadText = loading ? '载入中' : replayLoading ? '回放中' : '就绪'

  return (
    <Layout>
      <div className="min-h-screen px-6 py-6" style={{ background: PAPER.canvas }}>
        {/* 顶栏 */}
        <div className="mb-5 flex items-center justify-between gap-6">
          <div className="flex items-baseline gap-4">
            <h1 className="flex items-center gap-2 text-[22px] font-semibold leading-7" style={{ color: PAPER.ink }}>
              <BrainCircuit size={20} style={{ color: PAPER.primary }} />
              认知记忆中枢
            </h1>
            <span
              className="inline-flex items-center gap-1.5 rounded-[4px] px-2 py-1 font-mono text-[12px] uppercase tracking-wider"
              style={{ background: PAPER.alt, color: PAPER.muted, border: `1px solid ${PAPER.hair}` }}
            >
              <Dot tone={loadTone} />
              {loadText}
            </span>
          </div>
          <div className="flex items-center gap-3">
            {graph && (
              <span className="font-mono text-[12px]" style={{ color: PAPER.subtle }}>
                图谱 {graph.stats.trace_count} 情景 · {graph.stats.gene_count} 基因 · {graph.stats.edge_count} 边
              </span>
            )}
            <PrimaryButton onClick={() => void runConsolidate()} disabled={consolidating}>
              {consolidating ? <Loader2 size={14} className="animate-spin" /> : null}
              沉淀规程
            </PrimaryButton>
          </div>
        </div>

        {/* 三列 */}
        <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)_340px]">
          {/* 左：情景剧集 */}
          <HairlineBox>
            <div className="px-4 py-3">
              <SectionTitle title="情景剧集" meta={traces.length > 0 ? String(traces.length) : undefined} />
            </div>
            <div className="max-h-[640px] overflow-y-auto" style={{ borderTop: `1px solid ${PAPER.hair}` }}>
              {traces.length === 0 ? (
                loading ? (
                  <EmptyState text="正在载入情景事件记忆…" />
                ) : (
                  <EmptyState text="尚无情景事件记忆，先让智能体沉淀一段执行经历。" />
                )
              ) : (
                traces.map((trace) => {
                  const active = trace.id === selectedId
                  return (
                    <button
                      key={trace.id}
                      type="button"
                      onClick={() => setSelectedId(trace.id)}
                      className="block w-full px-4 py-3 text-left transition-colors"
                      style={{
                        background: active ? PAPER.alt : PAPER.card,
                        borderBottom: `1px solid ${PAPER.hair}`,
                        borderLeft: `2px solid ${active ? PAPER.primary : 'transparent'}`,
                      }}
                    >
                      <div className="flex items-center gap-2">
                        <Dot tone={scoreTone(trace.outcome_score)} />
                        <span className="truncate text-[13px] font-medium" style={{ color: PAPER.ink }}>
                          {trace.task_summary}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center justify-between font-mono text-[11px]" style={{ color: PAPER.subtle }}>
                        <span className="truncate">{trace.badge ?? '未挂徽章'}</span>
                        <span>{shortTime(trace.created_at)}</span>
                      </div>
                      <div className="mt-1 font-mono text-[11px]" style={{ color: PAPER.muted }}>
                        结果分 {trace.outcome_score.toFixed(2)}
                      </div>
                    </button>
                  )
                })
              )}
            </div>
          </HairlineBox>

          {/* 中：时间序列回放 + 因果链条 */}
          <div className="flex flex-col gap-4">
            <HairlineBox>
              <div className="px-4 py-3">
                <SectionTitle
                  title="时间序列回放"
                  meta={selected ? shortTime(selected.created_at) : undefined}
                />
              </div>
              <div style={{ borderTop: `1px solid ${PAPER.hair}` }}>
                {!selected ? (
                  <EmptyState text="在左侧选择一个情景剧集以回放其执行序列。" />
                ) : selected.causal_chain.length === 0 ? (
                  <EmptyState text="该情景没有记录因果链步骤。" />
                ) : (
                  <ol className="px-4 py-3">
                    {selected.causal_chain.map((step, index) => (
                      <li key={`${selected.id}-${index}`} className="flex gap-3 py-2">
                        <span
                          className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full font-mono text-[11px]"
                          style={{ background: PAPER.alt, color: PAPER.muted, border: `1px solid ${PAPER.hair}` }}
                        >
                          {step.step || index + 1}
                        </span>
                        <div className="min-w-0">
                          <p className="text-[13px] leading-5" style={{ color: PAPER.ink }}>
                            {step.action}
                          </p>
                          <p className="mt-0.5 font-mono text-[11px]" style={{ color: PAPER.subtle }}>
                            {step.actor ? `执行者 ${step.actor}` : '执行者未记录'}
                            {step.note ? ` · ${step.note}` : ''}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
                {selected?.reflection_notes && (
                  <p className="px-4 pb-4 text-[13px] leading-5" style={{ color: PAPER.muted }}>
                    复盘：{selected.reflection_notes}
                  </p>
                )}
              </div>
            </HairlineBox>

            <HairlineBox>
              <div className="px-4 py-3">
                <SectionTitle
                  title="因果链条"
                  meta={replay ? `max_depth ${replay.max_depth_reached}` : undefined}
                />
              </div>
              <div style={{ borderTop: `1px solid ${PAPER.hair}` }}>
                {!replay || replay.nodes.length === 0 ? (
                  <EmptyState text={replayLoading ? '正在回放因果链…' : '该情景暂无可回放的因果上下游。'} />
                ) : (
                  <>
                    <ul className="px-4 pt-3">
                      {replay.nodes.map((node, index) => {
                        const meta = directionLabel(node.direction)
                        return (
                          <li key={`${node.trace_id}-${index}`} className="flex gap-3">
                            <div className="flex flex-col items-center">
                              <Dot tone={meta.tone} />
                              {index < replay.nodes.length - 1 && (
                                <span className="w-px flex-1" style={{ background: PAPER.hair, minHeight: 28 }} />
                              )}
                            </div>
                            <div className="min-w-0 flex-1 pb-3">
                              <div className="flex items-center justify-between gap-3">
                                <span className="truncate text-[13px] font-medium" style={{ color: PAPER.ink }}>
                                  {node.task_summary}
                                </span>
                                <span className="shrink-0 font-mono text-[11px]" style={{ color: PAPER.subtle }}>
                                  深度 {node.depth}
                                </span>
                              </div>
                              <div className="mt-1 flex items-center gap-2">
                                <StatusLabel tone={meta.tone}>{meta.text}</StatusLabel>
                                <span className="font-mono text-[11px]" style={{ color: PAPER.muted }}>
                                  {node.badge ?? '未挂徽章'} · 结果分 {node.outcome_score.toFixed(2)} · {shortTime(node.created_at)}
                                </span>
                              </div>
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                    <p className="px-4 pb-3 text-[12px]" style={{ color: PAPER.subtle }}>
                      {replay.truncated ? '因果链已按最大深度截断' : '因果链已完整展开'}
                    </p>
                  </>
                )}
              </div>
            </HairlineBox>
          </div>

          {/* 右：经验基因卡 + 混合检索 */}
          <div className="flex flex-col gap-4">
            <HairlineBox>
              <div className="px-4 py-3">
                <SectionTitle title="经验基因卡" meta={genes.length > 0 ? String(genes.length) : undefined} />
              </div>
              <div className="max-h-[320px] overflow-y-auto" style={{ borderTop: `1px solid ${PAPER.hair}` }}>
                {genes.length === 0 ? (
                  <EmptyState text="尚无经验基因卡，点击右上「沉淀规程」从高分情景蒸馏。" />
                ) : (
                  genes.map((gene) => (
                    <div key={gene.gene_id} className="px-4 py-3" style={{ borderBottom: `1px solid ${PAPER.hair}` }}>
                      <p className="text-[13px] font-medium leading-5" style={{ color: PAPER.ink }}>
                        {gene.trigger_pattern}
                      </p>
                      <p className="mt-1 text-[12px] leading-5" style={{ color: PAPER.muted }}>
                        {gene.successful_sop_patch || '无 SOP 补丁'}
                      </p>
                      <div className="mt-1.5 flex items-center gap-2">
                        <Tag tone={scoreTone(gene.confidence_rating)}>
                          置信度 {gene.confidence_rating.toFixed(2)}
                        </Tag>
                        <span className="font-mono text-[11px]" style={{ color: PAPER.subtle }}>
                          样本 {gene.support_count} · {gene.badge ?? '未挂徽章'}
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </HairlineBox>

            <HairlineBox>
              <div className="px-4 py-3">
                <SectionTitle title="混合检索" meta={searchResult ? searchResult.fusion.toUpperCase() : undefined} />
              </div>
              <div className="px-4 pb-3" style={{ borderTop: `1px solid ${PAPER.hair}` }}>
                <div className="mt-3">
                  <Field
                    label="检索词"
                    value={query}
                    onChange={setQuery}
                    placeholder="例如：数据库连接池超时"
                  />
                </div>
                <div className="mt-2 flex items-center justify-between gap-3">
                  <span className="text-[12px]" style={{ color: PAPER.subtle }}>
                    稠密 + 稀疏 + 图邻域，RRF 融合
                  </span>
                  <SecondaryButton onClick={() => void runQuery()} disabled={replayLoading}>
                    查询
                  </SecondaryButton>
                </div>
                {searchResult && (
                  <p className="mt-2 font-mono text-[11px]" style={{ color: PAPER.muted }}>
                    命中 {searchResult.hits.length} · 稠密 {searchResult.channel_counts.dense} · 稀疏{' '}
                    {searchResult.channel_counts.sparse} · 图 {searchResult.channel_counts.graph}
                  </p>
                )}
              </div>
              {searchResult && (
                <div style={{ borderTop: `1px solid ${PAPER.hair}` }}>
                  {searchResult.hits.length === 0 ? (
                    <EmptyState text="没有命中任何情景记忆。" />
                  ) : (
                    searchResult.hits.map((hit) => (
                      <div key={hit.trace_id} className="px-4 py-3" style={{ borderBottom: `1px solid ${PAPER.hair}` }}>
                        <div className="flex items-center justify-between gap-3">
                          <span className="truncate text-[13px] font-medium" style={{ color: PAPER.ink }}>
                            {hit.task_summary}
                          </span>
                          <span className="shrink-0 font-mono text-[12px]" style={{ color: PAPER.primary }}>
                            {hit.score.toFixed(3)}
                          </span>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          {hit.channels.length > 0 ? (
                            hit.channels.map((channel) => (
                              <Tag key={channel}>{CHANNEL_LABEL[channel]}</Tag>
                            ))
                          ) : (
                            <span className="text-[11px]" style={{ color: PAPER.subtle }}>无通道标记</span>
                          )}
                        </div>
                        <p className="mt-1 font-mono text-[11px]" style={{ color: PAPER.subtle }}>
                          {hit.badge ?? '未挂徽章'} · 结果分 {hit.outcome_score.toFixed(2)} · 因果距离{' '}
                          {hit.causal_distance === null ? '不可达' : hit.causal_distance}
                        </p>
                      </div>
                    ))
                  )}
                </div>
              )}
            </HairlineBox>
          </div>
        </div>
      </div>
    </Layout>
  )
}
