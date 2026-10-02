/**
 * BossDashboard — 总览驾驶舱。
 *
 * 对应蓝图 screens-v2/01-boss-cockpit.html：近黑指挥区，回答「今天谁在跑、卡在哪」。
 * 版式：概览统计 + 14 天会话趋势单线 + 待裁决发丝线列表。
 *
 * 数据来源（全部为真实接口，零造数）：
 * - useLiveCompany       → 企业生命体征 + 协作事件流（SSE，失败降级 15s 轮询）
 * - getPendingApprovals  → 待我裁决队列（可批准/驳回，真实写操作）
 * - getOrgMetrics        → 成熟度等级 / 工具装配 / 业务影响
 * - getRuntime           → 最近一次编译版本与完成度
 * - getBusinessMetrics   → 14 天日计数（趋势线真实数据）
 *
 * 错误状态：任一子接口失败降级为「数据不可用」占位，不阻塞其余区块。
 */
import { useCallback, useMemo, useState } from 'react'
import { RefreshCw, Download, Activity, ShieldAlert, Clock } from 'lucide-react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import Layout from '@/components/Layout'
import { OnboardingWizard } from '@/components/OnboardingWizard'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { useLiveCompany } from '@/hooks/useLiveCompany'
import { useDashboardOverview } from '@/hooks/useDashboardOverview'
import { trendScale } from './dashboardTrend'

/**
 * 近黑作用域：本屏整块内容画布（顶栏与侧栏仍由 Layout 提供，保持 64px / 240px）。
 * 负外边距抵消 Layout 的 app-content 留白，让指挥区铺满内容区而非浮在纸面画布上。
 */
const shell = 'at-cockpit -mx-4 -mb-[3.5rem] -mt-6 min-h-[calc(100vh-4rem)] px-4 py-6 sm:-mx-6 sm:px-6 lg:px-10 lg:py-8'
const canvas = 'mx-auto w-full max-w-[1280px] space-y-6 lg:space-y-8'

/** 趋势线长度（14 天） */
const TREND_DAYS = 14

/** 协作事件类型 → 中文标签（只覆盖后端 CollaborationEventType 已声明的取值） */
const EVENT_LABEL: Record<string, string> = {
  inquiry_received: '收到询盘',
  product_query: '产品参数查询',
  quotation_generated: '销售报价',
  approval_submitted: '提交审批',
  approval_approved: '审批通过',
  order_synced: '订单同步',
  after_sales: '售后接管',
  handoff: '任务转交',
  escalation: '异常升级',
  error: '执行错误',
  opportunity_created: '商机创建',
  approval_flow_created: '审批流创建',
  deal_closed: '成交',
}

/** 协作事件状态 → 圆点语义色 */
const EVENT_TONE: Record<string, 'ok' | 'warn' | 'bad' | 'idle'> = {
  processed: 'ok',
  pending: 'warn',
  failed: 'bad',
}

/** 健康语义 → 圆点色（沿用后端 health.tone 契约） */
const HEALTH_TONE: Record<string, 'ok' | 'warn' | 'bad' | 'idle'> = {
  alive: 'ok',
  alert: 'warn',
  fault: 'bad',
  idle: 'idle',
}

const HEALTH_LABEL: Record<string, string> = {
  alive: '运行正常',
  alert: '存在告警',
  fault: '出现故障',
  idle: '待机中',
}

/** 安全数值：非有限数一律回退 0，杜绝 NaN 上屏 */
function num(v: number | null | undefined): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

/** 安全百分比：0–100 区间裁剪 */
function pct(v: number | null | undefined): number {
  return Math.min(100, Math.max(0, num(v)))
}

/** 安全格式化日期时间，无效值返回 fallback */
function formatStamp(ts: string | null | undefined, fallback = '—'): string {
  if (!ts) return fallback
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return fallback
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 相对等待时长：审批/事件的「已等待 Nm」 */
function formatWait(ts: string | null | undefined): string {
  if (!ts) return '—'
  const t = new Date(ts).getTime()
  if (Number.isNaN(t)) return '—'
  const mins = Math.floor((Date.now() - t) / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ${mins % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

/** 安全取首字作为字母牌（空名回退 '?'） */
function initial(name: string | null | undefined): string {
  const ch = (name || '').trim().charAt(0)
  return ch || '?'
}

function buildLinePath(values: number[], left: number, right: number, top: number, bottom: number, y: (value: number, top: number, bottom: number) => number): string {
  if (values.length < 2) return ''
  const step = (right - left) / (values.length - 1)
  return values
    .map((v, i) => {
      const x = left + i * step
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y(v, top, bottom).toFixed(1)}`
    })
    .join(' ')
}


export default function BossDashboard() {
  const enterpriseId = useEnterpriseId()
  const { vitals, events, loading: liveLoading, refreshing, error: liveError, mode, lastUpdated, refresh } =
    useLiveCompany(enterpriseId)

  const { orgMetrics, runtime, approvals, trend, trendAvailable, rosterCount, deciding,
    refreshSecondary, decide: handleDecision } = useDashboardOverview(enterpriseId, refresh)
  const [exporting, setExporting] = useState(false)

  const handleRefresh = useCallback(() => {
    refresh()
    refreshSecondary()
  }, [refresh, refreshSecondary])

  const handleExport = useCallback(() => {
    if (!vitals) {
      toast.error('生命体征尚未就绪，暂无可导出数据')
      return
    }
    setExporting(true)
    try {
      const rows: string[][] = [
        ['指标', '数值', '采集时间'],
        ['在岗数字员工', String(vitals.agents.production), vitals.collected_at],
        ['员工总数', String(vitals.agents.total), vitals.collected_at],
        ['近24小时协作事件', String(vitals.events.last_24h), vitals.collected_at],
        ['失败事件', String(vitals.events.failed), vitals.collected_at],
        ['待裁决', String(vitals.approvals.pending), vitals.collected_at],
        ['影子对齐度(%)', String(vitals.shadow.match), vitals.collected_at],
        ['组织成熟度', orgMetrics?.maturity_level ?? '—', vitals.collected_at],
        ['运行时版本', runtime?.version ?? '未编译', runtime?.compiled_at ?? '—'],
      ]
      const csv = rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(',')).join('\n')
      const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `autoteams_cockpit_${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(url)
      toast.success('驾驶舱日报已导出')
    } finally {
      setExporting(false)
    }
  }, [vitals, orgMetrics, runtime])
  const displayApprovals = approvals ?? []
  const pendingApprovals = displayApprovals
  const healthTone = vitals ? HEALTH_TONE[vitals.health.tone] ?? 'idle' : 'idle'
  const trendValues = trend.map((p) => num(p.count))
  const scale = trendScale(trendValues)
  const lastPoint = trend.length > 0 ? trend[trend.length - 1] : null
  const firstPoint = trend.length > 0 ? trend[0] : null

  /** 14 天趋势的 X 轴 4 个刻度标签 */
  const trendTicks = useMemo(() => {
    if (trend.length === 0) return []
    const idx = [0, Math.floor((trend.length - 1) / 3), Math.floor(((trend.length - 1) * 2) / 3), trend.length - 1]
    return [...new Set(idx)].map((i) => {
      const d = new Date(trend[i].date)
      const label = Number.isNaN(d.getTime())
        ? trend[i].date
        : `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      return { label, count: num(trend[i].count) }
    })
  }, [trend])

  const PLOT_LEFT = 0
  const W = 700
  const H = 240
  const TOP = 30
  const BOTTOM = 210
  const linePath = buildLinePath(trendValues, PLOT_LEFT, W, TOP, BOTTOM, scale.y)
  const lastX = trendValues.length > 1 ? W : 0
  const lastY = trendValues.length > 1 ? scale.y(trendValues[trendValues.length - 1], TOP, BOTTOM) : 0

  const busy = liveLoading && !vitals
  const refreshBusy = liveLoading || refreshing

  /**
   * 新企业还没有任何数字员工时，驾驶舱的每块看板都是 0，
   * 与其让人对着一屏空数字猜下一步，不如直接展开 3 步入职向导（方案 §4.3.4）。
   *
   * 判定同时看两个真实来源，且两者都必须已成功采集：
   * - vitals.agents.total：底层 Agent 数
   * - rosterCount：花名册编制数（可能存在「有编制但没绑 Agent」的员工）
   * 任一为「未知」（null）都不触发，避免把一次接口抖动变成一次强制注册流程。
   */
  const needsOnboarding =
    !busy && !liveError && vitals !== null && rosterCount !== null &&
    vitals.agents.total === 0 && rosterCount === 0

  if (needsOnboarding) {
    return (
      <Layout>
        <div className="at-paper min-h-full w-full">
          <OnboardingWizard onComplete={handleRefresh} />
        </div>
      </Layout>
    )
  }

  // 已知「一个 Agent 都没有」，但编制数还在路上：此时先不要闪一屏零，
  // 那正是要替新用户挡掉的东西。等编制数落定再决定去哪。
  if (!busy && !liveError && vitals !== null && vitals.agents.total === 0 && rosterCount === null) {
    return (
      <Layout>
        <div className={shell}>
          <div className={canvas}>
            <p className="at-sm at-muted py-20 text-center">正在核对你的数字员工编制…</p>
          </div>
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className={shell}>
        <div className={canvas}>
          {/* 页面抬头：30px 标题 + 13px 副标题 + 全屏唯一实心主按钮 */}
          <header className="flex flex-col gap-4 pb-4 sm:flex-row sm:items-end sm:justify-between at-hairline-b">
            <div>
              <h1 className="at-title">总览驾驶舱</h1>
              <p className="at-sm at-muted mt-1">
                {new Date().toLocaleDateString('zh-CN')} ·{' '}
                {mode === 'live' ? '事件实时推送 · 指标按采集更新' : '事件与指标每 15 秒轮询'} ·{' '}
                {lastUpdated ? `更新于 ${formatStamp(new Date(lastUpdated).toISOString())}` : '等待首次采集'}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="at-btn at-btn-quiet"
                onClick={handleRefresh}
                disabled={refreshBusy}
                aria-busy={refreshBusy}
                aria-label={refreshBusy ? '正在刷新驾驶舱数据' : '刷新驾驶舱数据'}
              >
                <RefreshCw className={`h-4 w-4 ${refreshBusy ? 'animate-spin' : ''}`} aria-hidden="true" />
                {refreshBusy ? '刷新中…' : '刷新'}
              </button>
              <button
                type="button"
                className="at-btn at-btn-primary"
                onClick={handleExport}
                disabled={exporting || !vitals}
                aria-busy={exporting}
              >
                <Download className={`h-4 w-4 ${exporting ? 'animate-pulse' : ''}`} aria-hidden="true" />
                {exporting ? '导出中…' : '导出日报'}
              </button>
            </div>
          </header>

          {liveError && (
            <div
              role="alert"
              className="flex items-start gap-3 rounded-[10px] border px-4 py-3"
              style={{ borderColor: 'var(--at-hairline)', background: 'var(--at-surface)' }}
            >
              <Activity className="mt-0.5 h-4 w-4 shrink-0 at-warn" aria-hidden="true" />
              <div className="flex-1">
                <p className="at-sm at-warn">实时通道异常：{liveError}</p>
                <button type="button" className="at-link at-sm mt-2" onClick={handleRefresh}>
                  重新连接
                </button>
              </div>
            </div>
          )}


          {/* 顶部统计只呈现已取得的真实来源；未知值保留为空态。 */}
          <section className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4" aria-label="驾驶舱概览">
            <div className="at-surface min-w-0 rounded-[10px] border p-3 sm:p-5" style={{ borderColor: 'var(--at-hairline)' }}>
              <div className="flex items-start justify-between gap-2">
                <span className="at-meta at-muted">在岗数字员工</span>
                <span className={`at-dot at-dot-${vitals ? 'ok' : 'idle'}`} aria-hidden="true" />
              </div>
              <div className="mt-2 flex flex-wrap items-baseline gap-x-1.5">
                <span className="text-2xl sm:text-3xl font-semibold tracking-tight text-white tabular-nums">
                  {vitals ? vitals.agents.production : '—'}
                </span>
                <span className="at-meta at-muted">位在岗</span>
              </div>
              <p className="at-meta at-subtle mt-1.5">
                {vitals ? `底层 Agent 共 ${vitals.agents.total} 人` : '员工状态尚未采集'}
              </p>
            </div>

            <div className="at-surface min-w-0 rounded-[10px] border p-3 sm:p-5" style={{ borderColor: 'var(--at-hairline)' }}>
              <div className="flex items-start justify-between gap-2">
                <span className="at-meta at-muted">最新记录日会话</span>
                <span className={`at-dot at-dot-${trendAvailable && lastPoint ? 'ok' : 'idle'}`} aria-hidden="true" />
              </div>
              <div className="mt-2 flex flex-wrap items-baseline gap-x-1.5">
                <span className="text-2xl sm:text-3xl font-semibold tracking-tight text-white tabular-nums">
                  {trendAvailable && lastPoint ? num(lastPoint.count) : '—'}
                </span>
                <span className="at-meta at-muted">次</span>
              </div>
              <p className="at-meta at-subtle mt-1.5">
                {trendAvailable && lastPoint ? `记录日期 ${lastPoint.date}` : '会话数据尚未取得'}
              </p>
            </div>

            <div className="at-surface min-w-0 rounded-[10px] border p-3 sm:p-5" style={{ borderColor: 'var(--at-hairline)' }}>
              <div className="flex items-start justify-between gap-2">
                <span className="at-meta at-muted">待裁决审批</span>
                <span className={`at-dot at-dot-${approvals === null ? 'idle' : pendingApprovals.length > 0 ? 'warn' : 'ok'}`} aria-hidden="true" />
              </div>
              <div className="mt-2 flex flex-wrap items-baseline gap-x-1.5">
                <span className="text-2xl sm:text-3xl font-semibold tracking-tight text-white tabular-nums">
                  {approvals === null ? '—' : displayApprovals.length}
                </span>
                <span className="at-meta at-muted">笔待办</span>
              </div>
              <p className="at-meta at-subtle mt-1.5">
                {approvals === null ? '审批队列不可用' : pendingApprovals.length > 0 ? '需要人工处理' : '暂无待办审批'}
              </p>
            </div>

            <div className="at-surface min-w-0 rounded-[10px] border p-3 sm:p-5" style={{ borderColor: 'var(--at-hairline)' }}>
              <div className="flex items-start justify-between gap-2">
                <span className="at-meta at-muted">组织运行状态</span>
                <span className={`at-dot at-dot-${healthTone}`} aria-hidden="true" />
              </div>
              <div className="mt-2 min-w-0">
                <span className="block whitespace-nowrap text-[clamp(1.25rem,6vw,1.875rem)] leading-tight font-semibold tracking-tight text-white">
                  {vitals ? HEALTH_LABEL[vitals.health.tone] ?? '状态未知' : '—'}
                </span>
              </div>
              <p className="at-meta at-subtle mt-1.5">
                {vitals ? `采集于 ${formatStamp(vitals.collected_at)}` : '运行状态尚未采集'}
              </p>
            </div>
          </section>
          {/* 趋势是主导区，比较值与影子对齐度只在图下呈现一次。 */}
          <section aria-labelledby="conversation-trend-heading">
            <div className="at-surface rounded-[10px] p-4 sm:p-6">
              <div className="mb-4 flex flex-wrap items-start justify-between gap-2 sm:mb-6">
                <div>
                  <h2 id="conversation-trend-heading" className="at-section">会话趋势</h2>
                  <p className="at-meta at-muted mt-0.5">过去 {TREND_DAYS} 天 · 每日会话数</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="inline-block h-[2px] w-6" style={{ background: 'var(--at-accent)' }} />
                  <span className="at-meta at-muted">会话量（次/日）</span>
                </div>
              </div>

              {!trendAvailable ? (
                <p className="at-sm at-subtle py-16 text-center">趋势数据接口不可用，请稍后刷新</p>
              ) : trendValues.length < 2 ? (
                <p className="at-sm at-subtle py-16 text-center">近 {TREND_DAYS} 天暂无足够的会话记录</p>
              ) : (
                <>
                  <div className="w-full">
                    <div className="relative pl-[36px]">
                      {scale.ticks.map((tick) => (
                        <span key={tick} aria-hidden="true" data-trend-tick
                          className="at-meta at-subtle absolute left-0 -translate-y-1/2 tabular-nums"
                          style={{ top: `${scale.y(tick, TOP, BOTTOM) / H * 100}%` }}>
                          {tick}
                        </span>
                      ))}
                    <svg
                      viewBox={`0 0 ${W} ${H}`}
                      preserveAspectRatio="none"
                      className="block h-40 w-full overflow-visible sm:h-52 lg:h-56"
                      role="img"
                      aria-label={`过去 ${TREND_DAYS} 天会话趋势，最新 ${trendValues[trendValues.length - 1]} 次`}
                    >
                      {scale.ticks.map((tick) => {
                        const y = scale.y(tick, TOP, BOTTOM)
                        if (y < TOP - 2) return null
                        return (
                          <g key={tick}>
                            <line
                              x1={PLOT_LEFT}
                              x2={W}
                              y1={y}
                              y2={y}
                              stroke="var(--at-hairline)"
                              strokeWidth={1}
                              strokeDasharray="2 2"
                            />
                          </g>
                        )
                      })}
                      <line x1={PLOT_LEFT} x2={W} y1={BOTTOM} y2={BOTTOM} stroke="var(--at-hairline)" strokeWidth={1} />
                      <path
                        d={linePath}
                        fill="none"
                        stroke="var(--at-accent)"
                        strokeWidth={1.25}
                        vectorEffect="non-scaling-stroke"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                      <circle
                        cx={lastX}
                        cy={lastY}
                        r={3.5}
                        fill="var(--at-surface)"
                        stroke="var(--at-accent)"
                        strokeWidth={2}
                      />
                    </svg>
                    </div>
                    <div className="flex justify-between pt-2 pl-[36px] pr-1">
                      {trendTicks.map((t) => (
                        <span key={t.label} className="at-meta at-subtle">
                          {t.label}
                        </span>
                      ))}
                    </div>
                  </div>
                  {lastPoint && firstPoint && (
                    <p className="at-meta at-subtle mt-3">
                      区间首日 {num(firstPoint.count)} 次 → 最新一日 {num(lastPoint.count)} 次
                      {num(firstPoint.count) > 0 && ` · ${num(lastPoint.count) >= num(firstPoint.count) ? '上行' : '回落'} ${Math.abs(Math.round(((num(lastPoint.count) - num(firstPoint.count)) / num(firstPoint.count)) * 100))}%`}
                    </p>
                  )}
                </>
              )}
              <p className="at-meta at-muted mt-4 border-t pt-3" style={{ borderColor: 'var(--at-hairline)' }}>
                平均影子对齐度 <strong className="ml-2 text-white tabular-nums">{vitals ? `${pct(vitals.shadow.match)}%` : '—'}</strong>
              </p>
            </div>
          </section>

          {/* 待我裁决：发丝线列表，真实批准/驳回 */}
          <section aria-labelledby="pending-approvals-heading">
            <div className="flex flex-wrap items-baseline justify-between gap-2 pb-3 at-hairline-b">
              <div className="flex items-center gap-3">
                <h2 id="pending-approvals-heading" className="at-section">待我裁决</h2>
                <span className="at-meta at-muted">
                  {approvals === null ? '数据加载失败' : `${pendingApprovals.length} 件需人工确认`}
                </span>
              </div>
              <span className="at-meta at-subtle">裁决原则：先入先决 · 超期 4 小时自动上浮</span>
            </div>

            {approvals === null ? (
              <p className="at-sm at-subtle py-10 text-center">审批队列不可用，请稍后刷新</p>
            ) : pendingApprovals.length === 0 ? (
              <p className="at-sm at-muted py-6">当前队列中没有需要人工处理的审批。</p>
            ) : (
              <div>
                {pendingApprovals.map((item) => (
                  <div key={item.id} className="at-row flex min-w-0 flex-col gap-3 py-4 at-hairline-b lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex min-w-0 flex-1 items-start gap-3">
                      <div className="at-plate shrink-0" style={{ background: 'var(--at-plate)' }} title={item.requester_name}>
                        {initial(item.requester_name)}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="at-body break-words" style={{ color: 'var(--at-ink)' }}>{item.title}</p>
                        {item.description && <p className="at-sm at-muted mt-1 whitespace-pre-wrap break-words">{item.description}</p>}
                        <p className="at-meta at-subtle mt-1 break-all">{item.requester_name} · <span className="at-mono">{item.requester_id || item.id}</span></p>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-3 pl-11 lg:shrink-0 lg:gap-4 lg:pl-0">
                      <span className="at-meta at-muted flex items-center gap-1.5 lg:mr-2">
                        <Clock className="h-3 w-3" aria-hidden="true" />
                        已等待 {formatWait(item.created_at)}
                      </span>
                      <button
                        type="button"
                        className="at-btn at-btn-quiet"
                        disabled={deciding === item.id}
                        aria-busy={deciding === item.id}
                        onClick={() => handleDecision(item, 'approve')}
                      >
                        {deciding === item.id ? '裁决中…' : '批准'}
                      </button>
                      <button
                        type="button"
                        className={`at-btn at-btn-quiet ${deciding === item.id ? 'opacity-50' : ''}`}
                        disabled={deciding === item.id}
                        aria-busy={deciding === item.id}
                        onClick={() => handleDecision(item, 'reject')}
                      >
                        驳回
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* 协作动态 + 全局管控：左右两栏，均为真实采集值 */}
          <section className="grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div>
              <div className="flex items-center justify-between pb-3 at-hairline-b">
                <h2 className="at-section">协作动态</h2>
                <span className="at-meta at-subtle">
                  {mode === 'live' ? '事件实时推送' : '每 15 秒轮询'}
                </span>
              </div>
              {busy ? (
                <p className="at-sm at-subtle py-6 text-center">正在获取协作事件…</p>
              ) : events.length === 0 ? (
                <p className="at-sm at-subtle py-6 text-center">暂无协作事件</p>
              ) : (
                <ul>
                  {events.slice(0, 6).map((e) => (
                    <li key={e.event_id} className="at-row flex flex-wrap items-start justify-between gap-x-3 gap-y-1 py-2.5 at-hairline-b">
                      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
                        <span
                          className={`at-dot at-dot-${EVENT_TONE[e.status ?? ''] ?? 'idle'}`}
                          aria-hidden="true"
                        />
                        <span className="at-sm break-words" style={{ color: 'var(--at-ink)' }}>
                          {EVENT_LABEL[e.event_type] ?? e.event_type}
                        </span>
                        <span className="at-mono at-subtle break-all">
                          {e.source_agent_id ?? '系统'}
                        </span>
                      </div>
                      <span className="at-meta at-subtle shrink-0">{formatStamp(e.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <div className="flex items-center justify-between pb-3 at-hairline-b">
                <h2 className="at-section">全局管控</h2>
                <Link to="/workforce-gallery" className="at-link at-sm">
                  查看花名册
                </Link>
              </div>
              <dl>
                {[
                  {
                    label: '组织成熟度',
                    value: orgMetrics?.maturity_level ?? '—',
                    hint: orgMetrics ? '五级编译器当前等级' : '成熟度接口不可用',
                  },
                  {
                    label: '工具装配',
                    value: orgMetrics?.tool_usage
                      ? `${orgMetrics.tool_usage.verified ?? 0}/${orgMetrics.tool_usage.total_tools ?? 0}`
                      : '—',
                    hint: orgMetrics?.tool_usage
                      ? `已验证 / 工具总数 · 安装率 ${pct(orgMetrics.tool_usage.install_rate)}%`
                      : '工具注册表不可用',
                  },
                  {
                    label: '最近一次编译',
                    value: runtime?.version || '未编译',
                    hint: runtime?.compiled_at
                      ? `${formatStamp(runtime.compiled_at)} · 完成度 ${pct(Math.round(num(runtime.completeness) * 100))}%`
                      : '尚未产生编译产物',
                  },
                  {
                    label: '业务影响',
                    value: orgMetrics?.business_impact
                      ? `¥${(num(orgMetrics.business_impact.output_value) / 10000).toFixed(1)} 万`
                      : '—',
                    hint: orgMetrics?.business_impact
                      ? `替代 ${num(orgMetrics.business_impact.hours_replaced)} 人时 · 节约 ¥${(
                          num(orgMetrics.business_impact.cost_saved) / 10000
                        ).toFixed(1)} 万`
                      : '业务影响接口不可用',
                  },
                ].map((row) => (
                  <div key={row.label} className="flex items-start justify-between gap-3 py-3 at-hairline-b">
                    <dt className="at-sm shrink-0" style={{ color: 'var(--at-ink)' }}>
                      {row.label}
                    </dt>
                    <dd className="min-w-0 text-right break-words">
                      <span className="at-sm at-mono break-all" style={{ color: 'var(--at-ink)' }}>
                        {row.value}
                      </span>
                      <span className="at-meta at-subtle block">{row.hint}</span>
                    </dd>
                  </div>
                ))}
              </dl>
              {vitals && vitals.agents.total > 0 && (
                <p className="at-meta at-subtle mt-3 flex flex-wrap items-center gap-1.5">
                  <ShieldAlert className="h-3 w-3 shrink-0" aria-hidden="true" />
                  编制 {vitals.agents.total} 人 · 在岗 {vitals.agents.production} · 带教 {vitals.agents.training} · 待入职{' '}
                  {vitals.agents.recruit}
                </p>
              )}
            </div>
          </section>
        </div>
      </div>
    </Layout>
  )
}
