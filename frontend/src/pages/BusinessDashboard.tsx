import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  AlertCircle, RefreshCw, Download, ChevronRight,
  TrendingUp, Clock, Coins, Percent, ShieldCheck,
  MessageSquare, Database, Star,
} from 'lucide-react'
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, Legend, Cell,
} from 'recharts'
import Layout from '@/components/Layout'
import { Button } from '@/components/ui/Button'
import { PageHeader } from '@/components/ui/PageHeader'
import { Spinner } from '@/components/ui/Spinner'
import { getBusinessMetrics, type BusinessMetrics } from '@/api/metrics'

// ============================================================
// recharts 图表主题（与 LoopDashboard 保持一致，使用 CSS 变量自适应明暗）
// ============================================================
const chartTooltip = {
  contentStyle: {
    backgroundColor: 'var(--bg-elevated)',
    border: '1px solid var(--border-default)',
    borderRadius: '8px',
    color: 'var(--text-primary)',
  },
  labelStyle: { color: 'var(--text-secondary)' },
  itemStyle: { color: 'var(--text-primary)' },
}
const chartAxis = { stroke: 'var(--text-tertiary)', fontSize: 12 }
const chartGrid = { stroke: 'var(--border-subtle)', strokeDasharray: '3 3' }

const CHART_PRIMARY = '#1E3A5F'
const CHART_SECONDARY = '#4A90A4'
const CHART_ACCENT = '#E6A23C'

type TimeRange = '7d' | '30d' | '90d'

const timeRanges: { key: TimeRange; label: string; days: number }[] = [
  { key: '7d', label: '7天', days: 7 },
  { key: '30d', label: '30天', days: 30 },
  { key: '90d', label: '90天', days: 90 },
]

// ============================================================
// 辅助函数
// ============================================================

/** 格式化货币（元），大额自动转万 */
function formatYuan(value: number): string {
  if (value >= 10000) return `${(value / 10000).toFixed(2)} 万`
  if (value >= 1) return value.toFixed(2)
  return value.toFixed(4)
}

/** 格式化百分比（0-1 → x%） */
function formatPct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

export default function BusinessDashboard() {
  const [metrics, setMetrics] = useState<BusinessMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [timeRange, setTimeRange] = useState<TimeRange>('30d')

  const loadMetrics = useCallback(async (range: TimeRange) => {
    const days = timeRanges.find((r) => r.key === range)!.days
    try {
      setLoading(true)
      setError(null)
      const data = await getBusinessMetrics(days)
      setMetrics(data)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误'
      setError(`加载业务指标失败: ${msg}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadMetrics(timeRange)
  }, [loadMetrics, timeRange])

  // 成本对比数据：启用 AI 前（人工基线）vs 启用 AI 后（实际成本）
  const costComparisonData = useMemo(() => {
    if (!metrics) return []
    return [
      { name: '启用 AI 前（人工基线）', cost: metrics.cost.pre_ai_cost_yuan },
      { name: '启用 AI 后（实际成本）', cost: metrics.cost.total_cost_yuan },
    ]
  }, [metrics])

  // 趋势数据：协作量 + 满意度合并到同一按日序列
  const trendData = useMemo(() => {
    if (!metrics) return []
    const counts = metrics.trends.daily_counts
    const satMap = new Map(
      metrics.trends.satisfaction_trend.map((p) => [p.date, p.score]),
    )
    return counts.map((p) => ({
      date: p.date,
      协作量: p.count,
      满意度: satMap.get(p.date) ?? 0,
    }))
  }, [metrics])

  const timeRangeLabel = timeRanges.find((r) => r.key === timeRange)!.label

  // 由真实聚合数据推导的四个核心指标（供卡片悬浮说明与展示）
  const roiCards = useMemo(() => {
    if (!metrics) return []
    const {
      cost,
      efficiency,
      summary,
    } = metrics
    const costSaved = cost.pre_ai_cost_yuan - cost.total_cost_yuan
    const roiX = cost.total_cost_yuan > 0 ? costSaved / cost.total_cost_yuan : 0
    const humanMinutes = efficiency.human_compare_minutes || 0
    return [
      {
        key: 'output',
        icon: MessageSquare,
        label: '业务产出',
        value: summary.resolved_count.toLocaleString(),
        unit: '单',
        subtitle: `已解决协作 ${summary.total_conversations ? Math.round((summary.resolved_count / summary.total_conversations) * 100) : 0}%`,
        highlight: false,
        tooltip: `近 ${metrics.period.range_days} 天内已解决的真实协作数（${summary.total_conversations} 次协作中 ${summary.resolved_count} 次拿到正向结果）。口径：会话末尾满意度非负向即视为已解决，由 conversations 表按日聚合。`,
      },
      {
        key: 'cost',
        icon: Coins,
        label: '成本节约',
        value: formatYuan(Math.max(costSaved, 0)),
        unit: '元',
        subtitle: `较人工基线 ${formatYuan(cost.pre_ai_cost_yuan)} 元`,
        highlight: false,
        tooltip: `人工基线成本 ${formatYuan(cost.pre_ai_cost_yuan)} 元（=${summary.resolved_count} 已解决 × ${humanMinutes} 分钟/问题 × 60 元/时）减去实际 AI 成本 ${formatYuan(cost.total_cost_yuan)} 元（输出 token ${cost.cost_per_1k_tokens_yuan} 元/千 + ${cost.tool_call_count} 次工具调用 + 知识检索）。`,
      },
      {
        key: 'hours',
        icon: Clock,
        label: '替代人时',
        value: efficiency.hours_saved.toFixed(1),
        unit: '小时',
        subtitle: `相当于 ${Math.round(efficiency.hours_saved / 8)} 个工作日`,
        highlight: false,
        tooltip: `已解决协作数 ×（人工耗时 ${humanMinutes} 分钟/问题 − AI 实际耗时）。AI 并行处理多路请求，单问题耗时远低于人工，累计省下的工时即替代人时。`,
      },
      {
        key: 'roi',
        icon: TrendingUp,
        label: '综合 ROI',
        value: `${roiX.toFixed(1)}x`,
        unit: '倍',
        subtitle: `成本降幅 ${formatPct(cost.cost_reduction_pct)}`,
        highlight: true,
        tooltip: `每投入 1 元 AI 成本带来的成本节约倍数 = 成本节约 ${formatYuan(costSaved)} 元 ÷ AI 实际成本 ${formatYuan(cost.total_cost_yuan)} 元。成本降幅 ${formatPct(cost.cost_reduction_pct)} 为相对人工基线的百分比。`,
      },
    ]

  }, [metrics])

  // 加载态：复用统一 Spinner
  if (loading && !metrics) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <Spinner size="xl" className="text-brand-500" />
        </div>
      </Layout>
    )
  }

  // 错误态：统一错误组件 + 重试按钮（与 LoopDashboard 一致）
  if (error && !metrics) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="text-center max-w-md space-y-4">
            <div className="w-12 h-12 mx-auto rounded-full bg-error/10 flex items-center justify-center">
              <AlertCircle className="w-6 h-6 text-error" />
            </div>
            <h2 className="text-h3 text-text-primary">加载失败</h2>
            <p className="text-body text-text-secondary">{error}</p>
            <Button onClick={() => loadMetrics(timeRange)} variant="primary">
              <RefreshCw className="w-4 h-4" />
              重试
            </Button>
          </div>
        </div>
      </Layout>
    )
  }

  if (!metrics) return null

  return (
    <Layout>
      <div className="max-w-[1600px] mx-auto space-y-8">
        {/* ============================================================
            1. 页面头部：面包屑 + 衬线标题 + 时间范围 + 月报导出
            ============================================================ */}
        <PageHeader
          title="业务效果仪表盘"
          subtitle="面向企业决策者的 ROI / 成本 / 覆盖 / 效率指标，衡量智能体带来的业务价值"
          actions={
            <div className="flex items-center gap-3">
              <div className="flex gap-1">
                {timeRanges.map((r) => (
                  <button
                    key={r.key}
                    onClick={() => setTimeRange(r.key)}
                    disabled={loading}
                    className={`px-3 h-8 text-sm rounded-md font-medium border transition-colors ${
                      timeRange === r.key
                        ? 'bg-brand-500 text-white border-brand-500'
                        : 'bg-surface text-text-tertiary border-border-default hover:text-text-primary hover:bg-elevated'
                    } ${loading ? 'opacity-60 cursor-not-allowed' : ''}`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
              {/* 月报导出：window.print()，配合打印样式 */}
              <Button variant="outline" size="sm" onClick={() => window.print()}>
                <Download className="w-4 h-4" />
                导出月报
              </Button>
            </div>
          }
        >
          <nav className="flex items-center gap-1.5 text-sm text-text-tertiary mb-1">
            <Link to="/" className="hover:text-text-primary transition-colors">
              控制台
            </Link>
            <ChevronRight className="w-3.5 h-3.5" />
            <span className="text-text-secondary">业务效果</span>
          </nav>
        </PageHeader>

        {/* 数据口径提示（打印时隐藏） */}
        <div className="print-hidden flex items-start gap-3 p-3 rounded-lg bg-info/5 border border-info/20">
          <ShieldCheck className="w-4 h-4 text-info flex-shrink-0 mt-0.5" />
          <p className="text-xs text-text-secondary leading-relaxed">
            数据口径：单次协作成本由「输出 token（
            <span className="font-mono mx-1">{metrics.cost.cost_per_1k_tokens_yuan}</span>
            元/千 token）＋ 推理/思考 ＋ 工具调用（{metrics.cost.tool_call_count} 次）＋ 知识检索」估算；
            月估算按单个 AI 员工约 200 次协作/月折算。ROI 基线为人工处理同等问题的人力成本（约
            <span className="font-mono mx-1">{metrics.efficiency.human_compare_minutes}</span>
            分钟/问题，60 元/小时）。指标基于近
            <span className="font-mono mx-1">{metrics.period.range_days}</span>
            天真实协作与 RAG 评估聚合。
          </p>
        </div>

        {/* ============================================================
            2. ROI 卡片组（业务产出 / 成本节约 / 替代人时 / 综合 ROI）
            ============================================================ */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5">
          {roiCards.map((card, i) => (
            <RoiCard
              key={card.key}
              icon={card.icon}
              label={card.label}
              value={card.value}
              unit={card.unit}
              subtitle={card.subtitle}
              highlight={card.highlight}
              delay={0.05 + i * 0.05}
              tooltip={card.tooltip}
            />
          ))}
        </div>

        {/* ============================================================
            3. 「启用 AI 前后」成本对比柱状图
            ============================================================ */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25 }}
          className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
        >
          <div className="flex items-center justify-between mb-4">
            <div className="space-y-2">
              <h2 className="font-serif-display text-base font-semibold text-text-primary">
                启用 AI 前后成本对比
              </h2>
              <div className="brand-rule"></div>
            </div>
            <span className="text-xs text-text-tertiary">{timeRangeLabel} · 单位：元</span>
          </div>
          {metrics.cost.pre_ai_cost_yuan === 0 ? (
            <div className="h-[260px] flex items-center justify-center text-text-tertiary text-sm">
              暂无已解决会话，无法计算人工基线成本
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={costComparisonData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid {...chartGrid} vertical={false} />
                <XAxis dataKey="name" {...chartAxis} tickLine={false} axisLine={false} />
                <YAxis {...chartAxis} tickLine={false} axisLine={false} tickFormatter={(v) => formatYuan(v as number)} />
                <Tooltip {...chartTooltip} formatter={(v) => `${formatYuan(v as number)} 元`} />
                <Bar dataKey="cost" name="成本" radius={[4, 4, 0, 0]} maxBarSize={96}>
                  {[CHART_ACCENT, CHART_PRIMARY].map((c, i) => (
                    <Cell key={i} fill={c} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </motion.div>

        {/* ============================================================
            4. 趋势折线（协作量 / 满意度）
            ============================================================ */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
        >
          <div className="flex items-center justify-between mb-4">
            <div className="space-y-2">
              <h2 className="font-serif-display text-base font-semibold text-text-primary">
                协作量与满意度趋势
              </h2>
              <div className="brand-rule"></div>
            </div>
            <span className="text-xs text-text-tertiary">{timeRangeLabel}</span>
          </div>
          {trendData.length === 0 || trendData.every((d) => d.协作量 === 0) ? (
            <div className="h-[260px] flex items-center justify-center text-text-tertiary text-sm">
              暂无协作数据
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={trendData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid {...chartGrid} vertical={false} />
                <XAxis dataKey="date" {...chartAxis} tickLine={false} axisLine={false} />
                <YAxis
                  yAxisId="left"
                  {...chartAxis}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  domain={[0, 5]}
                  {...chartAxis}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip {...chartTooltip} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="协作量"
                  stroke={CHART_PRIMARY}
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="满意度"
                  stroke={CHART_SECONDARY}
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </motion.div>

        {/* ============================================================
            5. 覆盖与质量双栏
            ============================================================ */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* 左：知识覆盖与技能使用 */}
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.35 }}
            className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
          >
            <div className="space-y-2 mb-4">
              <h2 className="font-serif-display text-base font-semibold text-text-primary">
                知识覆盖与技能使用
              </h2>
              <div className="brand-rule"></div>
            </div>
            <div className="grid grid-cols-2 gap-4 mb-5">
              <div className="bg-elevated rounded-lg p-4 border border-border-subtle">
                <div className="flex items-center gap-2 mb-1">
                  <Database className="w-4 h-4 text-brand-500" />
                  <span className="text-xs text-text-tertiary">知识覆盖率</span>
                </div>
                <div className="font-mono tabular-nums text-2xl font-bold text-text-primary">
                  {formatPct(metrics.coverage.knowledge_coverage)}
                </div>
                <div className="text-xs text-text-tertiary mt-1">
                  {metrics.coverage.answered_questions} / {metrics.coverage.total_questions} 问题已回答
                </div>
              </div>
              <div className="bg-elevated rounded-lg p-4 border border-border-subtle">
                <div className="flex items-center gap-2 mb-1">
                  <Percent className="w-4 h-4 text-brand-500" />
                  <span className="text-xs text-text-tertiary">RAG 准确率</span>
                </div>
                <div className="font-mono tabular-nums text-2xl font-bold text-text-primary">
                  {formatPct(metrics.quality.accuracy)}
                </div>
                <div className="text-xs text-text-tertiary mt-1">四项指标均值</div>
              </div>
            </div>
            <div>
              <h4 className="text-sm font-medium text-text-secondary mb-2">技能使用 Top5</h4>
              {metrics.coverage.skill_top5.length === 0 ? (
                <p className="text-text-tertiary text-center py-4 text-sm">暂无技能使用记录</p>
              ) : (
                <div className="space-y-2">
                  {metrics.coverage.skill_top5.map((s, i) => {
                    const max = metrics.coverage.skill_top5[0]?.count || 1
                    return (
                      <div key={s.skill_id} className="flex items-center gap-3">
                        <span className="text-xs font-mono text-text-tertiary w-4">{i + 1}</span>
                        <span className="text-sm text-text-primary flex-1 truncate">{s.name}</span>
                        <div className="flex-1 h-2 bg-elevated rounded-full overflow-hidden">
                          <div
                            className="h-full bg-brand-500 rounded-full"
                            style={{ width: `${(s.count / max) * 100}%` }}
                          />
                        </div>
                        <span className="text-xs font-mono tabular-nums text-text-secondary w-10 text-right">
                          {s.count}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </motion.div>

          {/* 右：质量与满意度 */}
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 }}
            className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
          >
            <div className="space-y-2 mb-4">
              <h2 className="font-serif-display text-base font-semibold text-text-primary">
                服务质量与满意度
              </h2>
              <div className="brand-rule"></div>
            </div>
            <div className="space-y-4">
              <div className="flex items-center justify-between p-4 bg-elevated rounded-lg border border-border-subtle">
                <div className="flex items-center gap-3">
                  <div className="bg-brand-50 text-brand-500 rounded-md p-2">
                    <Star className="w-5 h-5" />
                  </div>
                  <div>
                    <div className="text-xs text-text-tertiary">平均满意度评分</div>
                    <div className="font-mono tabular-nums text-2xl font-bold text-text-primary">
                      {metrics.quality.satisfaction_score.toFixed(1)}
                      <span className="text-sm font-normal text-text-tertiary"> / 5.0</span>
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-xs text-text-tertiary">满意率</div>
                  <div className="font-mono tabular-nums text-lg font-bold text-success">
                    {formatPct(metrics.quality.satisfaction_rate)}
                  </div>
                </div>
              </div>
              <div className="flex items-center justify-between p-4 bg-elevated rounded-lg border border-border-subtle">
                <div className="flex items-center gap-3">
                  <div className="bg-brand-50 text-brand-500 rounded-md p-2">
                    <MessageSquare className="w-5 h-5" />
                  </div>
                  <div>
                    <div className="text-xs text-text-tertiary">总会话量</div>
                    <div className="font-mono tabular-nums text-2xl font-bold text-text-primary">
                      {metrics.summary.total_conversations.toLocaleString()}
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-xs text-text-tertiary">已解决</div>
                  <div className="font-mono tabular-nums text-lg font-bold text-text-primary">
                    {metrics.summary.resolved_count.toLocaleString()}
                  </div>
                </div>
              </div>
              <div className="flex items-center justify-between p-4 bg-elevated rounded-lg border border-border-subtle">
                <div className="flex items-center gap-3">
                  <div className="bg-brand-50 text-brand-500 rounded-md p-2">
                    <Coins className="w-5 h-5" />
                  </div>
                  <div>
                    <div className="text-xs text-text-tertiary">Token 总消耗</div>
                    <div className="font-mono tabular-nums text-2xl font-bold text-text-primary">
                      {metrics.summary.total_tokens.toLocaleString()}
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-xs text-text-tertiary">总成本</div>
                  <div className="font-mono tabular-nums text-lg font-bold text-text-primary">
                    {formatYuan(metrics.cost.total_cost_yuan)} 元
                  </div>
                </div>
              </div>
            </div>
          </motion.div>
        </div>
      </div>
    </Layout>
  )
}

// ============================================================
// ROI 卡片子组件（支持悬浮说明：解释该指标如何由真实数据推导）
// ============================================================
function RoiCard({
  icon: Icon,
  label,
  value,
  unit,
  subtitle,
  highlight = false,
  delay = 0,
  tooltip,
}: {
  icon: typeof Clock
  label: string
  value: string
  unit?: string
  subtitle?: string
  highlight?: boolean
  delay?: number
  /** 悬浮说明：指标口径与推导过程（引用真实聚合数据） */
  tooltip?: string
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay }}
      className={`relative group bg-surface border rounded-xl shadow-soft p-5 ${
        highlight ? 'border-brand-200 bg-brand-50/30' : 'border-border-default'
      }`}
    >
      <div className="flex items-center justify-between mb-3">
        <div className={`rounded-md p-2 ${highlight ? 'bg-brand-500 text-white' : 'bg-brand-50 text-brand-500'}`}>
          <Icon className="w-5 h-5" />
        </div>
        {tooltip && (
          <span className="relative inline-flex items-center justify-center w-5 h-5 rounded-full bg-elevated text-text-tertiary text-xs cursor-help">
            ?
            <span className="pointer-events-none absolute right-0 top-full z-30 mt-1.5 w-64 rounded-lg bg-neutral-900/95 dark:bg-neutral-700/95 px-3 py-2 text-[11px] leading-relaxed text-white opacity-0 shadow-xl transition-opacity duration-150 group-hover:opacity-100">
              {tooltip}
            </span>
          </span>
        )}
      </div>
      <div className="text-sm text-text-tertiary">{label}</div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="font-mono tabular-nums text-2xl font-bold text-text-primary">{value}</span>
        {unit && <span className="text-sm text-text-tertiary">{unit}</span>}
      </div>
      {subtitle && <div className="text-xs text-text-tertiary mt-1">{subtitle}</div>}
    </motion.div>
  )
}
