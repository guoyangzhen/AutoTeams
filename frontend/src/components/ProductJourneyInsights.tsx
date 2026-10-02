import { useCallback, useEffect, useMemo, useState } from 'react'
import { BarChart3, Eye, Gauge, RefreshCw, Route, Users } from 'lucide-react'
import { getCompanyActionSummary, type CompanyActionSummary } from '@/api/productAnalytics'
import { useAuth } from '@/hooks/useAuth'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'

function percentage(numerator: number, denominator: number): string {
  if (denominator <= 0) return '—'
  return `${Math.round((numerator / denominator) * 100)}%`
}

function topAction(actionClicks: Record<string, number>): { label: string; count: number } | null {
  const entry = Object.entries(actionClicks).sort(([, left], [, right]) => right - left)[0]
  if (!entry) return null
  const labels: Record<string, string> = {
    compile: '开始企业编译',
    staff: '生成 AI 员工',
    start_demo: '运行协作流程',
    review_approvals: '查看待审批事项',
    inspect_execution: '查看执行详情',
    view_impact: '查看业务效果',
  }
  return { label: labels[entry[0]] ?? entry[0], count: entry[1] }
}

/**
 * 经营行动简报的独立管理员洞察模块。
 *
 * 仅管理员加载企业级匿名聚合，普通成员直接不渲染、不调用受限 API。组件不依赖
 * AICompanyView 的实时数据；因此可被将来的经营、设置或实验分析页面复用。
 */
export function ProductJourneyInsights() {
  const { user } = useAuth()
  const [summary, setSummary] = useState<CompanyActionSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEnterpriseAdmin = user?.role === 'admin' && Boolean(user.enterprise_id)

  const loadSummary = useCallback(async () => {
    if (!isEnterpriseAdmin) return
    setLoading(true)
    setError(null)
    try {
      setSummary(await getCompanyActionSummary(30))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载产品洞察失败')
    } finally {
      setLoading(false)
    }
  }, [isEnterpriseAdmin])

  useEffect(() => {
    void loadSummary()
  }, [loadSummary])

  const primaryAction = useMemo(
    () => (summary ? topAction(summary.action_clicks) : null),
    [summary],
  )

  if (!isEnterpriseAdmin) return null

  const hasSamples = (summary?.brief_views ?? 0) > 0 || (summary?.tour_started ?? 0) > 0

  return (
    <section aria-labelledby="product-journey-insights-title" className="ui-card overflow-hidden rounded-2xl border border-border-default">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-border-default px-5 py-4 sm:px-6">
        <div className="flex items-start gap-3">
          <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-brand-500/15 bg-brand-500/10 text-brand-500">
            <BarChart3 className="h-4 w-4" aria-hidden="true" />
          </span>
          <div>
            <p className="ui-context-kicker">管理员洞察 · 最近 30 天</p>
            <h2 id="product-journey-insights-title" className="mt-1 text-base font-semibold text-text-primary">价值路径是否正在被理解并执行</h2>
            <p className="mt-1 text-sm leading-relaxed text-text-secondary">仅显示企业级匿名会话汇总；不展示个人行为、聊天内容或业务资源明细。</p>
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void loadSummary()} disabled={loading}>
          {loading ? <Spinner size="sm" /> : <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />}
          刷新
        </Button>
      </div>

      {error ? (
        <div className="px-5 py-5 sm:px-6">
          <p className="text-sm text-text-secondary">产品洞察暂时不可用。此问题不会影响编译、协作或审批流程。</p>
          <Button className="mt-3" variant="outline" size="sm" onClick={() => void loadSummary()} disabled={loading}>重试</Button>
        </div>
      ) : loading && !summary ? (
        <div className="flex min-h-32 items-center justify-center"><Spinner size="sm" /></div>
      ) : !hasSamples ? (
        <div className="px-5 py-5 sm:px-6">
          <p className="text-sm font-medium text-text-primary">尚无足够的价值路径样本</p>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-text-secondary">当用户查看行动简报、完成首次导览或进入推荐的下一步后，这里将展示真实聚合转化数据。系统不会以模拟指标填充该区域。</p>
        </div>
      ) : summary ? (
        <div className="grid gap-px bg-border-default sm:grid-cols-2 xl:grid-cols-4">
          <InsightMetric icon={Eye} label="行动简报点击率" value={percentage(summary.brief_action_clicks, summary.brief_views)} detail={`${summary.brief_action_clicks} 次行动 / ${summary.brief_views} 次曝光`} />
          <InsightMetric icon={Route} label="导览完成率" value={percentage(summary.tour_completed, summary.tour_started)} detail={`${summary.tour_completed} 次完成 / ${summary.tour_started} 次启动`} />
          <InsightMetric icon={Users} label="匿名会话" value={String(summary.unique_sessions)} detail={`${summary.total_events} 条最小化事件`} />
          <InsightMetric icon={Gauge} label="最常执行的下一步" value={primaryAction?.label ?? '—'} detail={primaryAction ? `${primaryAction.count} 次点击` : '等待更多样本'} compact />
        </div>
      ) : null}
    </section>
  )
}

function InsightMetric({
  icon: Icon,
  label,
  value,
  detail,
  compact = false,
}: {
  icon: typeof Eye
  label: string
  value: string
  detail: string
  compact?: boolean
}) {
  return (
    <div className="min-w-0 bg-[var(--surface-raised)] p-4 sm:p-5">
      <div className="flex items-center gap-2 text-text-tertiary">
        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="text-xs font-medium">{label}</span>
      </div>
      <p className={`mt-3 truncate font-semibold tracking-[-0.025em] text-text-primary ${compact ? 'text-lg' : 'text-2xl'}`}>{value}</p>
      <p className="mt-1 truncate text-xs text-text-secondary">{detail}</p>
    </div>
  )
}
