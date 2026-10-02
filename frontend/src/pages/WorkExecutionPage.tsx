/**
 * WorkExecutionPage — AI 员工工作执行仪表盘。
 *
 * 这是 AI 员工「真正干活、交付结果」的实时视图：
 * 1. 总览指标：今日任务数、完成率、在岗员工、平均满意度
 * 2. 实时工作流：来自 collaboration 事件流的最新协作动态（询盘/报价/审批/同步/售后）
 * 3. 员工工作进度表：每个员工的任务总量/完成/失败、响应时间、满意度、KPI
 * 4. 工具使用统计：员工调用各工具的频次
 *
 * 数据来源：workforce（listWorkforce 返回 AgentRunMetrics）+ collaboration（listEvents）。
 * 错误状态：API 失败时显示告警 + 重试按钮（spec.md §2.5 约束）。
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'

import { motion } from 'framer-motion'
import {
  Activity, CheckCircle2, Clock, TrendingUp, Users,
  Zap, AlertCircle, MessagesSquare, ChevronLeft, ChevronRight,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Spinner } from '@/components/ui/Spinner'
import { PageHeader } from '@/components/ui/PageHeader'
import { ApiErrorState } from '@/components/ApiErrorState'
import { MetricGrid, type Metric } from '@/components/ui/MetricGrid'
import { InlineTabs } from '@/components/ui/InlineTabs'
import { EmptyState } from '@/components/ui/EmptyState'
import { CollaborationWorkspace } from '@/components/CollaborationWorkspace'
import LocalConnectionGuide from '@/components/LocalConnectionGuide'
import { LastUpdated } from '@/components/instrument'
import * as workforceApi from '@/api/workforce'
import type { OrchestrationSummary } from '@/api/workforce'
import * as collaborationApi from '@/api/collaboration'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { positionToLabel, formatRelativeTime } from '@/utils/fieldMappings'
import { formatSatisfaction } from '@/utils/format'
import type {
  AgentRunMetrics,
  CollaborationEvent,
  CollaborationEventType,
  LifecycleStage,
} from '@/types'

/** 协作事件类型 → 中文标签 + 颜色 */
const eventLabelMap: Record<CollaborationEventType, { label: string; color: string; bg: string }> = {
  inquiry_received: { label: '收到询盘', color: 'text-info', bg: 'bg-info/10' },
  product_query: { label: '产品查询', color: 'text-brand-500', bg: 'bg-brand-50' },
  quotation_generated: { label: '生成报价', color: 'text-success', bg: 'bg-success/10' },
  approval_submitted: { label: '提交审批', color: 'text-warning', bg: 'bg-warning/10' },
  approval_approved: { label: '审批通过', color: 'text-success', bg: 'bg-success/10' },
  order_synced: { label: '订单同步', color: 'text-info', bg: 'bg-info/10' },
  after_sales: { label: '售后接管', color: 'text-brand-500', bg: 'bg-brand-50' },
  handoff: { label: '任务转交', color: 'text-text-tertiary', bg: 'bg-elevated' },
  escalation: { label: '升级处理', color: 'text-error', bg: 'bg-error/10' },
  error: { label: '执行错误', color: 'text-error', bg: 'bg-error/10' },
  opportunity_created: { label: '创建商机', color: 'text-info', bg: 'bg-info/10' },
  approval_flow_created: { label: '创建审批流', color: 'text-warning', bg: 'bg-warning/10' },
  deal_closed: { label: '成交', color: 'text-success', bg: 'bg-success/10' },
}

/** 生命周期阶段 → 中文标签 */
const lifecycleLabel: Record<LifecycleStage, string> = {
  recruit: '招募中',
  training: '训练中',
  production: '生产中',
  evaluation: '绩效评估',
  continuous_learning: '持续学习',
  promotion: '晋升中',
  retired: '已退休',
}

// ============================================================
// 实时工作流聚合视图（同类信息聚合：按类型 / 按员工统计）
// ============================================================

function AggregateWorkflow({
  events,
  agentNameMap,
}: {
  events: CollaborationEvent[]
  agentNameMap: Map<string, string>
}) {
  // 按事件类型聚合
  const byType = useMemo(() => {
    const map = new Map<CollaborationEventType, number>()
    events.forEach((e) => map.set(e.event_type, (map.get(e.event_type) || 0) + 1))
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1])
  }, [events])

  // 按来源员工聚合
  const byAgent = useMemo(() => {
    const map = new Map<string, number>()
    events.forEach((e) => {
      const key = e.source_agent_id || 'system'
      map.set(key, (map.get(key) || 0) + 1)
    })
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1])
  }, [events])

  const maxTypeCount = Math.max(1, ...byType.map(([, c]) => c))

  return (
    <div className="p-4 space-y-4">
      {/* 按类型 */}
      <div>
        <div className="text-xs font-medium text-text-tertiary mb-2">按事件类型</div>
        <div className="space-y-2">
          {byType.map(([type, count]) => {
            const cfg = eventLabelMap[type] || eventLabelMap.handoff
            return (
              <div key={type} className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.color}`} style={{ background: 'currentColor' }} aria-hidden="true" />
                <span className={`text-xs w-20 flex-shrink-0 ${cfg.color}`}>{cfg.label}</span>
                <div className="flex-1 h-1.5 bg-elevated rounded-full overflow-hidden">
                  <div className={`h-full rounded-full ${cfg.color.replace('text-', 'bg-')}`} style={{ width: `${(count / maxTypeCount) * 100}%` }} />
                </div>
                <span className="text-xs text-text-primary font-medium w-6 text-right flex-shrink-0">{count}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* 按员工 */}
      <div>
        <div className="text-xs font-medium text-text-tertiary mb-2">按员工</div>
        <div className="space-y-1.5">
          {byAgent.map(([agentId, count]) => (
            <div key={agentId} className="flex items-center justify-between text-xs">
              <span className="text-text-secondary truncate">
                {agentNameMap.get(agentId) || (agentId === 'system' ? '系统' : '未知')}
              </span>
              <span className="text-text-primary font-medium flex-shrink-0">× {count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function WorkExecutionPage() {
  const enterpriseId = useEnterpriseId()
  const [agents, setAgents] = useState<AgentRunMetrics[]>([])
  const [events, setEvents] = useState<CollaborationEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 数据新鲜度：修复「30s 静默轮询无任何提示，数据跳变突兀」的问题
  const [refreshing, setRefreshing] = useState(false)
    const [lastUpdated, setLastUpdated] = useState<number | null>(null)
  // 轮询、可见性恢复与手动刷新共用同一请求锁，避免慢网络下多个响应交错覆盖视图。
  const loadInFlightRef = useRef(false)
  // 编排能力摘要（P0-2）：失败时优雅降级，不阻塞页面

  const [orchestration, setOrchestration] = useState<OrchestrationSummary | null>(null)
  const [orchestrationFailed, setOrchestrationFailed] = useState(false)

  // Tab 结构：workbench=协作工作台（CollaborationWorkspace），progress=员工进度（表格+实时工作流+工具统计）
  const [activeTab, setActiveTab] = useState<'workbench' | 'progress'>('workbench')
  // 实时工作流视图：stream=时间流，aggregate=聚合统计（同类信息聚合）
  const [workflowView, setWorkflowView] = useState<'stream' | 'aggregate'>('stream')
  // 员工进度表分页
  const [currentPage, setCurrentPage] = useState(1)
  const pageSize = 10

    const loadData = useCallback(async () => {
    if (!enterpriseId || loadInFlightRef.current) return
    loadInFlightRef.current = true
    setLoading(true)

    setError(null)
    try {
      const [workforceResp, eventsResp, orchestrationResp] = await Promise.allSettled([
        workforceApi.listWorkforce(enterpriseId),
        collaborationApi.listEvents(enterpriseId, { limit: 30 }),
        workforceApi.getOrchestration(enterpriseId),
      ])
      if (workforceResp.status === 'fulfilled') {
        setAgents(workforceResp.value.items)
      } else {
        // workforce 失败属于致命错误（核心数据）
        throw workforceResp.reason
      }
      if (eventsResp.status === 'fulfilled') {
        setEvents(eventsResp.value.items)
      } else {
        // 事件流失败不阻塞，仅清空
        setEvents([])
      }
      if (orchestrationResp.status === 'fulfilled') {
        setOrchestration(orchestrationResp.value)
        setOrchestrationFailed(false)
      } else {
        // 编排能力失败静默降级：不阻塞页面，仅标记以便展示空态
        setOrchestration(null)
        setOrchestrationFailed(true)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载工作执行数据失败')
    } finally {
      loadInFlightRef.current = false
      setLoading(false)
      setRefreshing(false)
      setLastUpdated(Date.now())
    }
  }, [enterpriseId])

  const refreshData = useCallback(() => {
    if (loadInFlightRef.current) return
    setRefreshing(true)
    void loadData()
  }, [loadData])

  useEffect(() => {
    loadData()
    // 30 秒自动刷新：实时工作仪表盘需要保持新鲜。
    // 静默刷新时通过 LastUpdated 给出可见提示，避免数据突然跳变让人困惑。
    // P8-b: 标签页隐藏时跳过本轮，避免后台空转堆积请求（路演切窗口场景）；
    // 切回可见时立即补一次，保证看到的不是过期数据。
        const timer = setInterval(() => {
      if (document.hidden) return
      refreshData()
    }, 30000)

    const handleVisibility = () => {
      if (document.hidden) return
      refreshData()
    }

    document.addEventListener('visibilitychange', handleVisibility)

    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [loadData, refreshData])

  // 聚合指标
  const stats = useMemo(() => {
    const totalTasks = agents.reduce((s, a) => s + a.tasks_total, 0)
    const completedTasks = agents.reduce((s, a) => s + a.tasks_completed, 0)
    const failedTasks = agents.reduce((s, a) => s + a.tasks_failed, 0)
    const activeAgents = agents.filter((a) => a.lifecycle_stage === 'production').length
    const completionRate = totalTasks > 0 ? (completedTasks / totalTasks) * 100 : 0
    const avgSatisfaction =
      agents.length > 0
        ? agents.reduce((s, a) => s + (a.avg_satisfaction || 0), 0) / agents.length
        : 0
    const avgResponseTime =
      agents.length > 0
        ? agents.reduce((s, a) => s + (a.avg_response_time_ms || 0), 0) / agents.length
        : 0
    return {
      totalTasks,
      completedTasks,
      failedTasks,
      activeAgents,
      totalAgents: agents.length,
      completionRate,
      avgSatisfaction,
      avgResponseTime,
    }
  }, [agents])

  // agent_id → agent_name 映射（用于事件流中显示员工名而非 UUID）
  const agentNameMap = useMemo(() => {
    const m = new Map<string, string>()
    agents.forEach((a) => m.set(a.agent_id, a.agent_name))
    return m
  }, [agents])

  // 工具使用聚合
  const toolUsage = useMemo(() => {
    const map = new Map<string, number>()
    agents.forEach((a) => {
      Object.entries(a.tool_usage || {}).forEach(([tool, count]) => {
        map.set(tool, (map.get(tool) || 0) + (count as number))
      })
    })
    return Array.from(map.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
  }, [agents])

  // 可协作的 AI 员工（除"已退休"外均可协作，包括训练中员工——协作即训练的一部分）
  const collaboratableAgents = useMemo(
    () => agents.filter((a) => a.lifecycle_stage !== 'retired'),
    [agents],
  )

  // 员工进度表分页
  const totalPages = Math.max(1, Math.ceil(agents.length / pageSize))
  // 当前页越界时自动回退到最后一页（防御性）
  const safeCurrentPage = Math.min(currentPage, totalPages)
  const pagedAgents = useMemo(
    () => agents.slice((safeCurrentPage - 1) * pageSize, safeCurrentPage * pageSize),
    [agents, safeCurrentPage],
  )

  // 在员工进度表中点击「发起协作」：切到工作台 Tab
  const startCollaboration = useCallback(() => {
    setActiveTab('workbench')
  }, [])

  // 当 agents 变化导致页数减少时，修正 currentPage
  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages)
    }
  }, [currentPage, totalPages])

  // 容器入场动画 variants
  const containerVariants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
  }
  const itemVariants = {
    hidden: { opacity: 0, y: 12 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: 'easeOut' as const } },
  }

  // 概览指标（MetricGrid）— 必须在所有 early return 之前调用（Hooks 规则）
  const overviewMetrics: Metric[] = useMemo(() => [
    {
      key: 'tasks',
      icon: Zap,
      value: stats.totalTasks,
      label: `完成 ${stats.completedTasks} · 失败 ${stats.failedTasks}`,
      tone: 'brand',
    },
    {
      key: 'completion',
      icon: CheckCircle2,
      value: `${stats.completionRate.toFixed(1)}%`,
      label: `${stats.completedTasks}/${stats.totalTasks} 已交付`,
      tone: stats.completionRate >= 80 ? 'success' : stats.completionRate >= 50 ? 'warning' : 'error',
    },
    {
      key: 'agents',
      icon: Users,
      value: stats.activeAgents,
      label: `生产中 / 总 ${stats.totalAgents} 人`,
      tone: 'info',
    },
    {
      key: 'satisfaction',
      icon: TrendingUp,
      value: stats.avgSatisfaction.toFixed(2),
      label: `响应 ${Math.round(stats.avgResponseTime)}ms`,
      tone: stats.failedTasks > 0 ? 'warning' : 'info',
    },
  ], [stats])

  if (loading && agents.length === 0 && events.length === 0) {
    return (
      <Layout>
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      </Layout>
    )
  }

  // 无员工时引导
  if (agents.length === 0 && !loading) {
    return (
      <Layout>
        <div className="max-w-3xl mx-auto w-full px-4 sm:px-6 py-8">
          <PageHeader
            title="智能体协作"
            subtitle="AI 员工实时工作状态、任务交付与协作动态"
          />
          <EmptyState
            icon={Users}
            title="暂无 AI 员工"
            description="协作工作台需要已启用的 AI 员工。请先在企业编译页面生成并启用员工，再来这里发起协作。"
            variant="brand"
            action={{ label: '前往 AI 员工', to: '/workforce' }}
          />
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <motion.div
        variants={containerVariants}
        initial="hidden"
        animate="visible"
                className="w-full space-y-7 pb-4"

      >
        {/* 页头：实时状态、任务范围与刷新动作归于同一上下文，避免长页中丢失当前工作目标。 */}
        <motion.section variants={itemVariants} className="ui-card overflow-hidden rounded-2xl border border-border-default">
          <div className="grid gap-5 p-5 sm:p-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
            <div className="min-w-0">
              <p className="ui-context-kicker">企业协作执行</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-text-primary">看见 AI 团队正在推进什么</h2>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-text-secondary">
                汇总 AI 员工的任务交付、协作动态与工作流状态。数据每 30 秒更新一次，也可以按需立即刷新。
              </p>
              <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-text-tertiary">
                <span className="inline-flex items-center gap-1.5"><span className="dot dot-success" />实时协作信号</span>
                <span className="inline-flex items-center gap-1.5"><span className="dot dot-info" />任务与审批可追踪</span>
              </div>
            </div>
            <LastUpdated
              timestamp={lastUpdated}
              refreshing={loading || refreshing}
              mode="poll"
              onRefresh={refreshData}
            />
          </div>
        </motion.section>

        {error && (
          <ApiErrorState message={error} onRetry={loadData} retrying={loading} />
        )}

        {/* 实时工作状态面板 — 总览指标卡片（MetricGrid） */}
        <motion.div variants={itemVariants}>
          <MetricGrid metrics={overviewMetrics} columns={4} />
        </motion.div>

        {/* Tab 切换（InlineTabs） */}
        <motion.div variants={itemVariants}>
          <InlineTabs
            variant="underline"
            tabs={[
              { key: 'workbench', label: '协作工作台', icon: MessagesSquare },
              { key: 'progress', label: '员工进度', icon: Activity },
            ]}
            activeKey={activeTab}
            onChange={(k) => setActiveTab(k as 'workbench' | 'progress')}
          />
        </motion.div>

        {/* ============================================================ */}
        {/* Tab 1：协作工作台（CollaborationWorkspace — 三栏布局）        */}
        {/* ============================================================ */}
        {activeTab === 'workbench' && (
          <motion.div variants={itemVariants} className="space-y-6">
            {/* 任务如何执行（编排能力摘要，P0-2）：失败时显示空态提示，不阻塞协作工作台 */}
            {orchestrationFailed && (
              <Card>
                <CardBody>
                  <p className="text-sm text-text-tertiary">暂无编排能力信息，无法展示任务执行方式</p>
                </CardBody>
              </Card>
            )}
            {orchestration && (
              <Card>
                <CardHeader>
                  <h2 className="text-h3 text-text-primary flex items-center gap-2">
                    <Zap className="w-5 h-5 text-brand-500" aria-hidden="true" />
                    任务如何执行
                  </h2>
                </CardHeader>
                <CardBody className="space-y-4">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm text-text-secondary">默认执行方式</span>
                    <span className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md bg-brand-50 text-brand-500">
                      {orchestration.default_method_label}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {orchestration.methods.map((m) => (
                      <span
                        key={m.key}
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md border ${
                          m.status === 'active'
                            ? 'bg-elevated border-border-default text-text-primary'
                            : 'bg-elevated/50 border-border-subtle text-text-tertiary'
                        }`}
                      >
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            m.status === 'active' ? 'bg-success' : 'bg-border-default'
                          }`}
                          aria-hidden="true"
                        />
                        {m.label}
                        {m.status === 'roadmap' && (
                          <span className="text-text-tertiary">（规划中）</span>
                        )}
                      </span>
                    ))}
                  </div>
                </CardBody>
              </Card>
            )}
            <LocalConnectionGuide />
            <CollaborationWorkspace agents={collaboratableAgents} />
          </motion.div>
        )}

        {/* ============================================================ */}
        {/* Tab 2：员工进度（增强版：进度表 + 实时工作流 + 工具统计）    */}
        {/* ============================================================ */}
        {activeTab === 'progress' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* 左侧：员工工作进度表 + 工具统计（占 2 列） */}
            <div className="lg:col-span-2 space-y-6">
              <Card>
                <CardHeader>
                  <h2 className="text-h3 text-text-primary flex items-center gap-2">
                    <Users className="w-5 h-5 text-brand-500" aria-hidden="true" />
                    员工工作进度
                  </h2>
                </CardHeader>
                <CardBody className="p-0">
                  {agents.length === 0 ? (
                    <div className="text-center py-12 text-text-tertiary text-sm">
                      暂无员工工作数据，请先在企业编译页面生成 AI 员工
                    </div>
                  ) : (
                    <>
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                                                    <thead className="bg-[var(--surface-sunken)] text-text-tertiary text-xs">

                            <tr>
                              <th className="text-left px-4 py-3 font-medium">员工</th>
                              <th className="text-left px-4 py-3 font-medium">状态</th>
                              <th className="text-left px-4 py-3 font-medium">任务进度</th>
                              <th className="text-right px-4 py-3 font-medium">响应</th>
                              <th className="text-right px-4 py-3 font-medium">满意度</th>
                              <th className="text-right px-4 py-3 font-medium">操作</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-border-subtle">
                            {pagedAgents.map((agent) => {
                              const completion =
                                agent.tasks_total > 0
                                  ? (agent.tasks_completed / agent.tasks_total) * 100
                                  : 0
                              const stage = lifecycleLabel[agent.lifecycle_stage] || agent.lifecycle_stage
                              const isProduction = agent.lifecycle_stage === 'production'
                              return (
                                                                <tr key={agent.agent_id} className="transition-colors hover:bg-[var(--surface-tint)]">

                                  <td className="px-4 py-3">
                                    <div className="flex items-center gap-2">
                                      <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-semibold flex-shrink-0 ${isProduction ? 'bg-gradient-to-br from-brand-400 to-brand-600' : 'bg-border-default'}`}>
                                        {agent.agent_name?.charAt(0) || '?'}
                                      </div>
                                      <div className="min-w-0">
                                        <p className="font-medium text-text-primary truncate">{agent.agent_name}</p>
                                        <p className="text-xs text-text-tertiary truncate">{positionToLabel(agent.position, agent.agent_name)}</p>
                                      </div>
                                    </div>
                                  </td>
                                  <td className="px-4 py-3">
                                    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded ${isProduction ? 'bg-success/10 text-success' : 'bg-elevated text-text-tertiary'}`}>
                                      {isProduction && <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />}
                                      {stage}
                                    </span>
                                  </td>
                                  <td className="px-4 py-3">
                                    <div className="min-w-[120px]">
                                      <div className="flex items-center justify-between text-xs mb-1">
                                        <span className="text-text-secondary">
                                          {agent.tasks_completed}/{agent.tasks_total}
                                        </span>
                                        {agent.tasks_failed > 0 && (
                                          <span className="text-error">{agent.tasks_failed} 失败</span>
                                        )}
                                      </div>
                                      <div className="h-1.5 bg-elevated rounded-full overflow-hidden">
                                        <div
                                          className="h-full bg-success rounded-full transition-all"
                                          style={{ width: `${completion}%` }}
                                        />
                                      </div>
                                    </div>
                                  </td>
                                  <td className="px-4 py-3 text-right text-text-secondary">
                                    {agent.avg_response_time_ms > 0 ? `${Math.round(agent.avg_response_time_ms)}ms` : '—'}
                                  </td>
                                  <td className="px-4 py-3 text-right">
                                    {/* 0-5 分制，统一走 format 契约 */}
                                    <span
                                      className={
                                        agent.avg_satisfaction > 0
                                          ? 'font-medium text-text-primary'
                                          : 'text-text-tertiary'
                                      }
                                    >
                                      {formatSatisfaction(agent.avg_satisfaction)}
                                    </span>
                                  </td>
                                  <td className="px-4 py-3 text-right">
                                    {isProduction ? (
                                      <button
                                        onClick={() => startCollaboration()}
                                        className="inline-flex items-center gap-1 text-xs text-brand-500 hover:text-brand-600 font-medium transition-colors"
                                        title="发起协作"
                                        aria-label={`与 ${agent.agent_name} 发起协作`}
                                      >
                                        <MessagesSquare className="w-3.5 h-3.5" aria-hidden="true" />
                                        发起协作
                                      </button>
                                    ) : (
                                      <span className="text-xs text-text-muted">—</span>
                                    )}
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>

                      {/* 分页控件 */}
                      {totalPages > 1 && (
                        <div className="flex items-center justify-between px-4 py-3 border-t border-border-subtle">
                          <span className="text-xs text-text-tertiary">
                            共 {agents.length} 位员工 · 第 {safeCurrentPage} / {totalPages} 页
                          </span>
                          <div className="flex items-center gap-1">
                            <button
                              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                              disabled={safeCurrentPage <= 1}
                              className="p-1.5 rounded-md border border-border-default text-text-secondary hover:bg-elevated transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                              aria-label="上一页"
                            >
                              <ChevronLeft className="w-4 h-4" aria-hidden="true" />
                            </button>
                            <span className="text-xs text-text-secondary px-2 font-mono">
                              {safeCurrentPage} / {totalPages}
                            </span>
                            <button
                              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                              disabled={safeCurrentPage >= totalPages}
                              className="p-1.5 rounded-md border border-border-default text-text-secondary hover:bg-elevated transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                              aria-label="下一页"
                            >
                              <ChevronRight className="w-4 h-4" aria-hidden="true" />
                            </button>
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </CardBody>
              </Card>

              {/* 工具使用统计 */}
              {toolUsage.length > 0 && (
                <Card>
                  <CardHeader>
                    <h2 className="text-h3 text-text-primary flex items-center gap-2">
                      <Zap className="w-5 h-5 text-warning" aria-hidden="true" />
                      工具调用统计
                    </h2>
                  </CardHeader>
                  <CardBody>
                    <div className="space-y-2">
                      {toolUsage.map(([tool, count]) => {
                        const maxCount = toolUsage[0][1]
                        const pct = maxCount > 0 ? (count / maxCount) * 100 : 0
                        return (
                          <div key={tool} className="flex items-center gap-3">
                            <span className="text-sm text-text-secondary w-32 truncate flex-shrink-0">{tool}</span>
                            <div className="flex-1 h-2 bg-elevated rounded-full overflow-hidden">
                              <div className="h-full bg-brand-500 rounded-full" style={{ width: `${pct}%` }} />
                            </div>
                            <span className="text-sm text-text-tertiary w-12 text-right flex-shrink-0">{count}</span>
                          </div>
                        )
                      })}
                    </div>
                  </CardBody>
                </Card>
              )}
            </div>

            {/* 右侧：实时工作流（占 1 列） */}
            <Card className="flex flex-col h-full">
              <CardHeader className="flex items-center justify-between gap-2 flex-shrink-0">
                <h2 className="text-h3 text-text-primary flex items-center gap-2">
                  <Activity className="w-5 h-5 text-success" aria-hidden="true" />
                  实时工作流
                </h2>
                {/* 视图切换：时间流 / 聚合统计 */}
                                <div className="ui-tab-rail w-fit gap-0.5 border border-border-subtle p-0.5">

                  <button
                    type="button"
                                        onClick={() => setWorkflowView('stream')}
                    aria-pressed={workflowView === 'stream'}
                    className={`ui-tab-item px-2.5 text-xs ${
                      workflowView === 'stream' ? 'ui-tab-item--active' : 'text-text-tertiary hover:text-text-secondary'
                    }`}

                  >
                    时间流
                  </button>
                  <button
                    type="button"
                                        onClick={() => setWorkflowView('aggregate')}
                    aria-pressed={workflowView === 'aggregate'}
                    className={`ui-tab-item px-2.5 text-xs ${
                      workflowView === 'aggregate' ? 'ui-tab-item--active' : 'text-text-tertiary hover:text-text-secondary'
                    }`}

                  >
                    聚合
                  </button>
                </div>
              </CardHeader>
              <CardBody className="p-0 flex-1 flex flex-col min-h-0">
                {events.length === 0 ? (
                  <div className="text-center py-12 text-text-tertiary text-sm">
                    暂无协作动态
                  </div>
                ) : workflowView === 'aggregate' ? (
                  <AggregateWorkflow events={events} agentNameMap={agentNameMap} />
                ) : (
                  <div className="flex-1 overflow-y-auto">
                    {events.map((event, idx) => {
                      const cfg = eventLabelMap[event.event_type] || eventLabelMap.handoff
                      return (
                        <div
                          key={event.event_id}
                          className={`flex items-start gap-3 px-4 py-3 ${idx !== events.length - 1 ? 'border-b border-border-subtle' : ''}`}
                        >
                          <div className={`w-8 h-8 rounded-full ${cfg.bg} flex items-center justify-center flex-shrink-0`}>
                            {event.event_type === 'error' || event.event_type === 'escalation' ? (
                              <AlertCircle className={`w-4 h-4 ${cfg.color}`} aria-hidden="true" />
                            ) : (
                              <CheckCircle2 className={`w-4 h-4 ${cfg.color}`} aria-hidden="true" />
                            )}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${cfg.bg} ${cfg.color}`}>
                                {cfg.label}
                              </span>
                              {event.source_agent_id && (
                                <span className="text-xs text-text-tertiary truncate">
                                  {agentNameMap.get(event.source_agent_id) || '系统'}
                                </span>
                              )}
                            </div>
                            <p className="text-sm text-text-secondary mt-1 line-clamp-2">
                              {String(event.payload.summary || event.payload.description || event.payload.customer || JSON.stringify(event.payload).slice(0, 80))}
                            </p>
                            <p className="text-xs text-text-tertiary mt-1 flex items-center gap-1">
                              <Clock className="w-3 h-3" aria-hidden="true" />
                              {formatRelativeTime(event.created_at)}
                            </p>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </CardBody>
            </Card>
          </div>
        )}
      </motion.div>
    </Layout>
  )
}
