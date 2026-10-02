import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  AlertCircle, Lightbulb, Clock, MessageSquare,
  Users, Star, Download, ChevronRight, AlertTriangle,
  CheckCircle, RefreshCw, GitCompare, BarChart3, History, Zap,
} from 'lucide-react'
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  LineChart, Line,
} from 'recharts'
import Layout from '@/components/Layout'
import { Button } from '@/components/ui/Button'
import { Card, CardBody } from '@/components/ui/Card'
import { PageHeader } from '@/components/ui/PageHeader'
import { MetricGrid, type Metric } from '@/components/ui/MetricGrid'
import { JsonView } from '@/components/ui/JsonView'
import { getAgent } from '@/api/agents'
import {
  getLoopStats, getLoopInsights, applyOptimization,
  type LoopStats,
  type LoopInsights, type OptimizationHistoryItem, type AgentVersionSnapshot,
} from '@/api/loop'
import { Agent } from '@/types'

// ============================================================
// recharts 图表主题（使用 CSS 变量，自适应明暗）— 必须保留
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

// 图表主色（品牌色 #1E3A5F）
const CHART_PRIMARY = '#1E3A5F'

// ============================================================
// 类型定义
// ============================================================
type TimeRange = '7d' | '30d' | '90d'
type TabKey = 'feedback' | 'gaps' | 'status'
type StatusSubTabKey = 'history' | 'versions' | 'rag'

// ============================================================
// 配置
// ============================================================
const timeRanges: { key: TimeRange; label: string }[] = [
  { key: '7d', label: '7天' },
  { key: '30d', label: '30天' },
  { key: '90d', label: '90天' },
]

const tabs: { key: TabKey; label: string }[] = [
  { key: 'feedback', label: '反馈分析' },
  { key: 'gaps', label: '知识缺口' },
  { key: 'status', label: '优化历史' },
]

const statusSubTabs: { key: StatusSubTabKey; label: string; icon: typeof History }[] = [
  { key: 'history', label: '优化历史', icon: History },
  { key: 'versions', label: '版本对比', icon: GitCompare },
  { key: 'rag', label: 'RAG 质量', icon: BarChart3 },
]

const priorityConfig: Record<string, { bg: string; text: string }> = {
  high: { bg: 'bg-error/10', text: 'text-error' },
  medium: { bg: 'bg-warning/10', text: 'text-warning' },
  low: { bg: 'bg-text-tertiary/10', text: 'text-text-secondary' },
}

// ============================================================
// 数据类型定义 — 监控面板
// ============================================================

// P0-1b: 所有监控数据已接入后端 GET /loop/{agent_id}/stats 真实聚合接口
// 以下仅保留派生类型定义与配置映射，不再使用任何 hardcoded mock 数据

/** 会话行类型（从 LoopStats.recent_sessions 派生） */
type SessionRow = LoopStats['recent_sessions'][number]

/** 错误日志类型（从 LoopStats.error_logs 派生） */
type ErrorLog = LoopStats['error_logs'][number]

// ============================================================
// 辅助函数
// ============================================================

/** 智能体状态映射 */
const agentStatusConfig: Record<Agent['status'], { dot: string; label: string }> = {
  ready: { dot: 'dot-success', label: '在线' },
  processing: { dot: 'dot-warning', label: '处理中' },
  error: { dot: 'dot-error', label: '异常' },
}

/** 会话状态映射 */
const sessionStatusConfig: Record<SessionRow['status'], { dot: string; label: string }> = {
  completed: { dot: 'dot-success', label: '已完成' },
  active: { dot: 'dot-warning', label: '进行中' },
  interrupted: { dot: 'dot-error', label: '已中断' },
}

/** 满意度星级颜色 */
const satisfactionColor = (rating: number): string => {
  if (rating >= 5) return 'text-success'
  if (rating >= 4) return 'text-text-primary'
  if (rating >= 3) return 'text-warning'
  return 'text-error'
}

/** 错误日志级别样式 */
const logLevelConfig: Record<ErrorLog['level'], { text: string; bg: string }> = {
  ERROR: { text: 'text-error', bg: 'bg-error/10' },
  WARN: { text: 'text-warning', bg: 'bg-warning/10' },
  INFO: { text: 'text-info', bg: 'bg-info/10' },
}

// ============================================================
// 优化历史子组件
// ============================================================

function OptimizationHistoryList({
  items,
  onApply,
  applyingId,
}: {
  items: OptimizationHistoryItem[]
  onApply?: (optId: string) => void
  applyingId?: string | null
}) {
  if (items.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-text-tertiary">暂无优化历史</p>
        <p className="text-xs text-text-muted mt-1.5 leading-relaxed max-w-md mx-auto">
          持续优化器每日定时分析协作反馈与知识缺口并生成优化项，
          生成后即可在此查看与应用。
        </p>
      </div>
    )
  }

  const typeLabel: Record<string, string> = {
    feedback: '反馈分析',
    gap: '知识缺口',
    optimization: '检索优化',
  }

  const typeColor: Record<string, string> = {
    feedback: 'bg-info/10 text-info',
    gap: 'bg-warning/10 text-warning',
    optimization: 'bg-success/10 text-success',
  }

  return (
    <div className="space-y-3">
      {items.map((opt, index) => (
        <div key={opt.id || index} className="flex gap-3">
          <div className="flex flex-col items-center">
            <div className="w-8 h-8 rounded-full bg-brand-50 flex items-center justify-center flex-shrink-0">
              <Clock className="h-4 w-4 text-brand-500" />
            </div>
            {index < items.length - 1 && (
              <div className="w-px flex-1 bg-border-subtle my-1"></div>
            )}
          </div>
          <Card className="flex-1 mb-3">
            <CardBody>
              <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded text-caption font-medium ${typeColor[opt.type] || 'bg-text-tertiary/10 text-text-secondary'}`}>
                    {typeLabel[opt.type] || opt.type}
                  </span>
                  {opt.applied && (
                    <span className="inline-flex items-center gap-1 text-caption text-success">
                      <CheckCircle className="w-3 h-3" />
                      已应用
                    </span>
                  )}
                </div>
                <span className="text-caption text-text-tertiary">
                  {opt.created_at ? new Date(opt.created_at).toLocaleString('zh-CN') : '—'}
                </span>
              </div>
              {opt.input && Object.keys(opt.input).length > 0 && (
                <div className="mb-2">
                  <JsonView value={opt.input} title="输入" />
                </div>
              )}
              {opt.output && Object.keys(opt.output).length > 0 && (
                <div className="mb-2">
                  <JsonView value={opt.output} title="输出" />
                </div>
              )}
              {/* O-08: 未应用的优化记录显示「应用优化」按钮，打通闭环 */}
              {!opt.applied && opt.id && onApply && (
                <div className="mt-3 flex justify-end">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={applyingId !== null && applyingId !== undefined}
                    onClick={() => onApply(opt.id)}
                  >
                    <Zap className="w-3.5 h-3.5" />
                    {applyingId === opt.id ? '应用中...' : '应用优化'}
                  </Button>
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      ))}
    </div>
  )
}

// ============================================================
// 版本对比子组件
// ============================================================

function VersionComparison({ versions }: { versions: AgentVersionSnapshot[] }) {
  if (versions.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-text-tertiary">暂无版本记录</p>
        <p className="text-xs text-text-muted mt-1.5 leading-relaxed max-w-md mx-auto">
          每次修改员工配置或应用优化项时，系统都会自动保存版本快照，
          用于后续对比与回滚。
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {versions.map((v, index) => (
        <Card key={v.id || index} className={v.is_active ? 'border-brand-200' : ''}>
          <CardBody>
            <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm font-semibold text-text-primary">{v.version}</span>
                {v.is_active && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-caption bg-success/10 text-success">
                    <CheckCircle className="w-3 h-3" />
                    当前激活
                  </span>
                )}
              </div>
              <span className="text-caption text-text-tertiary">
                {v.created_at ? new Date(v.created_at).toLocaleString('zh-CN') : '—'}
              </span>
            </div>
            <p className="text-body text-text-secondary mb-3">{v.changelog || '无变更说明'}</p>
            {v.config_snapshot && (
              <div className="bg-canvas p-3 rounded text-caption text-text-tertiary overflow-x-auto">
                <div className="font-medium text-text-secondary mb-1">配置快照</div>
                {v.config_snapshot.system_prompt && (
                  <div className="mb-1 truncate">系统提示：{v.config_snapshot.system_prompt}</div>
                )}
                {v.config_snapshot.skills && v.config_snapshot.skills.length > 0 && (
                  <div className="mb-1">技能：{v.config_snapshot.skills.join('、')}</div>
                )}
                {v.config_snapshot.params && (
                  <div className="mt-1">
                    <JsonView value={v.config_snapshot.params} title="参数" />
                  </div>
                )}
              </div>
            )}
          </CardBody>
        </Card>
      ))}
    </div>
  )
}

// ============================================================
// RAG 质量趋势子组件
// ============================================================

function RAGQualityPanel({ trend, latest }: { trend: import('@/api/loop').RAGTrendPoint[]; latest: import('@/api/loop').RAGLatest | null | undefined }) {
  const metricCards = [
    { key: 'faithfulness', label: '忠实度', value: latest?.faithfulness ?? 0 },
    { key: 'answer_relevancy', label: '答案相关性', value: latest?.answer_relevancy ?? 0 },
    { key: 'context_precision', label: '检索精度', value: latest?.context_precision ?? 0 },
    { key: 'context_recall', label: '召回率', value: latest?.context_recall ?? 0 },
  ]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {metricCards.map((m) => (
          <div key={m.key} className="bg-surface border border-border-default rounded-lg p-4">
            <div className="text-xs text-text-tertiary mb-1">{m.label}</div>
            <div className="font-mono text-2xl font-bold text-text-primary">
              {(m.value * 100).toFixed(1)}<span className="text-sm font-normal text-text-tertiary">%</span>
            </div>
          </div>
        ))}
      </div>

      {trend.length > 0 ? (
        <div className="bg-surface border border-border-default rounded-xl p-4">
          <h4 className="text-sm font-medium text-text-primary mb-3">RAG 质量趋势（7/30/90 天）</h4>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={trend} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid {...chartGrid} />
              <XAxis dataKey="date" {...chartAxis} tickLine={false} axisLine={false} />
              <YAxis domain={[0, 1]} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} {...chartAxis} tickLine={false} axisLine={false} />
              <Tooltip
                {...chartTooltip}
                formatter={(value) => `${(typeof value === 'number' ? value * 100 : 0).toFixed(1)}%`}
              />
              <Line type="monotone" dataKey="faithfulness" name="忠实度" stroke="#1E3A5F" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="answer_relevancy" name="答案相关性" stroke="#4A90A4" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="context_precision" name="检索精度" stroke="#E6A23C" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="context_recall" name="召回率" stroke="#5A8F7B" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="text-center py-8">
          <p className="text-text-tertiary">暂无 RAG 评估数据</p>
          <p className="text-xs text-text-muted mt-1.5 leading-relaxed max-w-md mx-auto">
            每次 AI 回答后会即时评估该次检索与回答质量；产生足够对话后，
            此处会呈现召回率等质量指标的趋势。
          </p>
        </div>
      )}
    </div>
  )
}

// ============================================================
// 主组件
// ============================================================
export default function LoopDashboard() {
  const { agentId } = useParams<{ agentId: string }>()
  const navigate = useNavigate()
  const [agent, setAgent] = useState<Agent | null>(null)
  const [stats, setStats] = useState<LoopStats | null>(null)
  const [insights, setInsights] = useState<LoopInsights | null>(null)
  const [loading, setLoading] = useState(true)
  const [statsLoading, setStatsLoading] = useState(false)
  // P0-1b 复查补全：增加 error 状态展示给用户（之前只 console.error）
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<TabKey>('feedback')
  const [statusSubTab, setStatusSubTab] = useState<StatusSubTabKey>('history')
  const [timeRange, setTimeRange] = useState<TimeRange>('7d')
  // O-08: Loop 闭环应用优化状态
  const [applyingId, setApplyingId] = useState<string | null>(null)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  // 初始加载：agent + 反馈/缺口/状态 + 默认 7d 统计
  // 使用 Promise.allSettled 容错：即使部分 API 失败（如新 agent 无反馈数据），
  // 仍能展示 agent 基本信息与已成功的部分，避免整体白屏
  const loadData = useCallback(async () => {
    if (!agentId) return
    try {
      setLoading(true)
      setError(null)
      const results = await Promise.allSettled([
        getAgent(agentId),
        getLoopStats(agentId, 7),
        getLoopInsights(agentId, 30),
      ])
      const [agentR, statsR, insightsR] = results

      // agent 必须加载成功，否则视为整体失败
      if (agentR.status === 'fulfilled') {
        setAgent(agentR.value)
      } else {
        const msg = agentR.reason instanceof Error ? agentR.reason.message : '智能体不存在'
        setError(`加载失败: ${msg}`)
        console.error('加载 agent 失败:', agentR.reason)
        return
      }

      // 其余 API 失败时降级为 null，由 UI 渲染空状态
      if (statsR.status === 'fulfilled') setStats(statsR.value)
      else console.warn('统计数据加载失败（降级为空）:', statsR.reason)

      if (insightsR.status === 'fulfilled') setInsights(insightsR.value)
      else console.warn('Loop 洞察加载失败（降级为空）:', insightsR.reason)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误'
      setError(`加载数据失败: ${msg}`)
      console.error('加载数据失败:', err)
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => {
    loadData()
  }, [loadData])

  // P0-1b: 时间范围切换时重新拉取统计聚合数据
  const loadStats = useCallback(async (range: TimeRange) => {
    if (!agentId) return
    const daysMap: Record<TimeRange, number> = { '7d': 7, '30d': 30, '90d': 90 }
    try {
      setStatsLoading(true)
      const statsData = await getLoopStats(agentId, daysMap[range])
      setStats(statsData)
    } catch (error) {
      console.error('加载统计数据失败:', error)
    } finally {
      setStatsLoading(false)
    }
  }, [agentId])

  const handleTimeRangeChange = useCallback((range: TimeRange) => {
    setTimeRange(range)
    loadStats(range)
  }, [loadStats])

  // O-08: toast 自动清除（3 秒后），组件卸载时清理定时器避免内存泄漏
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 3000)
    return () => clearTimeout(timer)
  }, [toast])

  // O-08: 应用优化记录到 Agent 配置（打通 Loop 闭环）
  const handleApplyOptimization = useCallback(async (optId: string) => {
    if (!agentId) return
    try {
      setApplyingId(optId)
      const result = await applyOptimization(agentId, optId)
      setToast({
        type: 'success',
        message: `优化已应用（${result.summary}），已创建版本快照 v${result.agent_version} 可回滚`,
      })
      // 刷新洞察数据（优化历史 applied 状态 + 版本快照列表）
      try {
        const newInsights = await getLoopInsights(agentId, 30)
        setInsights(newInsights)
      } catch (e) {
        console.warn('刷新 Loop 洞察失败:', e)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误'
      setToast({ type: 'error', message: `应用优化失败: ${msg}` })
    } finally {
      setApplyingId(null)
    }
  }, [agentId])

  // 趋势数据（来自后端聚合）
  const trendData = useMemo(() => stats?.trend || [], [stats?.trend])

  // 时间范围标签
  const timeRangeLabel = useMemo(() => {
    const map: Record<TimeRange, string> = { '7d': '近7天', '30d': '近30天', '90d': '近90天' }
    return map[timeRange]
  }, [timeRange])

  // P0-1b: 派生指标（从真实统计数据计算）
  const responseTimeData = useMemo(() => stats?.response_time || [], [stats?.response_time])
  const satisfactionPieData = useMemo(() => stats?.satisfaction_pie || [], [stats?.satisfaction_pie])
  const recentSessions = useMemo(() => stats?.recent_sessions || [], [stats?.recent_sessions])
  const errorLogs = stats?.error_logs || []
  const alerts = stats?.alerts || []

  // 协作量环比（最近一天 vs 前一天）
  const sessionTrendPct = useMemo(() => {
    if (trendData.length < 2) return null
    const last = trendData[trendData.length - 1]?.count || 0
    const prev = trendData[trendData.length - 2]?.count || 0
    if (prev === 0) return last > 0 ? 100 : 0
    return Math.round(((last - prev) / prev) * 100)
  }, [trendData])

  // 平均响应时间（基于桶中位数加权）
  const avgResponseMs = useMemo(() => {
    const midpoints = [100, 350, 750, 1500, 2500]
    let total = 0, count = 0
    responseTimeData.forEach((b, i) => {
      total += b.count * (midpoints[i] || 0)
      count += b.count
    })
    return count > 0 ? Math.round(total / count) : null
  }, [responseTimeData])

  // 活跃用户数（最近会话中去重用户数）
  const activeUsers = useMemo(() => {
    const users = new Set(recentSessions.map(s => s.user))
    return users.size
  }, [recentSessions])

  // 平均满意度评分（满意5/一般4/不满意2 加权）
  const avgRating = useMemo(() => {
    const total = satisfactionPieData.reduce((s, d) => s + d.value, 0)
    if (total === 0) return null
    const score = satisfactionPieData.reduce((s, d) => {
      const v = d.name === '满意' ? 5 : d.name === '一般' ? 4 : 2
      return s + d.value * v
    }, 0)
    return Math.round((score / total) * 10) / 10
  }, [satisfactionPieData])

  // R5: 满意度百分比改为从 insights.feedback_analysis 聚合获取
  const satisfactionPct = insights?.feedback_analysis
    ? Math.round(insights.feedback_analysis.satisfaction_rate * 100)
    : 0

  // R5: 总会话数改为从 insights.feedback_analysis 聚合获取
  const totalSessions = insights?.feedback_analysis?.total_feedback || 0

  // 概览指标（MetricGrid）
  // 必须在任何 early return 之前调用，否则违反 Hooks 调用顺序规则
  const overviewMetrics: Metric[] = useMemo(() => [
    {
      key: 'sessions',
      icon: MessageSquare,
      value: totalSessions.toLocaleString(),
      label: '总会话数',
      tone: 'brand',
      variant: 'compact',
      trend: sessionTrendPct ?? undefined,
    },
    {
      key: 'response',
      icon: Clock,
      value: avgResponseMs ?? '—',
      unit: 'ms',
      label: '平均响应时间',
      tone: 'info',
      variant: 'compact',
    },
    {
      key: 'satisfaction',
      icon: CheckCircle,
      value: satisfactionPct,
      unit: '%',
      label: '满意率',
      tone: satisfactionPct >= 80 ? 'success' : satisfactionPct >= 60 ? 'warning' : 'error',
      variant: 'compact',
    },
    {
      key: 'users',
      icon: Users,
      value: activeUsers,
      label: '活跃用户数',
      tone: 'brand',
      variant: 'compact',
    },
  ], [totalSessions, sessionTrendPct, avgResponseMs, satisfactionPct, activeUsers])

  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="skeleton h-12 w-12 rounded-full"></div>
        </div>
      </Layout>
    )
  }

  // P0-1b 复查补全：错误状态展示（可重试）
  if (error && !agent) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="text-center max-w-md space-y-4">
            <div className="w-12 h-12 mx-auto rounded-full bg-error/10 flex items-center justify-center">
              <AlertCircle className="w-6 h-6 text-error" />
            </div>
            <h2 className="text-h3 text-text-primary">加载失败</h2>
            <p className="text-body text-text-secondary">{error}</p>
            <Button onClick={() => loadData()} variant="primary">
              <RefreshCw className="w-4 h-4" />
              重试
            </Button>
          </div>
        </div>
      </Layout>
    )
  }

  if (!agent) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="text-center">
            <h2 className="text-h3 text-text-primary mb-3">智能体不存在</h2>
            <Button onClick={() => navigate('/')}>返回首页</Button>
          </div>
        </div>
      </Layout>
    )
  }

  // R5: 关键词数据（知识缺口 Tab 用）改为从 insights.knowledge_gaps 聚合获取
  const keywordData = (insights?.knowledge_gaps?.top_keywords || []).slice(0, 10).map(k => ({
    name: k.keyword,
    count: k.count,
  }))

  // 智能体状态
  const statusCfg = agentStatusConfig[agent.status] || agentStatusConfig.ready

  return (
    <Layout>
      <div className="max-w-[1600px] mx-auto space-y-8">
        {/* O-08: 应用优化结果 toast 通知 */}
        <AnimatePresence>
          {toast && (
            <motion.div
              initial={{ opacity: 0, y: -20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg border max-w-md ${
                toast.type === 'success'
                  ? 'bg-success/10 border-success/30 text-success'
                  : 'bg-error/10 border-error/30 text-error'
              }`}
            >
              <div className="flex items-start gap-2">
                {toast.type === 'success' ? (
                  <CheckCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                ) : (
                  <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                )}
                <span className="text-sm">{toast.message}</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        {/* ============================================================
            1. 页面头部：面包屑 + 衬线标题 + 时间范围 + 导出
            ============================================================ */}
        <PageHeader
          title="运行监控"
          actions={
            <div className="flex items-center gap-3">
              <div className="flex gap-1">
                {timeRanges.map(r => (
                  <button
                    key={r.key}
                    onClick={() => handleTimeRangeChange(r.key)}
                    disabled={statsLoading}
                    className={`px-3 py-1.5 text-sm rounded-md font-medium border transition-colors ${
                      timeRange === r.key
                        ? 'bg-brand-500 text-white border-brand-500'
                        : 'bg-surface text-text-tertiary border-border-default hover:text-text-primary hover:bg-elevated'
                    } ${statsLoading ? 'opacity-60 cursor-not-allowed' : ''}`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
              <Button variant="outline" size="sm" onClick={() => window.print()}>
                <Download className="w-4 h-4" />
                导出报告
              </Button>
            </div>
          }
        >
          <nav className="flex items-center gap-1.5 text-sm text-text-tertiary mb-1">
            <Link to="/" className="hover:text-text-primary transition-colors">
              控制台
            </Link>
            <ChevronRight className="w-3.5 h-3.5" />
            <span className="text-text-secondary">智能体监控</span>
          </nav>
          <p className="text-sm text-text-tertiary flex items-center gap-2">
            <span className={`dot ${statusCfg.dot}`}></span>
            <span className="text-text-secondary font-medium">{agent.name}</span>
            <span>·</span>
            <span>{statusCfg.label}</span>
            {agent.description && (
              <>
                <span>·</span>
                <span className="truncate max-w-md">{agent.description}</span>
              </>
            )}
          </p>
        </PageHeader>

        {/* ============================================================
            2. 实时指标卡片行（MetricGrid）
            ============================================================ */}
        <MetricGrid metrics={overviewMetrics} columns={4} />

        {/* ============================================================
            3. 协作量趋势图（全宽）
            ============================================================ */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25 }}
          className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
        >
          <div className="flex items-center justify-between mb-4">
            <div className="space-y-2">
              <h2 className="font-serif-display text-lg font-semibold text-text-primary">
                协作量趋势
              </h2>
              <div className="brand-rule"></div>
            </div>
            <span className="text-xs text-text-tertiary">{timeRangeLabel}</span>
          </div>
          {statsLoading ? (
            <div className="min-h-[260px] flex items-center justify-center">
              <div className="skeleton h-8 w-8 rounded-full"></div>
            </div>
          ) : trendData.length === 0 ? (
            <div className="min-h-[260px] flex flex-col items-center justify-center text-text-tertiary text-sm">
              <p>暂无协作数据</p>
              <p className="text-xs text-text-muted mt-1.5">
                与数字员工的协作产生会话记录后，此处会呈现所选时段（近 7/30/90 天）的每日趋势。
              </p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={trendData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="gradConversations" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={CHART_PRIMARY} stopOpacity={0.2} />
                    <stop offset="95%" stopColor={CHART_PRIMARY} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...chartGrid} vertical={false} />
                <XAxis dataKey="time" {...chartAxis} tickLine={false} axisLine={false} />
                <YAxis {...chartAxis} tickLine={false} axisLine={false} />
                <Tooltip {...chartTooltip} />
                <Area
                  type="monotone"
                  dataKey="count"
                  name="协作数"
                  stroke={CHART_PRIMARY}
                  strokeWidth={2}
                  fill="url(#gradConversations)"
                  dot={{ fill: 'var(--bg-surface)', stroke: CHART_PRIMARY, strokeWidth: 2, r: 3 }}
                  activeDot={{ r: 5, stroke: CHART_PRIMARY, strokeWidth: 2, fill: 'var(--bg-surface)' }}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
          <div className="flex gap-4 text-xs text-text-tertiary mt-3">
            <span className="flex items-center gap-1.5">
              <span className="inline-block w-2 h-2 rounded-full" style={{ background: CHART_PRIMARY }}></span>
              协作数
            </span>
          </div>
        </motion.div>

        {/* ============================================================
            4. 双栏图表区：响应时间分布 + 用户满意度
            ============================================================ */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* 左：响应时间分布柱状图 */}
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
            className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
          >
            <div className="flex items-center justify-between mb-4">
              <div className="space-y-2">
                <h2 className="font-serif-display text-base font-semibold text-text-primary">
                  响应时间分布
                </h2>
                <div className="brand-rule"></div>
              </div>
              <span className="text-xs text-text-tertiary">按响应耗时</span>
            </div>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={responseTimeData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid {...chartGrid} vertical={false} />
                <XAxis dataKey="range" {...chartAxis} tickLine={false} axisLine={false} />
                <YAxis {...chartAxis} tickLine={false} axisLine={false} />
                <Tooltip {...chartTooltip} cursor={{ fill: 'var(--brand-soft)' }} />
                <Bar dataKey="count" name="会话数" fill={CHART_PRIMARY} radius={[4, 4, 0, 0]} maxBarSize={64} />
              </BarChart>
            </ResponsiveContainer>
            {responseTimeData.length > 0 && responseTimeData.every(b => b.count === 0) && (
              <div className="text-center text-text-tertiary text-xs mt-2">
                暂无响应时间数据（基于 token 消耗代理统计）
              </div>
            )}
          </motion.div>

          {/* 右：用户满意度饼图 */}
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.35 }}
            className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
          >
            <div className="flex items-center justify-between mb-4">
              <div className="space-y-2">
                <h2 className="font-serif-display text-base font-semibold text-text-primary">
                  用户满意度
                </h2>
                <div className="brand-rule"></div>
              </div>
              <span className="text-xs text-text-tertiary">满意 / 一般 / 不满意</span>
            </div>
            {satisfactionPieData.length === 0 || satisfactionPieData.every(d => d.value === 0) ? (
              <div className="h-[240px] flex items-center justify-center text-text-tertiary text-sm">
                暂无满意度数据
              </div>
            ) : (
              <div className="flex items-center gap-4">
                <ResponsiveContainer width="55%" height={240}>
                  <PieChart>
                    <Pie
                      data={satisfactionPieData}
                      dataKey="value"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      outerRadius={80}
                      innerRadius={40}
                      paddingAngle={2}
                    >
                      {satisfactionPieData.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip {...chartTooltip} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="flex-1 space-y-3">
                  {satisfactionPieData.map(item => {
                    const total = satisfactionPieData.reduce((sum, d) => sum + d.value, 0)
                    const pct = total > 0 ? ((item.value / total) * 100).toFixed(1) : '0.0'
                    return (
                      <div key={item.name} className="flex items-center justify-between">
                        <span className="flex items-center gap-2 text-sm text-text-secondary">
                          <span
                            className="inline-block w-2.5 h-2.5 rounded-sm"
                            style={{ background: item.color }}
                          ></span>
                          {item.name}
                        </span>
                        <span className="font-mono tabular-nums text-sm text-text-primary">
                          {item.value.toLocaleString()}
                          <span className="text-text-tertiary ml-1">({pct}%)</span>
                        </span>
                      </div>
                    )
                  })}
                  <div className="pt-3 border-t border-border-subtle">
                    <div className="text-xs text-text-tertiary mb-1">平均评分</div>
                    <div className="font-mono tabular-nums text-2xl font-bold text-text-primary">
                      {avgRating !== null ? avgRating.toFixed(1) : '—'}
                      <span className="text-text-tertiary text-base font-normal"> / 5.0</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </motion.div>
        </div>

        {/* ============================================================
            4.5 告警面板（dot 状态点 + divide-y 分隔列表）
            ============================================================ */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.38 }}
          className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
        >
          <div className="flex items-center justify-between mb-4">
            <div className="space-y-2">
              <h2 className="font-serif-display text-base font-semibold text-text-primary">
                告警
              </h2>
              <div className="brand-rule" />
            </div>
            <span className="text-xs text-text-tertiary">{alerts.length} 条</span>
          </div>
          <div className="divide-y divide-border-default">
            {alerts.length === 0 ? (
              <div className="py-8 text-center text-text-tertiary text-sm">暂无告警</div>
            ) : (
              alerts.map((alert, index) => (
                <div
                  key={index}
                  className="py-3 first:pt-0 last:pb-0 hover:bg-surface-2 -mx-2 px-2 rounded transition-colors cursor-pointer"
                >
                  <div className="flex items-start gap-2.5">
                    <span className={`dot ${alert.dot} mt-1.5 flex-shrink-0`} />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-text-primary">{alert.title}</div>
                      <div className="text-xs text-text-tertiary mt-0.5">{alert.desc}</div>
                      <div className="font-mono tabular-nums text-xs text-text-tertiary mt-1">
                        {alert.time}
                      </div>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </motion.div>

        {/* ============================================================
            5. 最近会话表格
            ============================================================ */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="bg-surface border border-border-default rounded-xl shadow-soft overflow-hidden"
        >
          <div className="px-6 py-4 border-b border-border-subtle flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-primary">最近会话</h2>
            <span className="text-xs text-text-tertiary">{recentSessions.length} 条</span>
          </div>
          {recentSessions.length === 0 ? (
            <div className="py-12 text-center text-text-tertiary text-sm">暂无会话记录</div>
          ) : (
            <div className="overflow-x-auto scrollbar-thin">
              <table className="w-full">
                <thead>
                  <tr className="bg-elevated">
                    <th className="px-6 py-3 text-xs font-medium text-text-tertiary text-left">会话ID</th>
                    <th className="px-6 py-3 text-xs font-medium text-text-tertiary text-left">用户</th>
                    <th className="px-6 py-3 text-xs font-medium text-text-tertiary text-left">开始时间</th>
                    <th className="px-6 py-3 text-xs font-medium text-text-tertiary text-right">消息数</th>
                    <th className="px-6 py-3 text-xs font-medium text-text-tertiary text-left">响应时间</th>
                    <th className="px-6 py-3 text-xs font-medium text-text-tertiary text-left">满意度</th>
                    <th className="px-6 py-3 text-xs font-medium text-text-tertiary text-left">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {recentSessions.map((session) => {
                    const sCfg = sessionStatusConfig[session.status]
                    return (
                      <tr
                        key={session.id}
                        onClick={() => navigate(`/chat/${agentId}`)}
                        className="border-b border-border-subtle last:border-b-0 hover:bg-surface-2 transition-colors cursor-pointer"
                      >
                        <td className="px-6 py-3 text-sm font-mono tabular-nums text-text-primary">
                          {session.id}
                        </td>
                        <td className="px-6 py-3 text-sm text-text-secondary">{session.user}</td>
                        <td className="px-6 py-3 text-sm font-mono tabular-nums text-text-tertiary">
                          {session.startTime}
                        </td>
                        <td className="px-6 py-3 text-sm font-mono tabular-nums text-right text-text-primary">
                          {session.messages}
                        </td>
                        <td className="px-6 py-3 text-sm font-mono tabular-nums text-text-tertiary">
                          {session.responseTime}
                        </td>
                        <td className="px-6 py-3 text-sm">
                          {session.satisfaction !== null ? (
                            <span className={`flex items-center gap-0.5 ${satisfactionColor(session.satisfaction)}`}>
                              {Array.from({ length: 5 }).map((_, i) => (
                                <Star
                                  key={i}
                                  className={`w-3.5 h-3.5 ${i < session.satisfaction! ? 'fill-current' : 'opacity-20'}`}
                                />
                              ))}
                            </span>
                          ) : (
                            <span className="text-text-tertiary">—</span>
                          )}
                        </td>
                        <td className="px-6 py-3 text-sm text-text-primary">
                          <span className="inline-flex items-center gap-1.5">
                            <span className={`dot ${sCfg.dot}`}></span>
                            {sCfg.label}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </motion.div>

        {/* ============================================================
            6. 错误日志面板
            ============================================================ */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.45 }}
          className="bg-surface-2 rounded-xl p-4 border border-border-subtle"
        >
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-warning" />
              <h2 className="text-sm font-semibold text-text-primary">错误日志</h2>
            </div>
            <span className="text-xs text-text-tertiary">{errorLogs.length} 条</span>
          </div>
          <div className="space-y-2">
            {errorLogs.length === 0 ? (
              <div className="py-8 text-center text-text-tertiary text-sm flex items-center justify-center gap-2">
                <CheckCircle className="w-4 h-4 text-success" />
                无错误记录
              </div>
            ) : (
              errorLogs.map((log, index) => {
                const cfg = logLevelConfig[log.level]
                return (
                  <div
                    key={index}
                    className="flex items-start gap-3 p-3 bg-surface rounded-lg border border-border-subtle hover:border-border-default transition-colors"
                  >
                    <span className={`px-1.5 py-0.5 rounded text-xs font-mono font-medium ${cfg.bg} ${cfg.text} flex-shrink-0`}>
                      {log.level}
                    </span>
                    <span className="font-mono tabular-nums text-xs text-text-tertiary flex-shrink-0 mt-0.5">
                      {log.timestamp}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-text-primary">{log.message}</div>
                      {log.detail && (
                        <div className="text-xs text-text-tertiary mt-0.5">{log.detail}</div>
                      )}
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </motion.div>

        {/* ============================================================
            7. 保留：反馈分析 / 知识缺口 / 优化历史（Tab 区）
            ============================================================ */}
        <Card>
          {/* Tab 导航 */}
          <div className="flex border-b border-border-subtle relative">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`px-6 py-3 text-body font-medium transition-colors ${
                  activeTab === tab.key ? 'text-brand-500' : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {tab.label}
              </button>
            ))}
            {/* 下划线动画 */}
            <motion.div
              className="absolute bottom-0 h-0.5 bg-brand-500"
              layoutId="tabUnderline"
              style={{ width: `${100 / tabs.length}%` }}
              animate={{ left: `${tabs.findIndex((t) => t.key === activeTab) * (100 / tabs.length)}%` }}
              transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            />
          </div>

          <CardBody>
            <AnimatePresence mode="wait">
              {/* 反馈分析 Tab */}
              {activeTab === 'feedback' && (
                <motion.div
                  key="feedback"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                >
                  <h3 className="text-h3 text-text-primary mb-4">不满意原因分析</h3>
                  {insights?.feedback_analysis?.issues.length ? (
                    <div className="space-y-3">
                      {insights.feedback_analysis.issues.map((issue, index) => {
                        const cfg = priorityConfig[issue.priority] || priorityConfig.low
                        return (
                          <div
                            key={index}
                            className="p-4 bg-elevated rounded-lg border border-border-subtle"
                          >
                            <div className="flex items-center gap-2 mb-2">
                              <span className={`px-2 py-0.5 rounded text-caption font-medium ${cfg.bg} ${cfg.text}`}>
                                {issue.priority}
                              </span>
                              <AlertCircle className="h-3.5 w-3.5 text-text-tertiary" />
                            </div>
                            <p className="text-body text-text-primary mb-2">{issue.reason}</p>
                            {issue.expected_info && (
                              <p className="text-caption text-text-secondary">期望: {issue.expected_info}</p>
                            )}
                            {issue.improvement && (
                              <p className="text-caption text-success mt-2 flex items-center gap-1">
                                <Lightbulb className="h-3 w-3" />
                                {issue.improvement}
                              </p>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  ) : (
                    <p className="text-text-tertiary text-center py-8">暂无不满意反馈</p>
                  )}
                </motion.div>
              )}

              {/* 知识缺口 Tab */}
              {activeTab === 'gaps' && (
                <motion.div
                  key="gaps"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                >
                  <h3 className="text-h3 text-text-primary mb-4">知识缺口分析</h3>
                  {insights?.knowledge_gaps?.gaps.length ? (
                    <div className="space-y-4">
                      <div className="p-4 bg-info/5 rounded-lg border border-info/20">
                        <h4 className="text-body font-medium text-info mb-2">发现的知识缺口:</h4>
                        <ul className="list-disc list-inside space-y-1">
                          {insights.knowledge_gaps.gaps.map((gap, index) => (
                            <li key={index} className="text-body text-text-secondary">{gap}</li>
                          ))}
                        </ul>
                      </div>
                      {insights.knowledge_gaps.suggestions.length > 0 && (
                        <div className="p-4 bg-success/5 rounded-lg border border-success/20">
                          <h4 className="text-body font-medium text-success mb-2">改进建议:</h4>
                          <ul className="list-disc list-inside space-y-1">
                            {insights.knowledge_gaps.suggestions.map((suggestion, index) => (
                              <li key={index} className="text-body text-text-secondary">{suggestion}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p className="text-text-tertiary text-center py-8">暂未发现知识缺口</p>
                  )}

                  {/* 关键词词云（横向柱状图） */}
                  {keywordData.length > 0 && (
                    <div className="mt-6">
                      <h4 className="text-body font-medium text-text-primary mb-3">高频问题关键词</h4>
                      <ResponsiveContainer width="100%" height={Math.max(180, keywordData.length * 28)}>
                        <BarChart data={keywordData} layout="vertical" margin={{ left: 20 }}>
                          <CartesianGrid {...chartGrid} horizontal={false} />
                          <XAxis type="number" {...chartAxis} />
                          <YAxis
                            type="category"
                            dataKey="name"
                            {...chartAxis}
                            width={80}
                          />
                          <Tooltip {...chartTooltip} />
                          <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                            {keywordData.map((_, index) => (
                              <Cell key={`cell-${index}`} fill={index === 0 ? '#DC2626' : index === 1 || index === 2 ? '#D97706' : '#2563EB'} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </motion.div>
              )}

              {/* 优化历史 Tab */}
              {activeTab === 'status' && (
                <motion.div
                  key="status"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                >
                  {/* 子 Tab 导航 */}
                  <div className="flex gap-2 mb-4 border-b border-border-subtle pb-2">
                    {statusSubTabs.map((sub) => {
                      const Icon = sub.icon
                      return (
                        <button
                          key={sub.key}
                          onClick={() => setStatusSubTab(sub.key)}
                          className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                            statusSubTab === sub.key
                              ? 'bg-brand-50 text-brand-500'
                              : 'text-text-secondary hover:text-text-primary hover:bg-elevated'
                          }`}
                        >
                          <Icon className="w-4 h-4" />
                          {sub.label}
                        </button>
                      )
                    })}
                  </div>

                  {statusSubTab === 'history' ? (
                    <OptimizationHistoryList
                      items={insights?.optimizations || []}
                      onApply={handleApplyOptimization}
                      applyingId={applyingId}
                    />
                  ) : statusSubTab === 'versions' ? (
                    <VersionComparison versions={insights?.versions || []} />
                  ) : (
                    <RAGQualityPanel trend={insights?.rag_trend || []} latest={insights?.rag_latest} />
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </CardBody>
        </Card>
      </div>
    </Layout>
  )
}
