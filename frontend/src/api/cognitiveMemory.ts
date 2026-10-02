/**
 * 认知记忆中枢 API 客户端（AutoTeams 5.0 战役 2 · 分层长程因果认知记忆）。
 *
 * 对应后端 backend/app/api/cognitive_memory.py（挂载于 /api/v1/memory）：
 * - POST /memory/traces             记录一段情景事件记忆
 * - GET  /memory/traces             查询情景事件记忆列表
 * - GET  /memory/traces/{id}/replay 按因果链回放（含祖先/后代）
 * - POST /memory/query              稠密/稀疏/图三路混合检索
 * - GET  /memory/genes              查询程序性经验基因卡
 * - POST /memory/genes/consolidate  离线巩固：从高分成因链蒸馏规程
 * - GET  /memory/graph              查询记忆图谱（节点 / 边 / 统计）
 *
 * 所有响应统一包在 { success, data } 信封内，此处只返回 data。
 * 契约字段与 backend/app/services/ 的情景 / 程序性记忆 Schema 保持一致。
 */
import apiClient from './client'

/** 因果链上的一步（情景事件记忆内的有序动作）。 */
export interface CausalStep {
  step: number
  action: string
  actor?: string
  note?: string
}

/** 写入一段情景事件记忆时的请求体。 */
export interface EpisodicEventTrace {
  task_summary: string
  caused_by_event_id: string | null
  causal_chain: CausalStep[]
  reflection_notes: string | null
  outcome_score: number
  context_tags: string[]
  steps_detail?: unknown
}

/** 情景事件记忆。 */
export interface EpisodicTrace {
  id: string
  enterprise_id: string
  badge: string | null
  caused_by_event_id: string | null
  task_summary: string
  causal_chain: CausalStep[]
  reflection_notes: string | null
  outcome_score: number
  context_tags: string[]
  steps_detail: unknown
  created_at: string
}

/** 因果链回放中的单个节点。 */
export interface CausalChainNode {
  trace_id: string
  badge: string | null
  task_summary: string
  outcome_score: number
  depth: number
  direction: 'ancestor' | 'descendant' | 'self'
  created_at: string
}

/** 因果链回放结果。 */
export interface CausalChainReplay {
  root_trace_id: string
  nodes: CausalChainNode[]
  max_depth_reached: number
  truncated: boolean
}

/** 混合检索的召回通道。 */
export type RetrievalChannel = 'dense' | 'sparse' | 'graph'

/** 混合检索命中项（score 为 RRF 融合后的融合分）。 */
export interface MemoryHit {
  trace_id: string
  badge: string | null
  task_summary: string
  outcome_score: number
  created_at: string
  score: number
  channels: RetrievalChannel[]
  causal_distance: number | null
}

/** 混合检索响应。 */
export interface HybridMemoryResponse {
  query: string
  hits: MemoryHit[]
  channel_counts: { dense: number; sparse: number; graph: number }
  fusion: 'rrf'
}

/** 程序性经验基因卡（一次成功 SOP 的固化补丁）。 */
export interface ProceduralGene {
  gene_id: string
  enterprise_id: string
  badge: string | null
  trigger_pattern: string
  successful_sop_patch: string
  confidence_rating: number
  support_count: number
  source_trace_ids: string[]
  created_at: string
  updated_at: string
}

/** 记忆图谱节点。 */
export interface MemoryGraphNode {
  id: string
  label: string
  kind: 'trace' | 'gene'
  badge: string | null
  weight: number
  created_at: string
}

/** 记忆图谱边。 */
export interface MemoryGraphEdge {
  source: string
  target: string
  kind: 'caused_by' | 'distilled_into'
  weight: number
}

/** 记忆图谱。 */
export interface MemoryGraph {
  nodes: MemoryGraphNode[]
  edges: MemoryGraphEdge[]
  stats: { trace_count: number; gene_count: number; edge_count: number }
}

/** 巩固（离线蒸馏）结果。 */
export interface ConsolidationResult {
  genes: ProceduralGene[]
  distilled: number
  scanned_traces: number
}

/** 记录一段情景事件记忆。 */
export async function recordEpisodicTrace(
  badge: string,
  eventTrace: EpisodicEventTrace,
): Promise<EpisodicTrace> {
  const resp = await apiClient.post('/memory/traces', {
    badge,
    event_trace: eventTrace,
  })
  return resp.data.data
}

/** 查询情景事件记忆列表。 */
export async function listEpisodicTraces(limit = 50): Promise<EpisodicTrace[]> {
  const resp = await apiClient.get('/memory/traces', { params: { limit } })
  return resp.data.data
}

/** 回放一条情景记忆的因果链。 */
export async function replayCausalChain(
  traceId: string,
  maxDepth = 6,
): Promise<CausalChainReplay> {
  const resp = await apiClient.get(`/memory/traces/${encodeURIComponent(traceId)}/replay`, {
    params: { max_depth: maxDepth },
  })
  return resp.data.data
}

/** 三路混合检索（稠密向量 / 稀疏关键词 / 因果图邻域，RRF 融合）。 */
export async function queryHybridMemory(
  query: string,
  contextTags?: string[],
  topK?: number,
): Promise<HybridMemoryResponse> {
  const resp = await apiClient.post('/memory/query', {
    query,
    context_tags: contextTags,
    top_k: topK,
  })
  return resp.data.data
}

/** 查询程序性经验基因卡列表。 */
export async function listProceduralGenes(limit = 50): Promise<ProceduralGene[]> {
  const resp = await apiClient.get('/memory/genes', { params: { limit } })
  return resp.data.data
}

/** 离线巩固：从高分成因链蒸馏 / 更新规程基因卡。 */
export async function consolidateProceduralMemory(
  minOutcomeScore?: number,
  minSamples?: number,
): Promise<ConsolidationResult> {
  const resp = await apiClient.post('/memory/genes/consolidate', {
    min_outcome_score: minOutcomeScore,
    min_samples: minSamples,
  })
  return resp.data.data
}

/** 查询记忆图谱（情景 / 基因 / 因果边）。 */
export async function getMemoryGraph(limit = 120): Promise<MemoryGraph> {
  const resp = await apiClient.get('/memory/graph', { params: { limit } })
  return resp.data.data
}
