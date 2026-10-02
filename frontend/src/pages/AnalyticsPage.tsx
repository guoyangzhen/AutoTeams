/**
 * AnalyticsPage —「数据看板」核心工作空间（/analytics）。
 *
 * 面向中小企业老板：把散落在多个页面的经营数据合并成一块看板，回答两个问题：
 * 1. 这些 AI 员工到底省了多少人力、多少钱？（ROI）
 * 2. 数字员工真的在干活吗？（使用量 / 质量 / 成本）
 *
 * 数据全部来自真实后端，无任何兜底假数据：
 * - GET /metrics/business                          效率 / 成本 / 覆盖率 / 质量 / 趋势
 * - GET /cognition/vitals/{enterprise_id}          企业运行体征（事件量、员工编制、健康度）
 * - GET /product-analytics/company-action-summary   企业级价值漏斗（仅企业管理员）
 * - GET /agents                                    数字员工清单（用于按员工下钻）
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  BarChart3,
  Coins,
  Gauge,
  HeartPulse,
  ListChecks,
  MessageSquare,
  Star,
  TrendingUp,
  Wallet,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import Layout from '@/components/Layout'
import { PageHeader } from '@/components/ui/PageHeader'
import { SectionPanel } from '@/components/ui/SectionPanel'
import { EmptyState } from '@/components/ui/EmptyState'
import { MetricGrid, type Metric } from '@/components/ui/MetricGrid'
import { getBusinessMetrics, type BusinessMetrics } from '@/api/metrics'
import { getCompanyActionSummary, type CompanyActionSummary } from '@/api/productAnalytics'
import { getEnterpriseVitals } from '@/api/cognition'
import { getAgents } from '@/api/agents'
import { useAuth } from '@/hooks/useAuth'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'

const RANGE_OPTIONS = [
  { days: 7, label: '近 7 天' },
  { days: 30, label: '近 30 天' },
  { days: 90, label: '近 90 天' },
]

const CHART_TOOLTIP_STYLE = {
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border-default)',
  borderRadius: '8px',
  fontSize: '12px',
}

export default function AnalyticsPage() {
  const { user } = useAuth()
  const enterpriseId = useEnterpriseId()
  const [rangeDays, setRangeDays] = useState(30)
  const [agentId, setAgentId] = useState('')
  const isAdmin = user?.role === 'admin'

  const agentsQuery = useQuery({ queryKey: ['agents', 'list'], queryFn: getAgents })
  const metricsQuery = useQuery({
    queryKey: ['metrics', 'business', rangeDays, agentId],
    queryFn: () => getBusinessMetrics(rangeDays, agentId || undefined),
  })
  const vitalsQuery = useQuery({
    queryKey: ['cognition', 'vitals', enterpriseId],
    queryFn: () => getEnterpriseVitals(enterpriseId),
    enabled: enterpriseId.length > 0,
  })
  const funnelQuery = useQuery({
    queryKey: ['product-analytics', 'company-action-summary', rangeDays],
    queryFn: () => getCompanyActionSummary(rangeDays),
    enabled: isAdmin,
  })

  const agents = useMemo(() => agentsQuery.data ?? [], [agentsQuery.data])
  const metrics = metricsQuery.data ?? null
  const vitals = vitalsQuery.data ?? null
  const funnel = funnelQuery.data ?? null

  const metricsCards: Metric[] = useMemo(() => {
    if (!metrics) return []
    return [
      {
        key: 'hours',
        icon: TrendingUp,
        value: metrics.efficiency.hours_saved.toFixed(1),
        unit: '小时',
        label: `节省人力（近 ${metrics.period.range_days} 天）`,
        tone: 'brand',
      },
      {
        key: 'cost-reduction',
        icon: Wallet,
        value: metrics.cost.cost_reduction_pct.toFixed(1),
        unit: '%',
        label: '相对纯人工成本下降',
        tone: 'success',
      },
      {
        key: 'cost',
        icon: Coins,
        value: metrics.cost.total_cost_yuan.toFixed(2),
        unit: '元',
        label: `实际 AI 成本（预估月支出 ${metrics.cost.estimated_monthly_cost_yuan.toFixed(2)} 元）`,
        tone: 'info',
      },
      {
        key: 'resolution',
        icon: Gauge,
        value: (metrics.efficiency.resolution_rate * 100).toFixed(1),
        unit: '%',
        label: `一次解决率（转人工 ${(metrics.efficiency.escalation_rate * 100).toFixed(1)}%）`,
        tone: 'brand',
      },
      {
        key: 'conversations',
        icon: MessageSquare,
        value: metrics.summary.total_conversations,
        label: `会话总量（已解决 ${metrics.summary.resolved_count}）`,
        tone: 'info',
      },
      {
        key: 'satisfaction',
        icon: Star,
        value: metrics.quality.satisfaction_score.toFixed(2),
        unit: '/ 5',
        label: `满意度（${metrics.quality.rated_count} 次评价）`,
        tone: metrics.quality.satisfaction_score >= 4 ? 'success' : 'warning',
      },
    ]
  }, [metrics])

  const vitalsCards: Metric[] = useMemo(() => {
    if (!vitals) return []
    return [
      {
        key: 'agents',
        icon: ListChecks,
        value: `${vitals.agents.production}/${vitals.agents.total}`,
        label: '在岗数字员工 / 全部',
        tone: 'brand',
      },
      {
        key: 'events-24h',
        icon: Activity,
        value: vitals.events.last_24h,
        label: `24 小时协作事件（失败 ${vitals.events.failed}）`,
        tone: vitals.events.failed > 0 ? 'warning' : 'success',
      },
      {
        key: 'approvals',
        icon: HeartPulse,
        value: vitals.approvals.pending,
        label: '待人工审批',
        tone: vitals.approvals.pending > 0 ? 'warning' : 'success',
      },
      {
        key: 'health',
        icon: HeartPulse,
        value: (vitals.health.score * 100).toFixed(1),
        unit: '%',
        label: `企业健康度（${vitals.health.tone}）`,
        tone: vitals.health.score >= 0.7 ? 'success' : 'warning',
      },
    ]
  }, [vitals])

  return (
    <Layout>
      <div className="mx-auto w-full max-w-[1280px] px-10 py-10">
        <PageHeader
          title="数据看板"
          subtitle="AI 员工到底省了多少人、省了多少钱，一块看板说清楚"
          actions={
            <div className="flex items-center gap-2">
              <select
                value={rangeDays}
                onChange={(e) => setRangeDays(Number(e.target.value))}
                aria-label="统计周期"
                className="px-3 py-2 bg-surface text-text-primary border border-border-default rounded-lg text-[13px]"
              >
                {RANGE_OPTIONS.map((opt) => (
                  <option key={opt.days} value={opt.days}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <select
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                aria-label="数字员工"
                className="px-3 py-2 bg-surface text-text-primary border border-border-default rounded-lg text-[13px]"
              >
                <option value="">全部数字员工</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
          }
        />

        {metricsQuery.isPending ? (
          <p className="text-[13px] text-text-tertiary">正在汇总经营数据…</p>
        ) : metricsQuery.isError ? (
          <EmptyState
            icon={BarChart3}
            title="经营数据加载失败"
            description="请确认后端服务可用后重试；若持续失败请联系管理员查看服务端日志。"
            action={{ label: '重新加载', onClick: () => void metricsQuery.refetch() }}
          />
        ) : !metrics || metrics.summary.total_conversations === 0 ? (
          <EmptyState
            icon={BarChart3}
            title={
              agentId.length > 0
                ? '该数字员工近期没有会话'
                : `近 ${rangeDays} 天还没有可统计的使用数据`
            }
            description={
              agentId.length > 0
                ? '看板只统计客户会话类数据。若这位数字员工只做文档 / 运营类任务，其工作量请到「工作流」页查看规程执行情况。'
                : '看板的统计口径是「与客户的会话」：数字员工接待客户或回答问题后，这里会自动汇总效率、成本与 ROI 数据。'
            }
            action={
              agentId.length > 0
                ? { label: '查看全部数字员工', onClick: () => setAgentId('') }
                : { label: '去数字员工名册', to: '/workforce' }
            }
            secondaryAction={
              agentId.length === 0 && rangeDays < 90
                ? { label: '把时间范围拉长到 90 天', onClick: () => setRangeDays(90) }
                : undefined
            }
          >
            <p className="text-[12px] text-text-tertiary max-w-md leading-relaxed">
              当前筛选：近 {rangeDays} 天
              {agentId.length > 0
                ? ` · 单个数字员工（${agents.find((a) => a.id === agentId)?.name ?? '未知'}）`
                : ' · 全部数字员工'}
              。没有数据不是故障，只是这段时间还没有人机协作记录。
            </p>
          </EmptyState>
        ) : (
          <>
            <MetricGrid metrics={metricsCards} columns={3} className="mb-8" />

            <div className="grid grid-cols-12 gap-8 mb-8">
              <div className="col-span-12 lg:col-span-7">
                <TrendChart metrics={metrics} />
              </div>
              <div className="col-span-12 lg:col-span-5">
                <SkillRanking metrics={metrics} />
              </div>
            </div>

            <section className="mb-8">
              <h2 className="text-[15px] font-semibold text-text-primary mb-3">企业运行体征</h2>
              {vitals ? (
                <MetricGrid metrics={vitalsCards} />
              ) : (
                <p className="text-[12px] text-text-tertiary">
                  未能读取企业运行体征（需账号已绑定企业）。这不影响上方会话与成本统计。
                </p>
              )}
            </section>

            {isAdmin && <FunnelPanel funnel={funnel} pending={funnelQuery.isPending} />}

            <CostBreakdown metrics={metrics} />
          </>
        )}
      </div>
    </Layout>
  )
}

// ============================================================
// 子面板
// ============================================================

function TrendChart({ metrics }: { metrics: BusinessMetrics }) {
  const daily = metrics.trends.daily_counts.map((p) => ({
    date: p.date.slice(5),
    会话量: p.count,
  }))
  const satisfaction = metrics.trends.satisfaction_trend.map((p) => ({
    date: p.date.slice(5),
    满意度: p.score,
  }))

  return (
    <SectionPanel
      title="会话量与满意度趋势"
      description={`${metrics.period.start} 至 ${metrics.period.end}`}
      icon={<Activity className="w-4 h-4" />}
      bodyClassName="px-5 py-4"
    >
      <div className="flex flex-col gap-4">
        <div style={{ height: 200 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={daily} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="var(--text-tertiary)" />
              <YAxis tick={{ fontSize: 11 }} stroke="var(--text-tertiary)" allowDecimals={false} />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
              <Line type="monotone" dataKey="会话量" stroke="var(--brand)" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        {satisfaction.length > 0 && (
          <div style={{ height: 160 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={satisfaction} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="var(--text-tertiary)" />
                <YAxis
                  tick={{ fontSize: 11 }}
                  stroke="var(--text-tertiary)"
                  domain={[0, 5]}
                  allowDecimals={false}
                />
                <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                <Line type="monotone" dataKey="满意度" stroke="var(--success)" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </SectionPanel>
  )
}

function SkillRanking({ metrics }: { metrics: BusinessMetrics }) {
  const data = metrics.coverage.skill_top5.map((s) => ({ name: s.name, 使用次数: s.count }))

  return (
    <SectionPanel
      title="工作能力使用 Top 5"
      description="最常被数字员工调用的工作能力"
      icon={<BarChart3 className="w-4 h-4" />}
      bodyClassName="px-5 py-4"
    >
      {data.length === 0 ? (
        <p className="text-[12px] text-text-tertiary">统计周期内还没有工作能力被调用。</p>
      ) : (
        <div style={{ height: 300 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} layout="vertical" margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
              <XAxis
                type="number"
                tick={{ fontSize: 11 }}
                stroke="var(--text-tertiary)"
                allowDecimals={false}
              />
              <YAxis
                type="category"
                dataKey="name"
                width={110}
                tick={{ fontSize: 11 }}
                stroke="var(--text-tertiary)"
              />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
              <Bar dataKey="使用次数" fill="var(--brand)" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </SectionPanel>
  )
}

function FunnelPanel({ funnel, pending }: { funnel: CompanyActionSummary | null; pending: boolean }) {
  if (pending) {
    return (
      <section className="mb-8">
        <p className="text-[12px] text-text-tertiary">正在汇总企业级使用漏斗…</p>
      </section>
    )
  }

  if (!funnel) return null

  const rows: Array<[string, number]> = [
    ['活跃会话', funnel.unique_sessions],
    ['经营简报浏览', funnel.brief_views],
    ['简报内点击行动', funnel.brief_action_clicks],
    ['开始产品导览', funnel.tour_started],
    ['完成产品导览', funnel.tour_completed],
  ]

  return (
    <section className="mb-8">
      <SectionPanel
        title="价值落地漏斗"
        description={`最近 ${funnel.window_days} 天 · 仅企业管理员可见`}
        icon={<TrendingUp className="w-4 h-4" />}
        bodyClassName="px-5 py-4"
      >
        {funnel.total_events === 0 ? (
          <p className="text-[12px] text-text-tertiary">
            统计周期内还没有产品使用事件，员工开始使用看板与简报后这里会自动统计。
          </p>
        ) : (
          <ul className="space-y-2">
            {rows.map(([label, value]) => {
              const ratio = funnel.total_events > 0 ? (value / funnel.total_events) * 100 : 0
              return (
                <li key={label} className="flex items-center gap-3">
                  <span className="text-[12px] text-text-secondary w-32 flex-shrink-0">{label}</span>
                  <span className="flex-1 h-2 bg-elevated rounded-full overflow-hidden">
                    <span
                      className="block h-full bg-brand-500 rounded-full"
                      style={{ width: `${Math.min(ratio, 100)}%` }}
                    />
                  </span>
                  <span className="text-[12px] font-mono text-text-tertiary w-16 text-right flex-shrink-0">
                    {value} 次
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </SectionPanel>
    </section>
  )
}

function CostBreakdown({ metrics }: { metrics: BusinessMetrics }) {
  const items: Array<[string, string]> = [
    ['纯人工成本（同等工作量）', `¥${metrics.cost.pre_ai_cost_yuan.toFixed(2)}`],
    ['AI 实际成本', `¥${metrics.cost.total_cost_yuan.toFixed(2)}`],
    ['单次会话成本', `¥${metrics.cost.per_conversation_cost_yuan.toFixed(4)}`],
    ['每千 token 单价', `¥${metrics.cost.cost_per_1k_tokens_yuan.toFixed(4)}`],
    ['工具调用次数', `${metrics.cost.tool_call_count} 次`],
    ['消耗 token', metrics.cost.total_tokens.toLocaleString()],
    ['人工等效耗时', `${metrics.efficiency.human_compare_minutes.toFixed(0)} 分钟`],
    ['知识覆盖率', `${(metrics.coverage.knowledge_coverage * 100).toFixed(1)}%`],
    ['平均响应时长', `${(metrics.efficiency.avg_response_time_ms / 1000).toFixed(1)} 秒`],
  ]

  return (
    <SectionPanel
      title="成本与质量明细"
      description="口径与后端 metrics_service 一致，可直接用于经营汇报"
      icon={<Coins className="w-4 h-4" />}
      bodyClassName="px-5 py-4"
    >
      <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-3">
        {items.map(([label, value]) => (
          <div
            key={label}
            className="flex items-center justify-between gap-3 border-b border-border-default pb-2"
          >
            <dt className="text-[12px] text-text-tertiary">{label}</dt>
            <dd className="text-[13px] font-medium text-text-primary tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-[11px] text-text-tertiary mt-4 leading-relaxed">
        成本按 token 用量（¥0.008/千 token）与工具调用（¥0.08/次）核算，人力等效耗时按
        ¥1.0/人工分钟折算；数字员工 7×24 在岗不排班、不请假，成本随用量线性增长。
      </p>
    </SectionPanel>
  )
}
