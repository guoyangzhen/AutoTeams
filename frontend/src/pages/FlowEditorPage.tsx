/**
 * FlowEditorPage — 规程卡编排器（SOP 状态机编排与单步仿真）。
 *
 * 设计基线：AutoTeams UI 重设计蓝图 v2「克制 · 编辑式」
 * - 纸面底 + 白底发丝线容器，层级由排版与留白建立，不靠底色块与描边卡
 * - 节点图 = 白底 1px 发丝线节点 + 1px 实线连线 + 12px 条件标签，选中节点主色描边
 * - 全屏仅 1 枚实心主按钮（保存修订）
 * - 状态一律 6px 圆点 + 13px 文字
 *
 * 数据来源严格对齐后端 /api/v1/flow-core（见 api/flowCore.ts）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  Workflow,
  Play,
  StepForward,
  ShieldCheck,
  Sparkles,
  Save,
  RefreshCw,
  Check,
  X,
  AlertTriangle,
  CornerDownRight,
  FileText,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { Drawer } from '@/components/ui/Drawer'
import { ModalShell } from '@/components/ui/ModalShell'
import {
  listFlowCards,
  saveFlowCard,
  synthesizeSOP,
  startFlow,
  stepFlow,
  approveFlowRun,
  type FlowCard,
  type FlowExecutionRun,
  type FlowGuardrails,
  type FlowNode,
  type FlowNodeType,
} from '@/api/flowCore'

// ============================================================
// 设计常量与元数据映射
// ============================================================

const NODE_W = 184
const NODE_H = 78
const GAP_X = 88
const GAP_Y = 30
const HAIREDGE = '#D1D1CD'

const NODE_TYPE_META: Record<FlowNodeType, { label: string; hint: string }> = {
  collect_info: { label: '交互采集节点', hint: 'Collect' },
  action_tool: { label: '工具执行节点', hint: 'Action' },
  branch_condition: { label: '条件分支节点', hint: 'Branch' },
  approval_human: { label: '人工审批节点', hint: 'HITL Gateway' },
  sub_flow: { label: '子规程嵌套', hint: 'Sub Flow' },
}

const GUARDRAIL_META: Array<{ key: keyof FlowGuardrails; label: string; rule: string }> = [
  {
    key: 'closed_loop_required',
    label: '闭环履约校验',
    rule: '禁止以「稍候 / 正在处理」作为终局回复，状态机必须抵达终端节点收敛。',
  },
  {
    key: 'adaptive_slot_filling',
    label: '槽位自适应跳步',
    rule: '上下文中已具备的必填槽位禁止重复追问，命中即直接推进下一节点。',
  },
  {
    key: 'high_risk_confirmation',
    label: '高危写动作二次确认',
    rule: '命中退款 / 转账 / 删除等高危词的动作节点强制阻断，转入人工核准后方可执行。',
  },
]

const DEFAULT_GUARDRAILS: FlowGuardrails = {
  closed_loop_required: true,
  adaptive_slot_filling: true,
  high_risk_confirmation: true,
}

/** 6px 圆点 + 13px 文字的状态表达（蓝图 §2 状态规范）。 */
function StatusDot({ tone, label }: { tone: 'success' | 'warning' | 'muted' | 'brand'; label: string }) {
  const color =
    tone === 'success' ? 'bg-success' : tone === 'warning' ? 'bg-warning' : tone === 'brand' ? 'bg-brand-500' : 'bg-text-muted'
  return (
    <span className="inline-flex items-center gap-2 text-body-sm text-text-tertiary whitespace-nowrap">
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${color}`} aria-hidden="true" />
      {label}
    </span>
  )
}

/** 极简开关：文字 ON/OFF + 22×14 轨道，选中态为墨色。 */
function MiniToggle({ on, onToggle }: { on: boolean; onToggle?: () => void }) {
  const track = on ? 'bg-text-primary justify-end' : 'bg-border-default justify-start'
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="font-mono text-[11px] font-semibold text-text-primary w-6">{on ? 'ON' : 'OFF'}</span>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        disabled={!onToggle}
        onClick={onToggle}
        className={`w-7 h-4 rounded-full flex items-center px-0.5 transition-colors ${track} ${
          onToggle ? 'cursor-pointer' : 'cursor-default opacity-70'
        }`}
      >
        <span className="w-3 h-3 rounded-full bg-surface" />
      </button>
    </span>
  )
}

// ============================================================
// 节点画布布局：自 start_node_id 起 BFS 分层，列内按定义顺序居中
// ============================================================

interface PlacedNode {
  node: FlowNode
  x: number
  y: number
}

interface PlacedEdge {
  key: string
  sourceId: string
  targetId: string
  x1: number
  y1: number
  x2: number
  y2: number
  label: string
  labelX: number
  labelY: number
}

interface GraphLayout {
  nodes: PlacedNode[]
  edges: PlacedEdge[]
  width: number
  height: number
}

function buildGraphLayout(card: FlowCard): GraphLayout {
  const nodes = card.nodes ?? []
  const edges = card.edges ?? []
  if (nodes.length === 0) return { nodes: [], edges: [], width: 0, height: 0 }

  const layerOf = new Map<string, number>()
  const queue: string[] = [card.start_node_id]
  layerOf.set(card.start_node_id, 0)
  const settled = new Set<string>()
  let guard = 0
  while (queue.length > 0 && guard < 4096) {
    guard += 1
    const current = queue.shift() as string
    if (settled.has(current)) continue
    settled.add(current)
    const currentLayer = layerOf.get(current) ?? 0
    for (const edge of edges) {
      if (edge.source_node_id !== current) continue
      const known = layerOf.get(edge.target_node_id)
      if (known === undefined || known > currentLayer + 1) {
        layerOf.set(edge.target_node_id, currentLayer + 1)
        queue.push(edge.target_node_id)
      }
    }
  }

  // 游离节点（未与 start 连通）统一挂在最后一层，避免丢失
  let maxLayer = 0
  for (const node of nodes) maxLayer = Math.max(maxLayer, layerOf.get(node.node_id) ?? 0)
  for (const node of nodes) {
    if (!layerOf.has(node.node_id)) {
      maxLayer += 1
      layerOf.set(node.node_id, maxLayer)
    }
  }

  const columns = new Map<number, FlowNode[]>()
  for (const node of nodes) {
    const layer = layerOf.get(node.node_id) ?? 0
    const bucket = columns.get(layer)
    if (bucket) bucket.push(node)
    else columns.set(layer, [node])
  }

  const tallest = Math.max(...Array.from(columns.values()).map((c) => c.length), 1)
  const canvasHeight = tallest * NODE_H + (tallest - 1) * GAP_Y

  const placed: PlacedNode[] = []
  for (const [layer, bucket] of columns) {
    const colHeight = bucket.length * NODE_H + (bucket.length - 1) * GAP_Y
    const offset = (canvasHeight - colHeight) / 2
    bucket.forEach((node, idx) => {
      placed.push({
        node,
        x: layer * (NODE_W + GAP_X),
        y: offset + idx * (NODE_H + GAP_Y),
      })
    })
  }

  const posOf = new Map(placed.map((p) => [p.node.node_id, p]))
  const placedEdges: PlacedEdge[] = edges
    .map((edge, idx) => {
      const from = posOf.get(edge.source_node_id)
      const to = posOf.get(edge.target_node_id)
      if (!from || !to) return null
      const x1 = from.x + NODE_W
      const y1 = from.y + NODE_H / 2
      const x2 = to.x
      const y2 = to.y + NODE_H / 2
      const c1 = x1 + (x2 - x1) / 2
      const c2 = x2 - (x2 - x1) / 2
      return {
        key: `${edge.source_node_id}->${edge.target_node_id}-${idx}`,
        sourceId: edge.source_node_id,
        targetId: edge.target_node_id,
        x1,
        y1,
        x2,
        y2,
        label: edge.label || edge.condition_expression || '',
        // 三次贝塞尔 t=0.5 处的解析中点
        labelX: (x1 + 3 * c1 + 3 * c2 + x2) / 8,
        labelY: (y1 + 3 * y1 + 3 * y2 + y2) / 8,
      }
    })
    .filter((e): e is PlacedEdge => e !== null)

  const width = (maxLayer + 1) * NODE_W + maxLayer * GAP_X
  return { nodes: placed, edges: placedEdges, width, height: canvasHeight }
}

// ============================================================
// 页面主体
// ============================================================

export default function FlowEditorPage() {
  const [cards, setCards] = useState<FlowCard[]>([])
  const [draft, setDraft] = useState<FlowCard | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [inspectorTab, setInspectorTab] = useState<'node' | 'steps'>('node')

  // 护栏抽屉
  const [guardrailOpen, setGuardrailOpen] = useState(false)

  // 自然语言经验提炼
  const [synthOpen, setSynthOpen] = useState(false)
  const [synthText, setSynthText] = useState('')
  const [synthName, setSynthName] = useState('')
  const [synthesizing, setSynthesizing] = useState(false)

  // 单步仿真
  const [runState, setRunState] = useState<FlowExecutionRun | null>(null)
  const [stepping, setStepping] = useState(false)
  const [userInput, setUserInput] = useState('')

  const loadCards = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listFlowCards()
      const list = data ?? []
      setCards(list)
      setDraft((prev) => {
        if (prev) {
          const stillThere = list.find((c) => c.flow_id === prev.flow_id)
          if (stillThere) return stillThere
        }
        return list[0] ?? null
      })
    } catch (err) {
      toast.error(`加载规程库失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadCards()
  }, [loadCards])

  // 切换规程时复位选中节点与运行态
  useEffect(() => {
    if (!draft) {
      setSelectedNodeId('')
      return
    }
    const nodeIds = (draft.nodes ?? []).map((n) => n.node_id)
    setSelectedNodeId((prev) => (prev && nodeIds.includes(prev) ? prev : draft.start_node_id))
    setRunState(null)
    setUserInput('')
  }, [draft?.flow_id]) // eslint-disable-line react-hooks/exhaustive-deps

  const graph = useMemo(() => (draft ? buildGraphLayout(draft) : null), [draft])
  const selectedNode = useMemo(
    () => draft?.nodes?.find((n) => n.node_id === selectedNodeId) ?? null,
    [draft, selectedNodeId],
  )
  const guardrails: FlowGuardrails = draft?.guardrails ?? DEFAULT_GUARDRAILS

  const terminalSet = useMemo(
    () => new Set(draft?.terminal_node_ids ?? []),
    [draft],
  )
  const currentNode = useMemo(
    () => (runState ? draft?.nodes?.find((n) => n.node_id === runState.current_node_id) ?? null : null),
    [runState, draft],
  )

  const topologyIssues = useMemo(() => {
    if (!draft) return [] as string[]
    const ids = new Set((draft.nodes ?? []).map((n) => n.node_id))
    const issues: string[] = []
    if (!ids.has(draft.start_node_id)) issues.push('起始节点不在节点集合中')
    for (const edge of draft.edges ?? []) {
      if (!ids.has(edge.source_node_id)) issues.push(`连线源 ${edge.source_node_id} 不存在`)
      if (!ids.has(edge.target_node_id)) issues.push(`连线目标 ${edge.target_node_id} 不存在`)
    }
    if ((draft.nodes ?? []).length === 0) issues.push('规程至少需要一个节点')
    return issues
  }, [draft])

  const patchGuardrail = (key: keyof FlowGuardrails, value: boolean) => {
    if (!draft) return
    setDraft({ ...draft, guardrails: { ...guardrails, [key]: value } })
  }

  const handleSave = async () => {
    if (!draft) return
    setSaving(true)
    try {
      await saveFlowCard({ ...draft, guardrails })
      toast.success(`规程「${draft.name}」修订已保存至企业规程库`)
      await loadCards()
    } catch (err) {
      toast.error(`保存失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setSaving(false)
    }
  }

  const handleSynthesize = async () => {
    if (!synthText.trim()) {
      toast.error('请先粘贴业务规程或操作手册原文')
      return
    }
    setSynthesizing(true)
    try {
      const card = await synthesizeSOP(synthText.trim(), { name: synthName.trim() || undefined })
      setDraft(card)
      setSynthOpen(false)
      setSynthText('')
      setSynthName('')
      toast.success('经验提炼完成，已生成标准 FlowCard 状态机')
      await loadCards()
    } catch (err) {
      toast.error(`提炼失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setSynthesizing(false)
    }
  }

  const handleStartRun = async () => {
    if (!draft) return
    setStepping(true)
    try {
      const run = await startFlow(draft.flow_id)
      setRunState(run)
      setSelectedNodeId(run.current_node_id)
      toast.success('状态机已启动')
    } catch (err) {
      toast.error(`启动失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setStepping(false)
    }
  }

  // AUD-15：执行状态由服务端持有，客户端只提交 run_id + 乐观锁版本号。
  const handleStep = async (opts?: { userInput?: string }) => {
    if (!runState) return
    setStepping(true)
    try {
      const run = await stepFlow(runState.run_id, {
        userInput: opts?.userInput,
        expectedVersion: runState.version,
      })
      setRunState(run)
      setUserInput('')
      setSelectedNodeId(run.current_node_id)
    } catch (err) {
      toast.error(`流转失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setStepping(false)
    }
  }

  // 审批是独立端点：非授权审批人返回 403 且不落库、不推进；版本过期返回 409。
  const handleApprove = async (decision: 'granted' | 'rejected') => {
    if (!runState) return
    const nodeId = runState.state?.pending_approval_node_id ?? runState.current_node_id
    setStepping(true)
    try {
      const run = await approveFlowRun(runState.run_id, nodeId, decision, {
        expectedVersion: runState.version,
      })
      setRunState(run)
      setSelectedNodeId(run.current_node_id)
      toast.success(decision === 'granted' ? '已核准' : '已驳回')
    } catch (err) {
      toast.error(`审批失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setStepping(false)
    }
  }

  const handleSelectCard = (card: FlowCard) => {
    setDraft(card)
    setInspectorTab('node')
  }

  return (
    <Layout>
      <div className="at-paper min-h-full w-full px-6 md:px-10 py-8">
        <div className="w-full max-w-[1280px] mx-auto space-y-6">
        <header className="flex items-start justify-between gap-6 flex-wrap">
          <div className="min-w-0">
            <h1 className="text-[30px] leading-9 font-bold tracking-tight text-text-primary">
              规程卡编排器
            </h1>
            <div className="mt-2 flex items-center flex-wrap gap-2 text-body-sm text-text-tertiary">
              <span className="text-text-primary">{draft?.name ?? '未选择规程'}</span>
              <span className="text-border-default">·</span>
              <span className="font-mono text-caption">v{draft?.version ?? '—'}</span>
              <span className="text-border-default">·</span>
              <span className="font-mono text-caption">{draft?.nodes?.length ?? 0} 节点</span>
              <span className="text-border-default">·</span>
              <span className="font-mono text-caption">{draft?.edges?.length ?? 0} 连线</span>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => void loadCards()}
              className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
              刷新
            </button>
            <button
              type="button"
              onClick={() => setSynthOpen(true)}
              className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
            >
              <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
              经验提炼
            </button>
            <button
              type="button"
              onClick={() => setGuardrailOpen(true)}
              className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
            >
              <ShieldCheck className="w-3.5 h-3.5" aria-hidden="true" />
              三重安全护栏
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={!draft || saving}
              className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" aria-hidden="true" />
              {saving ? '保存中…' : '保存修订'}
            </button>
          </div>
        </header>

        {/* ============ 2. 规程库：一行一条，无卡片包裹 ============ */}
        <section className="border-y border-border-subtle">
          <div className="flex items-center justify-between py-2.5">
            <h2 className="text-h4 font-semibold text-text-primary">企业规程库</h2>
            <span className="font-mono text-caption text-text-tertiary">{cards.length} 条</span>
          </div>
          {cards.length === 0 ? (
            <div className="py-6 text-center">
              <p className="text-body-sm text-text-secondary">规程库还是空的</p>
              <p className="mt-1 text-caption text-text-muted">
                点击「经验提炼」，输入一段业务手册或操作经验，由 AI 逆向编译为标准
                FlowCard 状态机；提炼依赖 LLM，网络波动时可能失败，可重试。
              </p>
            </div>
          ) : (
            <ul className="flex gap-6 overflow-x-auto scrollbar-thin pb-1">
              {cards.map((card) => {
                const active = draft?.flow_id === card.flow_id
                return (
                  <li key={card.flow_id} className="shrink-0">
                    <button
                      type="button"
                      onClick={() => handleSelectCard(card)}
                      className={`text-left pl-3 border-l-2 py-0.5 transition-colors ${
                        active ? 'border-brand-500' : 'border-transparent hover:border-border-default'
                      }`}
                    >
                      <span
                        className={`block text-body-sm whitespace-nowrap ${
                          active ? 'text-text-primary font-semibold' : 'text-text-secondary'
                        }`}
                      >
                        {card.name}
                      </span>
                      <span className="mt-0.5 flex items-center gap-2 font-mono text-caption text-text-tertiary">
                        <span>v{card.version}</span>
                        <span className="text-border-default">/</span>
                        <span>{card.nodes?.length ?? 0} 节点</span>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        {!draft ? (
          <div className="rounded-[10px] border border-border-default bg-surface px-6 py-16 text-center">
            <Workflow className="w-7 h-7 mx-auto text-text-muted mb-3" aria-hidden="true" />
            <p className="text-h4 font-semibold text-text-primary">请选择一条规程或现场提炼一份</p>
            <p className="mt-1.5 text-body-sm text-text-tertiary">
              规程卡是数字员工执行复杂业务的唯一合法路径：节点、槽位、授权工具与三条护栏共同构成强约束。
            </p>
            <button
              type="button"
              onClick={() => setSynthOpen(true)}
              className="mt-5 h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors"
            >
              <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
              提炼规程
            </button>
          </div>
        ) : (
          <>
            {/* ============ 3. 节点画布 + 节点检视器 ============ */}
            <div className="flex gap-6 items-start">
              {/* 节点画布 */}
              <section className="flex-1 min-w-0 rounded-[10px] border border-border-default bg-surface">
                <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-border-subtle">
                  <div className="flex items-center gap-3 min-w-0">
                    <h2 className="text-h4 font-semibold text-text-primary whitespace-nowrap">
                      状态机执行流
                    </h2>
                    <span className="font-mono text-caption text-text-muted truncate">
                      FLOW_ID: {draft.flow_id}
                    </span>
                  </div>
                  {topologyIssues.length === 0 ? (
                    <StatusDot tone="success" label="拓扑校验已通过" />
                  ) : (
                    <StatusDot tone="warning" label={`${topologyIssues.length} 项拓扑告警`} />
                  )}
                </div>

                <div className="overflow-auto scrollbar-thin px-6 py-8">
                  <div
                    className="relative"
                    style={{
                      width: Math.max(graph?.width ?? 0, 320),
                      height: Math.max(graph?.height ?? 0, 180),
                    }}
                  >
                    {/* 连线：1px 实线 */}
                    <svg
                      className="absolute inset-0 pointer-events-none"
                      width={Math.max(graph?.width ?? 0, 320)}
                      height={Math.max(graph?.height ?? 0, 180)}
                      aria-hidden="true"
                    >
                      {(graph?.edges ?? []).map((edge) => {
                        const c1 = edge.x1 + (edge.x2 - edge.x1) / 2
                        const c2 = edge.x2 - (edge.x2 - edge.x1) / 2
                        const active =
                          runState?.current_node_id === edge.sourceId ||
                          runState?.current_node_id === edge.targetId
                        return (
                          <path
                            key={edge.key}
                            d={`M ${edge.x1} ${edge.y1} C ${c1} ${edge.y1}, ${c2} ${edge.y2}, ${edge.x2} ${edge.y2}`}
                            fill="none"
                            stroke={active ? '#1E3A5F' : HAIREDGE}
                            strokeWidth="1"
                          />
                        )
                      })}
                    </svg>

                    {/* 条件标签：12px mono */}
                    {(graph?.edges ?? []).map((edge) =>
                      edge.label ? (
                        <span
                          key={`${edge.key}-label`}
                          className="absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded border border-border-default bg-surface px-1.5 py-0.5 font-mono text-[11px] leading-4 text-text-tertiary"
                          style={{ left: edge.labelX, top: edge.labelY }}
                        >
                          {edge.label}
                        </span>
                      ) : null,
                    )}

                    {/* 节点：白底 1px 发丝线，选中主色描边 */}
                    {(graph?.nodes ?? []).map((placed) => {
                      const { node } = placed
                      const isStart = node.node_id === draft.start_node_id
                      const isEnd = terminalSet.has(node.node_id)
                      const isRunning = runState?.current_node_id === node.node_id
                      const selected = node.node_id === selectedNodeId
                      return (
                        <button
                          key={node.node_id}
                          type="button"
                          onClick={() => {
                            setSelectedNodeId(node.node_id)
                            setInspectorTab('node')
                          }}
                          style={{ left: placed.x, top: placed.y, width: NODE_W, minHeight: NODE_H }}
                          className={`absolute text-left px-3.5 py-3 rounded-[6px] bg-surface transition-colors ${
                            selected
                              ? 'border border-brand-500'
                              : 'border border-border-default hover:border-border-strong'
                          }`}
                        >
                          <span className="flex items-center gap-1.5">
                            <span className="text-body-sm font-medium text-text-primary truncate">
                              {node.name}
                            </span>
                            {isRunning && (
                              <span
                                className="w-1.5 h-1.5 rounded-full bg-brand-500 flex-shrink-0"
                                aria-label="运行中"
                              />
                            )}
                          </span>
                          <span className="mt-1.5 flex items-center gap-1.5">
                            <span className="font-mono text-[11px] leading-4 text-text-tertiary truncate">
                              {NODE_TYPE_META[node.node_type]?.hint ?? node.node_type}
                            </span>
                            {isStart && (
                              <span className="font-mono text-[10px] leading-4 text-brand-500">
                                START
                              </span>
                            )}
                            {isEnd && (
                              <span className="font-mono text-[10px] leading-4 text-success">END</span>
                            )}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="flex items-center justify-between gap-3 px-6 py-3 border-t border-border-subtle text-caption text-text-tertiary flex-wrap">
                  <span>状态机策略：线性约束 · 支持动态重试与降级</span>
                  <span className="font-mono">
                    MAX_TIMEOUT: {Math.max(0, ...(draft.nodes ?? []).map((n) => n.timeout_seconds ?? 0))}s
                  </span>
                </div>
              </section>

              {/* 节点检视器 */}
              <aside className="w-[320px] shrink-0 rounded-[10px] border border-border-default bg-surface flex flex-col">
                <div className="flex items-center border-b border-border-subtle">
                  {(
                    [
                      { key: 'node', label: '节点属性' },
                      { key: 'steps', label: '步骤编排' },
                    ] as const
                  ).map((tab) => (
                    <button
                      key={tab.key}
                      type="button"
                      onClick={() => setInspectorTab(tab.key)}
                      className={`px-5 py-3.5 text-body-sm border-b-2 -mb-px transition-colors ${
                        inspectorTab === tab.key
                          ? 'border-brand-500 text-text-primary font-semibold'
                          : 'border-transparent text-text-tertiary hover:text-text-secondary'
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {inspectorTab === 'node' ? (
                  selectedNode ? (
                    <div className="px-5 py-1 divide-y divide-border-subtle">
                      <FactRow label="节点名称" value={selectedNode.name} />
                      <FactRow
                        label="节点类型"
                        value={
                          NODE_TYPE_META[selectedNode.node_type]?.label ?? selectedNode.node_type
                        }
                      />
                      <FactRow
                        label="指令"
                        value={selectedNode.instruction || '—'}
                        mono={false}
                      />
                      <FactRow
                        label="必填槽位"
                        value={(selectedNode.expected_slots ?? []).join('、') || '—'}
                      />
                      <FactRow
                        label="授权工具"
                        value={(selectedNode.bound_tools ?? []).join('、') || '—'}
                      />
                      <FactRow
                        label="知识范围"
                        value={(selectedNode.scoped_knowledge_buckets ?? []).join('、') || '—'}
                      />
                      <FactRow
                        label="超时"
                        value={`${selectedNode.timeout_seconds ?? 120}s`}
                      />
                      {selectedNode.assignee_role && (
                        <FactRow label="审批角色" value={selectedNode.assignee_role} />
                      )}
                      <FactRow label="节点 ID" value={selectedNode.node_id} />
                    </div>
                  ) : (
                    <p className="px-5 py-8 text-body-sm text-text-muted text-center">
                      在画布中选择一个节点以查看其属性
                    </p>
                  )
                ) : (
                  <ol className="px-5 py-1 divide-y divide-border-subtle max-h-[420px] overflow-y-auto scrollbar-thin">
                    {(draft.nodes ?? []).map((node, idx) => (
                      <li key={node.node_id}>
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedNodeId(node.node_id)
                            setInspectorTab('node')
                          }}
                          className="w-full text-left py-3 flex items-start gap-3 group"
                        >
                          <span className="font-mono text-caption text-text-muted w-5 flex-shrink-0 pt-0.5">
                            {String(idx + 1).padStart(2, '0')}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-body-sm text-text-primary group-hover:text-brand-500 transition-colors">
                              {node.name}
                            </span>
                            <span className="mt-0.5 block font-mono text-[11px] leading-4 text-text-tertiary">
                              {NODE_TYPE_META[node.node_type]?.label ?? node.node_type}
                              {node.expected_slots?.length
                                ? ` · ${node.expected_slots.length} 槽位`
                                : ''}
                            </span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ol>
                )}

                <div className="mt-auto px-5 py-4 border-t border-border-subtle">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-body-sm font-medium text-text-tertiary">防护护栏</span>
                    <button
                      type="button"
                      onClick={() => setGuardrailOpen(true)}
                      className="text-caption text-brand-500 hover:underline"
                    >
                      查看全部
                    </button>
                  </div>
                  <div className="space-y-2.5">
                    {GUARDRAIL_META.map((g) => (
                      <div key={g.key} className="flex items-center justify-between gap-2">
                        <span className="text-body-sm text-text-primary truncate">{g.label}</span>
                        <MiniToggle on={Boolean(guardrails?.[g.key])} />
                      </div>
                    ))}
                  </div>
                </div>
              </aside>
            </div>

            {/* ============ 4. 单步仿真日志 ============ */}
            <section className="rounded-[10px] border border-border-default bg-surface">
              <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-border-subtle flex-wrap">
                <h2 className="text-h4 font-semibold text-text-primary">单步仿真日志</h2>
                <div className="flex items-center gap-3">
                  {runState ? (
                    <>
                      <span className="font-mono text-caption text-text-tertiary">
                        NODE: {runState.current_node_id}
                      </span>
                      <span className="font-mono text-caption text-text-tertiary">
                        RUN: {runState.run_id.slice(0, 8)} · v{runState.version} ·{' '}
                        {runState.step_count} 步
                      </span>
                      <StatusDot
                        tone={
                          runState.status === 'completed'
                            ? 'success'
                            : runState.status === 'failed'
                              ? 'warning'
                              : 'brand'
                        }
                        label={
                          runState.status === 'completed'
                            ? '已闭环收敛'
                            : runState.status === 'failed'
                              ? '流程终止'
                              : runState.status === 'waiting_approval'
                                ? '等待人工核准'
                                : runState.status === 'waiting_user_input'
                                  ? '等待用户输入'
                                  : '运行中'
                        }
                      />
                    </>
                  ) : (
                    <StatusDot tone="muted" label="尚未启动" />
                  )}
                  <button
                    type="button"
                    onClick={handleStartRun}
                    disabled={stepping}
                    className="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-caption text-text-secondary hover:bg-elevated transition-colors disabled:opacity-50"
                  >
                    <Play className="w-3 h-3" aria-hidden="true" />
                    启动仿真
                  </button>
                </div>
              </div>

              {!runState ? (
                <p className="px-6 py-8 text-body-sm text-text-muted text-center">
                  启动后按节点逐步推进，可观察槽位累积、护栏拦截与终局收敛。
                </p>
              ) : (
                <>
                  <div className="px-6 py-5">
                    <p className="text-caption text-text-tertiary">数字员工提示</p>
                    <p className="mt-1 text-[14px] leading-6 text-text-primary">
                      {runState.last_output || '—'}
                    </p>
                    {runState.error_message && (
                      <p className="mt-2 inline-flex items-center gap-1.5 text-body-sm text-error">
                        <AlertTriangle className="w-3.5 h-3.5" aria-hidden="true" />
                        {runState.error_message}
                      </p>
                    )}

                    {Object.keys(runState.state?.accumulated_slots ?? {}).length > 0 && (
                      <div className="mt-4 flex flex-wrap items-center gap-2">
                        <span className="text-caption text-text-tertiary">已沉淀槽位</span>
                        {Object.entries(runState.state?.accumulated_slots ?? {}).map(([k, v]) => (
                          <span
                            key={k}
                            className="rounded border border-border-default bg-elevated px-2 py-0.5 font-mono text-[11px] leading-4 text-text-secondary"
                          >
                            {k} = {String(v)}
                          </span>
                        ))}
                      </div>
                    )}

                    {runState.status !== 'completed' && runState.status !== 'failed' && (
                      <div className="mt-5 flex items-center gap-2 flex-wrap">
                        {runState.status === 'waiting_approval' ? (
                          <>
                            <span className="text-body-sm text-text-secondary">
                              本步骤触发高危护栏，需人工核准后继续：
                            </span>
                            <button
                              type="button"
                              onClick={() => void handleApprove('rejected')}
                              disabled={stepping}
                              className="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-caption text-text-secondary hover:bg-elevated transition-colors disabled:opacity-50"
                            >
                              <X className="w-3 h-3" aria-hidden="true" />
                              驳回终止
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleApprove('granted')}
                              disabled={stepping}
                              className="h-8 px-3 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-caption font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
                            >
                              <Check className="w-3 h-3" aria-hidden="true" />
                              核准通过
                            </button>
                          </>
                        ) : (
                          <>
                            <input
                              type="text"
                              value={userInput}
                              onChange={(e) => setUserInput(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' && userInput.trim()) void handleStep({ userInput: userInput.trim() })
                              }}
                              placeholder={`响应「${currentNode?.name ?? '当前节点'}」所需的槽位信息…`}
                              className="flex-1 min-w-[220px] h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500"
                            />
                            <button
                              type="button"
                              onClick={() => void handleStep({ userInput: userInput.trim() || undefined })}
                              disabled={stepping || !userInput.trim()}
                              className="h-9 px-3.5 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors disabled:opacity-50"
                            >
                              <StepForward className="w-3.5 h-3.5" aria-hidden="true" />
                              {stepping ? '推进中…' : '推进一步'}
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>

                  {(runState.state?.history_trace ?? []).length > 0 && (
                    <div className="border-t border-border-subtle px-6 py-5">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-4">
                        {(runState.state?.history_trace ?? []).map((entry, idx) => {
                          const from = draft.nodes?.find((n) => n.node_id === entry.from_node)?.name
                          const to = draft.nodes?.find((n) => n.node_id === entry.to_node)?.name
                          const ts = entry.timestamp
                            ? entry.timestamp.split('T')[1]?.slice(0, 12) ?? entry.timestamp
                            : '--'
                          return (
                            <div key={`${entry.timestamp ?? idx}-${idx}`} className="flex gap-2.5">
                              <span className="w-1.5 h-1.5 rounded-full bg-text-muted flex-shrink-0 mt-1.5" />
                              <div className="min-w-0">
                                <div className="font-mono text-caption text-text-muted">{ts}</div>
                                <div className="mt-0.5 text-[14px] text-text-primary flex items-center gap-1">
                                  {from ? (
                                    <>
                                      <span className="truncate">{from}</span>
                                      <CornerDownRight
                                        className="w-3 h-3 text-text-muted flex-shrink-0"
                                        aria-hidden="true"
                                      />
                                    </>
                                  ) : null}
                                  <span className="truncate">{to ?? '—'}</span>
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </>
              )}
            </section>

            {topologyIssues.length > 0 && (
              <div className="rounded-[10px] border border-warning/40 bg-warning/5 px-5 py-4">
                <p className="text-body-sm font-semibold text-text-primary flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 text-warning" aria-hidden="true" />
                  拓扑校验未通过
                </p>
                <ul className="mt-1.5 space-y-1">
                  {topologyIssues.map((issue) => (
                    <li key={issue} className="text-caption text-text-tertiary">
                      · {issue}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        {/* ============ 三重安全护栏抽屉 ============ */}
        <Drawer
          open={guardrailOpen}
          onClose={() => setGuardrailOpen(false)}
          title="三重安全护栏"
          description="规程级履约铁律：修改后需点击「保存修订」写回企业规程库"
          width={520}
          actions={
            <button
              type="button"
              onClick={() => {
                setGuardrailOpen(false)
                void handleSave()
              }}
              disabled={!draft || saving}
              className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" aria-hidden="true" />
              保存护栏
            </button>
          }
        >
          <div className="divide-y divide-border-subtle border-y border-border-subtle">
            {GUARDRAIL_META.map((g, idx) => (
              <div key={g.key} className="py-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-caption text-text-muted">
                        G{idx + 1}
                      </span>
                      <span className="text-h4 font-semibold text-text-primary">{g.label}</span>
                    </div>
                    <p className="mt-2 text-body-sm leading-6 text-text-tertiary">{g.rule}</p>
                  </div>
                  <MiniToggle
                    on={Boolean(guardrails?.[g.key])}
                    onToggle={() => patchGuardrail(g.key, !guardrails?.[g.key])}
                  />
                </div>
              </div>
            ))}
          </div>
          <p className="mt-5 text-caption text-text-muted">
            护栏在 Flow-Core 引擎内以硬约束执行：任一铁律关闭都会放宽履约下限，修改记录随规程版本一并留痕。
          </p>
        </Drawer>

        {/* ============ 经验提炼模态 ============ */}
        <ModalShell
          open={synthOpen}
          onClose={() => setSynthOpen(false)}
          labelledBy="synth-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          panelClassName="w-full max-w-xl rounded-[10px] border border-border-default bg-surface shadow-lift"
        >
          <div className="p-6 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="synth-title" className="text-h4 font-semibold text-text-primary">
                  自然语言经验提炼
                </h2>
                <p className="mt-1 text-body-sm text-text-tertiary">
                  粘贴专家经验、业务手册或操作规程，逆向编译为标准 FlowCard 状态机。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSynthOpen(false)}
                aria-label="关闭"
                className="w-8 h-8 flex items-center justify-center rounded-md text-text-tertiary hover:bg-elevated transition-colors"
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
            </div>

            <div>
              <label htmlFor="synth-name" className="block text-body-sm text-text-secondary mb-1.5">
                规程名称（可选）
              </label>
              <input
                id="synth-name"
                type="text"
                value={synthName}
                onChange={(e) => setSynthName(e.target.value)}
                placeholder="例如：大客户售后退款标准 SOP"
                className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500"
              />
            </div>

            <div>
              <label htmlFor="synth-text" className="block text-body-sm text-text-secondary mb-1.5">
                规程原文
              </label>
              <textarea
                id="synth-text"
                rows={7}
                value={synthText}
                onChange={(e) => setSynthText(e.target.value)}
                placeholder="第一步，询问客户订单号与退款原因；第二步，若金额超过 1000 元需主管审批；第三步，调用退款接口打款并向客户发送完成通知。"
                className="w-full p-3 rounded-md border border-border-default bg-surface text-[14px] leading-6 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500 resize-y"
              />
            </div>

            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setSynthOpen(false)}
                className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleSynthesize}
                disabled={synthesizing || !synthText.trim()}
                className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
              >
                <FileText className="w-3.5 h-3.5" aria-hidden="true" />
                {synthesizing ? '编译中…' : '编译为 FlowCard'}
              </button>
            </div>
          </div>
        </ModalShell>
        </div>
      </div>
    </Layout>
  )
}

/** 检视器字段行：上 13px 弱化标签 + 下 14px 正文，发丝线分隔。 */
function FactRow({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="py-3">
      <div className="text-body-sm text-text-tertiary">{label}</div>
      <div
        className={`mt-1 text-[14px] leading-6 text-text-primary break-words ${mono ? 'font-mono text-[13px]' : ''}`}
      >
        {value}
      </div>
    </div>
  )
}
