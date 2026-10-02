/**
 * CounterfactualArena — 双盲反事实影子推演台（AutoTeams 5.0 战役 4）。
 *
 * 设计基线：AutoTeams UI 重设计蓝图 v2「克制 · 编辑式」
 * - 主体为「差分对比瀑布」：中轴发丝线 + 1-2px 细条，右侧 mono 读数
 * - 状态一律 6px 圆点 + 13px 文字，无斑马纹、无渐变、无阴影
 * - 全屏仅 1 枚实心主按钮（提交反事实推演）
 *
 * 数据来源严格对齐后端 /api/v1/shadow/counterfactual（见 api/counterfactualShadow.ts）：
 * 差分裁决、净收益折现、免干预转正进度均取接口结算返回值，页面不本地复算口径。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Scale, ShieldAlert, Sparkles, TrendingUp, RotateCcw, Activity } from 'lucide-react'
import Layout from '@/components/Layout'
import { ApiErrorState } from '@/components/ApiErrorState'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import {
  createCounterfactualSession,
  getCounterfactualSession,
  getPromotionGate,
  listCounterfactualSessions,
  resetPromotionGate,
} from '@/api/counterfactualShadow'
import type {
  CounterfactualDiff,
  CounterfactualSession,
  DiffAssessment,
  DiffDimension,
  PromotionGate,
} from '@/types'

/** 战役 4 规格：连续 50 笔达标即免干预自动转正 */
const PROMOTION_STREAK = 50

const DIMENSION_LABEL: Record<DiffDimension, string> = {
  semantics: '语义对齐',
  latency: '时效差',
  cost: '成本差',
  risk: '风险差',
}

const ASSESSMENT_META: Record<DiffAssessment, { label: string; dot: string; text: string }> = {
  better: { label: '数字员工占优', dot: 'bg-success', text: 'text-success' },
  parity: { label: '持平', dot: 'bg-text-muted', text: 'text-text-tertiary' },
  worse: { label: '数字员工落后', dot: 'bg-warning', text: 'text-warning' },
}

/** 差分读数格式：正数带 + 号，负数带 − 号。 */
function signed(value: number, digits = 2): string {
  const fixed = Math.abs(value).toFixed(digits)
  if (value > 0) return `+${fixed}`
  if (value < 0) return `−${fixed}`
  return fixed
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

/**
 * 差分瀑布单行：中轴为 0，正数向右（数字员工占优），负数向左。
 * 条宽按 |delta_score| 线性映射到半幅，1.0 即铺满半幅。
 */
function WaterfallRow({ diff }: { diff: CounterfactualDiff }) {
  const meta = ASSESSMENT_META[diff.assessment] ?? ASSESSMENT_META.parity
  const half = Math.min(1, Math.abs(diff.delta_score)) * 50
  const positive = diff.delta_score > 0
  return (
    <div className="grid grid-cols-[minmax(0,320px)_minmax(0,1fr)_92px] items-center gap-4 h-[52px] border-b border-border-subtle last:border-b-0">
      <div className="min-w-0">
        <div className="text-[14px] text-text-primary truncate">{DIMENSION_LABEL[diff.dimension]}</div>
        <div className="text-caption text-text-muted truncate">{diff.note}</div>
      </div>

      <div className="relative h-full">
        <div className="absolute inset-y-0 left-1/2 w-px bg-border-default" aria-hidden="true" />
        <div className="absolute inset-y-[9px] left-1/2 w-px bg-border-subtle" aria-hidden="true" />
        {half > 0 && (
          <div
            className={`absolute top-1/2 -translate-y-1/2 h-[2px] ${positive ? 'bg-brand-500' : 'bg-warning'}`}
            style={
              positive
                ? { left: '50%', width: `${half}%` }
                : { right: '50%', width: `${half}%` }
            }
            aria-hidden="true"
          />
        )}
      </div>

      <div className="text-right">
        <div className={`font-mono text-body-sm font-semibold ${positive ? 'text-brand-500' : diff.delta_score < 0 ? 'text-warning' : 'text-text-tertiary'}`}>
          {signed(diff.delta_score)}
        </div>
        <div className="text-caption text-text-muted">{meta.label}</div>
      </div>
    </div>
  )
}

/** 因果得失测算图谱：对照 / 实验两侧读数对照。 */
function CausalLedger({ session }: { session: CounterfactualSession }) {
  const rows: Array<{ label: string; control: string; treatment: string; gain: string }> = [
    {
      label: '处置耗时',
      control: `${(session.human_duration_seconds ?? 0).toFixed(0)}s`,
      treatment: `${(session.agent_duration_seconds ?? 0).toFixed(0)}s`,
      gain: `节省 ${signed(session.time_saving_seconds, 1)}s`,
    },
    {
      label: '处置成本',
      control: `${session.human_cost_yuan?.toFixed(2) ?? '0.00'} 元`,
      treatment: `${session.agent_cost_yuan?.toFixed(2) ?? '0.00'} 元`,
      gain: `增量 ${signed(session.cost_delta_yuan)} 元`,
    },
    {
      label: '语义对齐度',
      control: '基准 1.00',
      treatment: (session.semantic_alignment_score ?? 0).toFixed(2),
      gain: '双盲对称打分',
    },
    {
      label: '护栏突破',
      control: '0 次',
      treatment: `${session.guardrail_breach_count ?? 0} 次`,
      gain: session.guardrail_breach_count > 0 ? '硬否决' : '无暴露',
    },
  ]
  return (
    <div className="rounded-[2px] border border-border-default bg-surface overflow-x-auto">
      <table className="w-full text-left border-collapse min-w-[640px]">
        <thead>
          <tr className="h-11 border-b border-border-default text-[12px] text-text-tertiary">
            <th className="pl-6 pr-4 text-caption font-medium uppercase tracking-wider w-48">度量</th>
            <th className="px-4 text-caption font-medium uppercase tracking-wider w-40">对照组 · 真人</th>
            <th className="px-4 text-caption font-medium uppercase tracking-wider w-40">实验组 · 数字员工</th>
            <th className="pr-6 pl-4 text-caption font-medium uppercase tracking-wider">因果得失</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {rows.map((row) => (
            <tr key={row.label} className="h-[46px]">
              <td className="pl-6 pr-4 text-body-sm text-text-secondary">{row.label}</td>
              <td className="px-4 font-mono text-body-sm text-text-tertiary">{row.control}</td>
              <td className="px-4 font-mono text-body-sm text-text-primary">{row.treatment}</td>
              <td className="pr-6 pl-4 text-body-sm text-text-tertiary">{row.gain}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function CounterfactualArena() {
  const enterpriseId = useEnterpriseId()

  const [sessions, setSessions] = useState<CounterfactualSession[]>([])
  const [gate, setGate] = useState<PromotionGate | null>(null)
  const [selectedId, setSelectedId] = useState<string>('')
  /** 列表端点不返回差分明细，瀑布需按选中项拉取详情 */
  const [detail, setDetail] = useState<CounterfactualSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>('')
  const [submitting, setSubmitting] = useState(false)

  const [badge, setBadge] = useState('生产计划员·A')
  const [scenario, setScenario] = useState('客户催单，库存与交期需在 5 分钟内核清')
  const [humanAction, setHumanAction] = useState('先核库存再回复客户')
  const [agentProposal, setAgentProposal] = useState('先核库存再回复客户')
  const [humanSeconds, setHumanSeconds] = useState(300)
  const [agentSeconds, setAgentSeconds] = useState(20)
  const [humanCost, setHumanCost] = useState(30)
  const [agentCost, setAgentCost] = useState(2)
  const [breaches, setBreaches] = useState(0)

  const badges = useMemo(() => {
    const seen = new Set(sessions.map((s) => s.employee_badge).filter(Boolean))
    if (badge) seen.add(badge)
    return Array.from(seen)
  }, [sessions, badge])

  const selected = useMemo(() => {
    if (detail && (!selectedId || detail.session_id === selectedId)) return detail
    return sessions.find((s) => s.session_id === selectedId) ?? sessions[0] ?? null
  }, [detail, sessions, selectedId])

  // 只依赖选中项的 id：直接依赖 `selected` 对象会在 setDetail 写入新对象后
  // 再次触发本 effect，形成「拉取详情 → setDetail → 再拉取」的自激循环。
  const selectedSessionId = selected?.session_id ?? null

  useEffect(() => {
    if (!selectedSessionId) {
      setDetail(null)
      return
    }
    let cancelled = false
    getCounterfactualSession(selectedSessionId)
      .then((full) => {
        if (!cancelled) setDetail(full)
      })
      .catch(() => {
        if (!cancelled) setDetail(null)
      })
    return () => {
      cancelled = true
    }
  }, [selectedSessionId])

  const load = useCallback(async () => {
    if (!enterpriseId) {
      setLoading(false)
      setError('当前账号未绑定企业，无法加载反事实推演数据')
      return
    }
    setLoading(true)
    setError('')
    try {
      const page = await listCounterfactualSessions(enterpriseId, { limit: 50 })
      setSessions(page.items ?? [])
      if (badge) {
        setGate(await getPromotionGate(enterpriseId, badge))
      }
    } catch (err) {
      setError((err as Error).message || '反事实推演数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [enterpriseId, badge])

  useEffect(() => {
    void load()
  }, [load])

  const handleSubmit = async () => {
    if (!scenario.trim() || !humanAction.trim() || !agentProposal.trim()) {
      toast.error('场景与两侧处置快照均不能为空')
      return
    }
    setSubmitting(true)
    try {
      const created = await createCounterfactualSession({
        enterprise_id: enterpriseId,
        employee_badge: badge.trim(),
        scenario: scenario.trim(),
        human_action_snapshot: humanAction.trim(),
        agent_proposal_snapshot: agentProposal.trim(),
        human_duration_seconds: Number(humanSeconds) || 0,
        agent_duration_seconds: Number(agentSeconds) || 0,
        human_cost_yuan: Number(humanCost) || 0,
        agent_cost_yuan: Number(agentCost) || 0,
        guardrail_breach_count: Number(breaches) || 0,
      })
      setSelectedId(created.session_id)
      toast.success(
        created.is_auto_promoted
          ? `差分达标，已免干预转正（第 ${created.consecutive_pass_streak} 笔）`
          : created.is_qualified
            ? `差分达标，连续记录 ${created.consecutive_pass_streak}/${PROMOTION_STREAK}`
            : `差分未达标：${created.verdict?.reasons?.[0] ?? '存在不达标维度'}`,
      )
      await load()
    } catch (err) {
      toast.error(`推演提交失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setSubmitting(false)
    }
  }

  const handleResetGate = async () => {
    try {
      const result = await resetPromotionGate(enterpriseId, badge)
      toast.success(`准入看板已重置，清空 ${result.reset} 笔转正记录`)
      await load()
    } catch (err) {
      toast.error(`重置失败：${(err as Error).message || '未知错误'}`)
    }
  }

  const streakPct = gate ? Math.min(100, (gate.consecutive_pass_streak / gate.required_streak) * 100) : 0
  const qualifiedRate =
    gate && gate.total_sessions > 0 ? (gate.qualified_sessions / gate.total_sessions) * 100 : 0

  return (
    <Layout>
      <div className="at-paper min-h-full w-full px-6 md:px-10 py-8">
        <div className="w-full max-w-[1280px] mx-auto space-y-8">
          {/* ============ 1. 编辑式页头 ============ */}
          <header className="flex items-start justify-between gap-6 flex-wrap">
            <div className="min-w-0">
              <nav className="flex items-center gap-2 text-caption text-text-tertiary">
                <span>进化</span>
                <span className="text-text-muted">/</span>
                <span>影子评估</span>
                <span className="text-text-muted">/</span>
                <span className="text-text-primary">双盲反事实</span>
              </nav>
              <h1 className="mt-2 text-[30px] leading-9 font-bold tracking-tight text-text-primary">
                反事实推演台
              </h1>
              <div className="mt-2.5 flex items-center flex-wrap gap-2.5 text-body-sm text-text-tertiary">
                <span className="text-text-primary font-medium">
                  免干预转正门槛 {PROMOTION_STREAK} 笔连续达标
                </span>
                <span className="w-1 h-1 rounded-full bg-border-default" />
                <span className="font-mono text-caption">硬否决：护栏突破 &gt; 0</span>
                <span className="w-1 h-1 rounded-full bg-border-default" />
                <span className="inline-flex items-center gap-1.5">
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${gate?.is_auto_promoted ? 'bg-success' : 'bg-text-muted'}`}
                    aria-hidden="true"
                  />
                  {badge || '未选员工'}：{gate?.is_auto_promoted ? '已免干预转正' : '推演中'}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0 pt-1">
              <button
                type="button"
                onClick={() => void load()}
                className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
              >
                <Activity className="w-3.5 h-3.5" aria-hidden="true" />
                刷新
              </button>
              <button
                type="button"
                onClick={() => void handleSubmit()}
                disabled={submitting || !enterpriseId}
                className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
              >
                <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
                {submitting ? '推演中…' : '提交反事实推演'}
              </button>
            </div>
          </header>

          {error && <ApiErrorState message={error} onRetry={() => void load()} />}

          {/* ============ 2. 推演录入 ============ */}
          <section className="space-y-4">
            <div className="flex items-baseline justify-between gap-4 pb-3 border-b border-border-subtle flex-wrap">
              <div className="flex items-baseline gap-3">
                <h2 className="text-h4 font-semibold text-text-primary">推演录入</h2>
                <span className="text-caption text-text-tertiary">
                  两侧处置快照将剥离身份标记后进入对称打分
                </span>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <label className="block">
                <span className="block text-caption text-text-tertiary mb-1.5">业务场景</span>
                <input
                  value={scenario}
                  onChange={(e) => setScenario(e.target.value)}
                  className="w-full h-9 px-2.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                />
              </label>
              <label className="block">
                <span className="block text-caption text-text-tertiary mb-1.5">数字员工名牌</span>
                <select
                  value={badge}
                  onChange={(e) => setBadge(e.target.value)}
                  className="w-full h-9 px-2.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                >
                  {badges.map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="block text-caption text-text-tertiary mb-1.5">
                  对照组 · 真人实际处置
                </span>
                <textarea
                  value={humanAction}
                  onChange={(e) => setHumanAction(e.target.value)}
                  rows={3}
                  className="w-full px-2.5 py-2 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                />
              </label>
              <label className="block">
                <span className="block text-caption text-text-tertiary mb-1.5">
                  实验组 · 数字员工反事实提案
                </span>
                <textarea
                  value={agentProposal}
                  onChange={(e) => setAgentProposal(e.target.value)}
                  rows={3}
                  className="w-full px-2.5 py-2 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                />
              </label>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {(
                [
                  { label: '真人耗时 (s)', value: humanSeconds, set: setHumanSeconds },
                  { label: '数字员工耗时 (s)', value: agentSeconds, set: setAgentSeconds },
                  { label: '真人成本 (元)', value: humanCost, set: setHumanCost },
                  { label: '数字员工成本 (元)', value: agentCost, set: setAgentCost },
                  { label: '护栏突破 (次)', value: breaches, set: setBreaches },
                ] as const
              ).map((field) => (
                <label key={field.label} className="block">
                  <span className="block text-caption text-text-tertiary mb-1.5">{field.label}</span>
                  <input
                    type="number"
                    min={0}
                    value={field.value}
                    onChange={(e) => field.set(Number(e.target.value))}
                    className="w-full h-9 px-2.5 rounded-md border border-border-default bg-surface font-mono text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                  />
                </label>
              ))}
            </div>
          </section>

          {/* ============ 3. 双盲反事实差分对比瀑布 ============ */}
          <section className="space-y-4">
            <div className="flex items-baseline justify-between gap-4 pb-3 border-b border-border-subtle flex-wrap">
              <div className="flex items-baseline gap-3">
                <h2 className="text-h4 font-semibold text-text-primary">差分对比瀑布</h2>
                <span className="text-caption text-text-tertiary">
                  {selected ? `${selected.scenario}` : '尚无推演记录'}
                </span>
              </div>
              {selected && (
                <span className="inline-flex items-center gap-2 text-caption text-text-tertiary">
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${selected.is_qualified ? 'bg-success' : 'bg-warning'}`}
                    aria-hidden="true"
                  />
                  {selected.is_qualified ? '本笔达标' : '本笔不达标'} ·{' '}
                  <span className="font-mono">{formatDateTime(selected.created_at)}</span>
                </span>
              )}
            </div>

            {loading ? (
              <p className="py-10 text-center text-body-sm text-text-muted">推演数据加载中…</p>
            ) : !selected ? (
              <p className="py-10 text-center text-body-sm text-text-muted">
                尚无反事实推演记录，请先提交一笔推演
              </p>
            ) : (
              <>
                <div className="rounded-[2px] border border-border-default bg-surface px-6">
                  {selected.diffs.length > 0 ? (
                    selected.diffs.map((diff) => (
                      <WaterfallRow key={diff.diff_id} diff={diff} />
                    ))
                  ) : (
                    <p className="py-8 text-center text-body-sm text-text-muted">
                      该笔推演未返回差分明细
                    </p>
                  )}
                </div>
                <p className="text-caption text-text-muted">
                  差分读数由后端反事实引擎结算，中轴为 0，向右表示数字员工占优；语义维度为双盲对称打分结果，与两侧身份无关。
                </p>
              </>
            )}
          </section>

          {/* ============ 4. 因果得失测算图谱 ============ */}
          {selected && (
            <section className="space-y-4">
              <div className="flex items-baseline justify-between gap-4 pb-3 border-b border-border-subtle flex-wrap">
                <div className="flex items-baseline gap-3">
                  <h2 className="text-h4 font-semibold text-text-primary">因果得失测算</h2>
                  <span className="text-caption text-text-tertiary">对照组与实验组逐项对照</span>
                </div>
                <span className="inline-flex items-center gap-1.5 text-body-sm font-semibold text-text-primary">
                  <Scale className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
                  预期净收益 {signed(selected.expected_net_benefit_yuan)} 元
                </span>
              </div>
              <CausalLedger session={selected} />
              {selected.verdict && selected.verdict.reasons.length > 0 && (
                <ul className="space-y-1.5">
                  {selected.verdict.reasons.map((reason) => (
                    <li key={reason} className="flex items-start gap-2 text-body-sm text-warning">
                      <ShieldAlert className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" aria-hidden="true" />
                      <span>{reason}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {/* ============ 5. 免干预转正准入看板 ============ */}
          <section className="space-y-4">
            <div className="flex items-center justify-between gap-4 pb-3 border-b border-border-subtle flex-wrap">
              <h2 className="text-h4 font-semibold text-text-primary">
                免干预转正准入
                <span className="ml-2 text-body-sm font-normal text-text-tertiary">
                  连续达标 {gate?.consecutive_pass_streak ?? 0} / {gate?.required_streak ?? PROMOTION_STREAK} 笔
                </span>
              </h2>
              <button
                type="button"
                onClick={() => void handleResetGate()}
                disabled={!enterpriseId}
                className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors disabled:opacity-50"
              >
                <RotateCcw className="w-3.5 h-3.5" aria-hidden="true" />
                重置准入
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
              <div className="space-y-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-body-sm text-text-secondary">连续达标进度</span>
                  <span className="font-mono text-body-sm font-semibold text-text-primary">
                    {streakPct.toFixed(1)}%
                  </span>
                </div>
                <div className="h-[2px] rounded-full bg-elevated overflow-hidden">
                  <div
                    className={`block h-full ${gate?.is_auto_promoted ? 'bg-success' : 'bg-brand-500'}`}
                    style={{ width: `${streakPct}%` }}
                  />
                </div>
                <p className="text-caption text-text-muted">
                  {gate?.is_auto_promoted
                    ? '已免干预转正，后续推演仅做观测'
                    : `还需 ${gate?.remaining_to_promotion ?? PROMOTION_STREAK} 笔连续达标即自动转正`}
                </p>
              </div>

              <div className="space-y-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-body-sm text-text-secondary">历史达标率</span>
                  <span className="font-mono text-body-sm font-semibold text-text-primary">
                    {qualifiedRate.toFixed(1)}%
                  </span>
                </div>
                <div className="h-[2px] rounded-full bg-elevated overflow-hidden">
                  <div className="block h-full bg-text-primary" style={{ width: `${qualifiedRate}%` }} />
                </div>
                <p className="text-caption text-text-muted">
                  共 {gate?.total_sessions ?? 0} 笔推演，{gate?.qualified_sessions ?? 0} 笔达标
                </p>
              </div>

              <div className="space-y-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-body-sm text-text-secondary">语义对齐阈值</span>
                  <span className="font-mono text-body-sm font-semibold text-text-primary">
                    {(gate?.semantic_alignment_threshold ?? 0).toFixed(2)}
                  </span>
                </div>
                <div className="h-[2px] rounded-full bg-elevated overflow-hidden">
                  <div
                    className="block h-full bg-brand-500"
                    style={{ width: `${((gate?.semantic_alignment_threshold ?? 0) * 100).toFixed(1)}%` }}
                  />
                </div>
                <p className="text-caption text-text-muted">
                  成本增量上限 {gate?.max_cost_delta_yuan ?? 0} 元 · 护栏突破上限{' '}
                  {gate?.max_guardrail_breaches ?? 0} 次
                </p>
              </div>
            </div>
          </section>

          {/* ============ 6. 推演记录 ============ */}
          <section className="space-y-4">
            <div className="flex items-baseline justify-between gap-4 pb-3 border-b border-border-subtle flex-wrap">
              <h2 className="text-h4 font-semibold text-text-primary">
                推演记录
                <span className="ml-2 text-body-sm font-normal text-text-tertiary">
                  共 {sessions.length} 笔
                </span>
              </h2>
              <span className="inline-flex items-center gap-1.5 text-caption text-text-tertiary">
                <TrendingUp className="w-3.5 h-3.5" aria-hidden="true" />
                达标样本不中断累计，任一笔不达标即归零
              </span>
            </div>

            <div className="rounded-[2px] border border-border-default bg-surface overflow-x-auto">
              <table className="w-full text-left border-collapse min-w-[760px]">
                <thead>
                  <tr className="h-11 border-b border-border-default text-[12px] text-text-tertiary">
                    <th className="pl-6 pr-4 text-caption font-medium uppercase tracking-wider">推演时刻</th>
                    <th className="px-4 text-caption font-medium uppercase tracking-wider">数字员工</th>
                    <th className="px-4 text-caption font-medium uppercase tracking-wider w-28 text-right">对齐度</th>
                    <th className="px-4 text-caption font-medium uppercase tracking-wider w-28 text-right">净收益</th>
                    <th className="px-4 text-caption font-medium uppercase tracking-wider w-24 text-right">连续</th>
                    <th className="pr-6 pl-4 text-caption font-medium uppercase tracking-wider w-28">裁决</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {sessions.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-6 py-10 text-center">
                        <p className="text-body-sm text-text-muted">暂无推演记录</p>
                        <p className="text-caption text-text-tertiary mt-1.5 max-w-md mx-auto">
                          在上方「推演录入」填写同一场景下的真人处置与数字员工提案，
                          点击页头「提交反事实推演」即可生成差分裁决并累计免干预转正连击。
                        </p>
                      </td>
                    </tr>
                  ) : (
                    sessions.map((row) => (
                      <tr
                        key={row.session_id}
                        onClick={() => setSelectedId(row.session_id)}
                        className={`h-[48px] cursor-pointer transition-colors hover:bg-elevated/40 ${
                          row.session_id === selected?.session_id ? 'bg-elevated/60' : ''
                        }`}
                      >
                        <td className="pl-6 pr-4 font-mono text-caption text-text-tertiary">
                          {formatDateTime(row.created_at)}
                        </td>
                        <td className="px-4 text-body-sm text-text-primary truncate max-w-[280px]">
                          {row.employee_badge}
                        </td>
                        <td className="px-4 text-right font-mono text-body-sm text-text-primary">
                          {row.semantic_alignment_score.toFixed(2)}
                        </td>
                        <td className="px-4 text-right font-mono text-body-sm text-text-primary">
                          {signed(row.expected_net_benefit_yuan)} 元
                        </td>
                        <td className="px-4 text-right font-mono text-body-sm text-text-tertiary">
                          {row.consecutive_pass_streak}
                        </td>
                        <td className="pr-6 pl-4">
                          <span className="inline-flex items-center gap-2 text-body-sm whitespace-nowrap">
                            <span
                              className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                                row.is_qualified ? 'bg-success' : 'bg-warning'
                              }`}
                              aria-hidden="true"
                            />
                            <span className={row.is_qualified ? 'text-text-secondary' : 'text-warning'}>
                              {row.is_auto_promoted ? '免干预转正' : row.is_qualified ? '达标' : '不达标'}
                            </span>
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </Layout>
  )
}
