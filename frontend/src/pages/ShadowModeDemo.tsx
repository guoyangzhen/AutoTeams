import { useState, useEffect, useCallback, useMemo } from 'react'
import Layout from '@/components/Layout'
import { Card, CardBody } from '@/components/ui/Card'
import { PageHeader } from '@/components/ui/PageHeader'
import { Spinner } from '@/components/ui/Spinner'
import { ApiErrorState } from '@/components/ApiErrorState'
import { useConfirmDialog } from '@/hooks/useConfirmDialog'
import {
  Eye, User, Bot, Shield, TrendingUp, CheckCircle2, AlertTriangle,
  Sparkles, Users, Activity, ScrollText, Play,
} from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { toast } from 'sonner'
import * as workforceApi from '@/api/workforce'
import * as collaborationApi from '@/api/collaboration'
import * as shadowApi from '@/api/shadow'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { positionToLabel } from '@/utils/fieldMappings'
import {
  HumanAIDiff,
  StateMachineTrack,
  MetricReadout,
  EventStream,
  type TrackNode,
} from '@/components/instrument'
import { formatRatio, formatSatisfaction } from '@/utils/format'
import type { AgentRunMetrics, CollaborationEvent, LifecycleStage, ApprovalRequest, ShadowTask, ShadowStatus } from '@/types'

/**
 * 影子模式 · 数字员工信任建立过程
 *
 * 三阶段渐进式信任建立，让 AI 数字员工从「观察」到「自主」安全接管业务。
 * 数据来源：
 * - workforce API（listWorkforce 返回 AgentRunMetrics，用于阶段分组）
 * - collaboration API（事件流 + 审批门，用于真实运转指标与审批操作）
 *
 * 生命周期阶段映射：
 * - recruit/training → 影子模式（AI 观察学习，不对外输出）
 * - evaluation/continuous_learning → 评估模式（AI 输出但需审批）
 * - production/promotion → 自主模式（AI 自主处理）
 */

type TrustStage = 'shadow' | 'evaluation' | 'autonomous'

interface StageConfig {
  key: TrustStage
  label: string
  color: string
  bgColor: string
  borderColor: string
  icon: typeof Eye
  description: string
  aiBehavior: string
  humanBehavior: string
  /** 映射的后端 lifecycle_stage 值 */
  lifecycleStages: LifecycleStage[]
  /** 该阶段对应的影子任务状态（用于统计真实信任度） */
  shadowStatuses: ShadowStatus[]
}

const STAGES: StageConfig[] = [
  {
    key: 'shadow',
    label: '影子模式',
    color: 'text-info',
    bgColor: 'bg-info/10',
    borderColor: 'border-info/30',
    icon: Eye,
    description: 'AI 静默观察真人处理，不对外输出。系统收集 (问题, 真人回答) 对，建立基线数据集。',
    aiBehavior: '不输出，仅记录"如果是我会怎么回答"，与真人答案做离线对比',
    humanBehavior: '正常处理，无感知。处理时长与质量作为基线',
    lifecycleStages: ['recruit', 'training'],
    shadowStatuses: ['shadowing'],
  },
  {
    key: 'evaluation',
    label: '评估模式',
    color: 'text-warning',
    bgColor: 'bg-warning/10',
    borderColor: 'border-warning/30',
    icon: Shield,
    description: 'AI 输出建议，真人逐条审批。系统持续对比 AI 与真人结论，达到阈值后晋升。',
    aiBehavior: '生成建议答案，标注置信度，提交审批队列',
    humanBehavior: '审批 AI 建议：通过 / 修改 / 拒绝。修改记录用于微调',
    lifecycleStages: ['evaluation', 'continuous_learning'],
    shadowStatuses: ['evaluating', 'qualified'],
  },
  {
    key: 'autonomous',
    label: '自主模式',
    color: 'text-success',
    bgColor: 'bg-success/10',
    borderColor: 'border-success/30',
    icon: Sparkles,
    description: 'AI 自主处理所有问题。系统按 5% 抽样审计，异常自动降级回评估模式。',
    aiBehavior: '直接输出，无需审批。低置信度问题自动转人工',
    humanBehavior: '仅处理 AI 转交的疑难问题。每周抽查 5% 案例',
    lifecycleStages: ['production', 'promotion'],
    shadowStatuses: ['autonomous'],
  },
]

/** 影子任务状态 → 中文标签 + 颜色 */
const SHADOW_STATUS: Record<ShadowStatus, { label: string; color: string; bg: string }> = {
  shadowing: { label: '影子观察', color: 'text-info', bg: 'bg-info/10 border-info/30' },
  evaluating: { label: '评估中', color: 'text-warning', bg: 'bg-warning/10 border-warning/30' },
  qualified: { label: '已达标', color: 'text-brand-500', bg: 'bg-brand-500/10 border-brand-500/30' },
  autonomous: { label: '自主运行', color: 'text-success', bg: 'bg-success/10 border-success/30' },
}

export default function ShadowModeDemo() {
  const enterpriseId = useEnterpriseId()
  const confirmDialog = useConfirmDialog()
  const [agents, setAgents] = useState<AgentRunMetrics[]>([])
  const [events, setEvents] = useState<CollaborationEvent[]>([])
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeStage, setActiveStage] = useState<TrustStage>('evaluation')
  const [runningDemo, setRunningDemo] = useState(false)
  const [actingApprovalId, setActingApprovalId] = useState<string | null>(null)

  // ---- 补1：影子任务（真实后端状态机数据）----
  const [shadowTasks, setShadowTasks] = useState<ShadowTask[]>([])
  const [actingShadowId, setActingShadowId] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    if (!enterpriseId) return
    setLoading(true)
    setError(null)
    try {
      const [workforceResp, eventsResp, approvalsData, shadowResp] = await Promise.all([
        workforceApi.listWorkforce(enterpriseId),
        collaborationApi.listEvents(enterpriseId, { limit: 20 }).catch(() => ({ items: [] as CollaborationEvent[], total: 0 })),
        collaborationApi.getPendingApprovals(enterpriseId).catch(() => [] as ApprovalRequest[]),
        shadowApi.listShadowTasks(enterpriseId, { limit: 50 }).catch(() => ({ items: [] as ShadowTask[], total: 0 })),
      ])
      setAgents(workforceResp.items)
      setEvents(eventsResp.items)
      setApprovals(approvalsData)
      setShadowTasks(shadowResp.items)
      // 自动选中员工数最多的阶段
      const counts = countByStage(workforceResp.items)
      const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]
      if (top && top[1] > 0) setActiveStage(top[0] as TrustStage)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载员工数据失败')
    } finally {
      setLoading(false)
    }
  }, [enterpriseId])

  useEffect(() => {
    loadData()
  }, [loadData])

  // 一键演示：触发 7 步业务链路，生成真实事件流
  const handleRunDemo = useCallback(async () => {
    if (!enterpriseId || agents.length < 5) {
      toast.warning('需要至少 5 名 AI 员工才能跑通完整演示链路')
      return
    }
    // 取前 5 个员工作为 5 个角色（演示用途）
    const [sales, product, finance, cs, afterSales] = agents
    const ok = await confirmDialog.ask({
      title: '运行演示案例',
      description: '将生成询盘→报价→审批→成交→售后的完整协作事件流，并在评估模式创建真实审批门。是否继续？',
      confirmText: '运行',
      cancelText: '取消',
    })
    if (!ok) return
    setRunningDemo(true)
    try {
      const result = await collaborationApi.triggerDemoCase(enterpriseId, {
        sales_agent_id: sales.agent_id,
        product_expert_agent_id: product.agent_id,
        finance_agent_id: finance.agent_id,
        customer_service_agent_id: cs.agent_id,
        after_sales_agent_id: afterSales.agent_id,
      }, 'manual')
      toast.success(`演示完成：生成 ${result.steps} 步事件${result.approval_gate_id ? '，已创建审批门' : ''}`)
      await loadData()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '演示运行失败')
    } finally {
      setRunningDemo(false)
    }
  }, [enterpriseId, agents, confirmDialog, loadData])

  // 审批通过
  const handleApprove = useCallback(async (approval: ApprovalRequest) => {
    setActingApprovalId(approval.id)
    try {
      await collaborationApi.approveRequest(approval.id, '通过')
      toast.success('审批已通过，流程继续')
      setApprovals((prev) => prev.filter((a) => a.id !== approval.id))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '审批操作失败')
    } finally {
      setActingApprovalId(null)
    }
  }, [])

  // 审批拒绝
  const handleReject = useCallback(async (approval: ApprovalRequest) => {
    const ok = await confirmDialog.ask({
      title: '拒绝审批',
      description: '确定拒绝此审批吗？流程将终止。',
      confirmText: '拒绝',
      cancelText: '取消',
      variant: 'danger',
    })
    if (!ok) return
    setActingApprovalId(approval.id)
    try {
      await collaborationApi.rejectRequest(approval.id, { reason: '人工拒绝' })
      toast.success('已拒绝审批')
      setApprovals((prev) => prev.filter((a) => a.id !== approval.id))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '审批操作失败')
    } finally {
      setActingApprovalId(null)
    }
  }, [confirmDialog])

  // ---- 补1：影子任务状态机操作 ----
  const refreshShadow = useCallback(async () => {
    if (!enterpriseId) return
    try {
      const resp = await shadowApi.listShadowTasks(enterpriseId, { limit: 50 })
      setShadowTasks(resp.items)
    } catch { /* 静默失败 */ }
  }, [enterpriseId])

  const withShadowAction = useCallback(async (taskId: string, action: () => Promise<unknown>) => {
    setActingShadowId(taskId)
    try {
      await action()
      await refreshShadow()
      toast.success('影子任务状态已更新')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
    } finally {
      setActingShadowId(null)
    }
  }, [refreshShadow])

  // 记录 AI 回答（shadowing → evaluating）
  //
  // 修复：此前这里用 `AI 对「${question}」的模拟回答` 直接编造答案写入数据库，
  // 污染了影子模式的核心资产（人机对照样本）。现改为调用后端让真实
  // Agent 作答；后端未提供该能力时明确提示，不再伪造。
  const handleRecordAi = useCallback(async (task: ShadowTask) => {
    setActingShadowId(task.id)
    try {
      await shadowApi.generateAiAnswer(task.id)
      await refreshShadow()
      toast.success('AI 已完成作答，可进行人机对照评估')
    } catch (err) {
      const msg = err instanceof Error ? err.message : '生成 AI 回答失败'
      toast.error(msg)
    } finally {
      setActingShadowId(null)
    }
  }, [refreshShadow])

  // 评估：人机一致 / 存在分歧（真实反馈，不再只有「通过」单向路径）
  const handleEvaluate = useCallback((task: ShadowTask, match: boolean) => {
    withShadowAction(task.id, () =>
      shadowApi.evaluateShadowTask(task.id, { match, auto_qualify: match }),
    )
  }, [withShadowAction])

  // 晋升（qualified → autonomous）
  const handlePromote = useCallback((task: ShadowTask) => {
    withShadowAction(task.id, () => shadowApi.promoteShadowTask(task.id))
  }, [withShadowAction])

  // 降级（异常）
  const handleDemote = useCallback((task: ShadowTask) => {
    withShadowAction(task.id, () => shadowApi.demoteShadowTask(task.id, 'evaluating'))
  }, [withShadowAction])

  // 按信任阶段分组真实员工
  // 合并归类：同一岗位（position）只保留 1 名代表，总数限制为 10 名核心员工
  const stageAgents = useMemo(() => {
    const map: Record<TrustStage, AgentRunMetrics[]> = {
      shadow: [],
      evaluation: [],
      autonomous: [],
    }
    // 1. 按岗位去重（每个 position 只保留第一名代表，避免同岗位多名员工刷屏）
    const seenPositions = new Set<string>()
    const dedupedAgents: AgentRunMetrics[] = []
    agents.forEach((agent) => {
      const posKey = agent.position || agent.agent_id
      if (seenPositions.has(posKey)) return
      seenPositions.add(posKey)
      dedupedAgents.push(agent)
    })
    // 2. 限制总数为 10 名核心员工
    const cappedAgents = dedupedAgents.slice(0, 10)
    // 3. 按阶段分组
    cappedAgents.forEach((agent) => {
      const stage = STAGES.find((s) => s.lifecycleStages.includes(agent.lifecycle_stage))
      if (stage) map[stage.key].push(agent)
    })
    return map
  }, [agents])

  const stageCounts = useMemo(
    () => ({
      shadow: stageAgents.shadow.length,
      evaluation: stageAgents.evaluation.length,
      autonomous: stageAgents.autonomous.length,
    }),
    [stageAgents],
  )

  // 真实运转指标：用协作事件数 + 审批数替代 workforce 全 0 指标
  const stageMetrics = useMemo(() => {
    // 按事件类型归类到阶段
    const shadowEvents = events.filter((e) => ['handoff', 'error'].includes(e.event_type))
    const evalEvents = events.filter((e) => ['approval_submitted', 'escalation'].includes(e.event_type))
    const autoEvents = events.filter((e) => ['order_synced', 'deal_closed', 'after_sales', 'approval_approved'].includes(e.event_type))
    const calc = (list: AgentRunMetrics[], evtCount: number) => {
      const totalTasks = list.reduce((s, a) => s + a.tasks_total, 0)
      const completedTasks = list.reduce((s, a) => s + a.tasks_completed, 0)
      // 满意度为 0-5 分制，仅对有评分样本的员工求均值，避免 0 值拉低
      const rated = list.filter((a) => (a.avg_satisfaction || 0) > 0)
      const avgSatisfaction =
        rated.length > 0
          ? rated.reduce((s, a) => s + a.avg_satisfaction, 0) / rated.length
          : null
      return {
        // 语义修正：这是「满意度评分」而非「准确率」，此前混用两个概念
        satisfaction: avgSatisfaction,
        samplesCollected: totalTasks,
        eventCount: evtCount,
        completionRate: totalTasks > 0 ? (completedTasks / totalTasks) * 100 : 0,
      }
    }
    return {
      shadow: calc(stageAgents.shadow, shadowEvents.length),
      evaluation: calc(stageAgents.evaluation, evalEvents.length),
      autonomous: calc(stageAgents.autonomous, autoEvents.length),
    }
  }, [stageAgents, events])

  /**
   * 真实信任度（UI v4 §一 罪一）。
   *
   * 此前三个阶段的信任度写死为 30/60/100，是纯粹的装饰数字 ——
   * 无论 AI 表现如何都不会变化，等于向用户承诺了一个不存在的度量。
   * 现改为真实计算：信任度 = 人机判断一致的样本数 / 已评估样本数。
   * 无评估样本时返回 null，UI 显示「样本不足」而非编造数字。
   */
  const trustMetrics = useMemo(() => {
    const evaluated = shadowTasks.filter(
      (t) => t.eval_result === 'match' || t.eval_result === 'mismatch',
    )
    const matched = evaluated.filter((t) => t.eval_result === 'match')
    const overall = evaluated.length > 0 ? matched.length / evaluated.length : null

    // 分阶段统计：各阶段的样本量与一致率
    const byStage = {} as Record<TrustStage, { total: number; evaluated: number; match: number; rate: number | null }>
    STAGES.forEach((stage) => {
      const inStage = shadowTasks.filter((t) => stage.shadowStatuses.includes(t.status))
      const ev = inStage.filter((t) => t.eval_result === 'match' || t.eval_result === 'mismatch')
      const mt = ev.filter((t) => t.eval_result === 'match')
      byStage[stage.key] = {
        total: inStage.length,
        evaluated: ev.length,
        match: mt.length,
        rate: ev.length > 0 ? mt.length / ev.length : null,
      }
    })

    return {
      overall,
      evaluated: evaluated.length,
      matched: matched.length,
      total: shadowTasks.length,
      byStage,
    }
  }, [shadowTasks])

  /** 状态机轨道节点：把四态状态机可视化（此前只是静态标签） */
  const trackNodes = useMemo<TrackNode[]>(() => {
    const order: { key: ShadowStatus; label: string; caption: string }[] = [
      { key: 'shadowing', label: '影子观察', caption: 'AI 静默学习，收集真人基线' },
      { key: 'evaluating', label: '评估对照', caption: 'AI 作答并与真人比对' },
      { key: 'qualified', label: '达标待授权', caption: '一致率达标，等待人工授权' },
      { key: 'autonomous', label: '自主运行', caption: 'AI 独立处理，抽样审计' },
    ]
    const counts: Record<string, number> = {}
    shadowTasks.forEach((t) => {
      counts[t.status] = (counts[t.status] || 0) + 1
    })
    // 当前活跃态 = 样本量最多且非终态的阶段
    const activeIdx = order.findIndex((o) => (counts[o.key] || 0) > 0)
    return order.map((o, i) => ({
      key: o.key,
      label: o.label,
      caption: o.caption,
      count: counts[o.key] || 0,
      status:
        (counts[o.key] || 0) === 0
          ? 'pending'
          : i === activeIdx
            ? 'active'
            : 'done',
    }))
  }, [shadowTasks])

  /** agent_id → 显示名映射，供事件流展示协作链路 */
  const agentNameMap = useMemo(() => {
    const map: Record<string, string> = {}
    agents.forEach((a) => {
      map[a.agent_id] = a.agent_name
    })
    return map
  }, [agents])

  const activeConfig = STAGES.find((s) => s.key === activeStage)!
  const ActiveIcon = activeConfig.icon
  const activeMetrics = stageMetrics[activeStage]
  const activeAgents = stageAgents[activeStage]

  if (loading && agents.length === 0) {
    return (
      <Layout>
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-8 space-y-8">
        {/* 标题 + 演示按钮 */}
        <PageHeader
          title="影子模式 · 数字员工信任建立过程"
          subtitle="通过三阶段渐进式信任建立，让 AI 数字员工从「观察」到「自主」安全接管业务。"
          actions={
            <button
              onClick={handleRunDemo}
              disabled={runningDemo}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-soft"
            >
              <Play className={`w-3.5 h-3.5 ${runningDemo ? 'animate-pulse' : ''}`} />
              {runningDemo ? '运行中…' : '一键演示'}
            </button>
          }
        />

        {/* 确认对话框（一键演示 / 拒绝审批共用）：必须渲染，否则 ask() 永不 resolve */}
        {confirmDialog.dialog}

        {error && (
          <ApiErrorState message={error} onRetry={loadData} retrying={loading} />
        )}

        {/* 信任度进度条 */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <Card className="shadow-soft">
            <CardBody>
              <div className="flex items-start justify-between mb-5 gap-4">
                <div>
                  <h2 className="text-sm font-semibold text-text-primary">信任建立进程</h2>
                  <p className="text-xs text-text-tertiary mt-0.5">
                    信任度 = 人机判断一致的样本数 / 已评估样本数，随真实评估结果变化
                  </p>
                </div>
                <div className="text-right flex-shrink-0">
                  {/* 真实信任度：无样本时显示「样本不足」，绝不编造数字 */}
                  <MetricReadout
                    label="综合信任度"
                    value={
                      trustMetrics.overall != null
                        ? formatRatio(trustMetrics.overall)
                        : '样本不足'
                    }
                    tone={
                      trustMetrics.overall == null
                        ? 'idle'
                        : trustMetrics.overall >= 0.85
                          ? 'alive'
                          : trustMetrics.overall >= 0.7
                            ? 'alert'
                            : 'fault'
                    }
                    size="lg"
                    hint={
                      trustMetrics.evaluated > 0
                        ? `${trustMetrics.matched}/${trustMetrics.evaluated} 条判断一致`
                        : `共 ${trustMetrics.total} 条样本，尚无评估结果`
                    }
                  />
                </div>
              </div>

              {/* 状态机轨道：把 shadowing→evaluating→qualified→autonomous
                  四态跃迁可视化（此前仅为三个静态标签，看不出这是状态机） */}
              <StateMachineTrack nodes={trackNodes} className="mt-2 mb-5" />

              {/* 阶段切换 + 真实员工数与一致率 */}
              <div className="grid grid-cols-3 gap-2">
                {STAGES.map((stage, idx) => {
                  const Icon = stage.icon
                  const isActive = stage.key === activeStage
                  const count = stageCounts[stage.key]
                  const st = trustMetrics.byStage[stage.key]
                  return (
                    <motion.button
                      key={stage.key}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 0.1 + idx * 0.05 }}
                      onClick={() => setActiveStage(stage.key)}
                      className={`p-3 rounded-md border text-left transition-all ${
                        isActive
                          ? `${stage.bgColor} ${stage.borderColor} shadow-soft`
                          : 'bg-elevated border-border-default hover:bg-border-subtle'
                      }`}
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <Icon className={`w-4 h-4 ${isActive ? stage.color : 'text-text-tertiary'}`} aria-hidden="true" />
                        <span className={`text-sm font-medium ${isActive ? stage.color : 'text-text-secondary'}`}>
                          {stage.label}
                        </span>
                        {count > 0 && (
                          <span className="ml-auto text-xs px-1.5 py-0.5 rounded-full bg-surface text-text-tertiary">
                            {count} 人
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-text-tertiary">
                        {/* 各阶段一致率同样来自真实评估，无样本则明说 */}
                        {st?.rate != null
                          ? `一致率 ${formatRatio(st.rate)}（${st.evaluated} 样本）`
                          : st?.total
                            ? `${st.total} 条样本待评估`
                            : '暂无样本'}
                      </div>
                    </motion.button>
                  )
                })}
              </div>
            </CardBody>
          </Card>
        </motion.div>

        {/* 当前阶段详情 */}
        <motion.div
          key={activeStage}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <Card className={`border ${activeConfig.borderColor} shadow-soft`}>
            <CardBody>
              <div className="flex items-center gap-3 mb-4">
                <div className={`w-11 h-11 rounded-lg ${activeConfig.bgColor} flex items-center justify-center`}>
                  <ActiveIcon className={`w-5 h-5 ${activeConfig.color}`} aria-hidden="true" />
                </div>
                <div>
                  <h2 className="font-serif-display text-lg font-semibold text-text-primary">{activeConfig.label}</h2>
                  <p className="text-xs text-text-tertiary mt-0.5">
                    {/* 该阶段一致率来自真实评估，替代此前写死的信任度 */}
                    {trustMetrics.byStage[activeStage]?.rate != null
                      ? `一致率 ${formatRatio(trustMetrics.byStage[activeStage].rate!)}`
                      : '尚无评估样本'}
                    {' · '}{activeAgents.length} 名员工 · {activeMetrics.eventCount} 个协作事件
                  </p>
                </div>
              </div>
              <p className="text-sm text-text-secondary mb-4 leading-relaxed">{activeConfig.description}</p>

              {/* 行为对比 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <div className="bg-elevated rounded-md p-4 border border-border-default">
                  <div className="flex items-center gap-2 mb-2">
                    <User className="w-4 h-4 text-text-secondary" aria-hidden="true" />
                    <span className="text-sm font-medium text-text-primary">真人处理</span>
                  </div>
                  <p className="text-xs text-text-secondary leading-relaxed">{activeConfig.humanBehavior}</p>
                </div>
                <div className="bg-elevated rounded-md p-4 border border-border-default">
                  <div className="flex items-center gap-2 mb-2">
                    <Bot className="w-4 h-4 text-brand-500" aria-hidden="true" />
                    <span className="text-sm font-medium text-text-primary">AI 数字员工</span>
                  </div>
                  <p className="text-xs text-text-secondary leading-relaxed">{activeConfig.aiBehavior}</p>
                </div>
              </div>

              {/* 关键指标（真实数据） */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-elevated rounded-md p-3">
                  <div className="flex items-center gap-1 text-xs text-text-tertiary mb-1">
                    <TrendingUp className="w-3 h-3" aria-hidden="true" />
                    满意度
                  </div>
                  <div className="text-lg font-semibold text-text-primary">
                    {/* 0-5 分制，无评分样本时显示占位符 */}
                    {formatSatisfaction(activeMetrics.satisfaction)}
                  </div>
                </div>
                <div className="bg-elevated rounded-md p-3">
                  <div className="flex items-center gap-1 text-xs text-text-tertiary mb-1">
                    <Activity className="w-3 h-3" aria-hidden="true" />
                    协作事件
                  </div>
                  <div className="text-lg font-semibold text-text-primary">{activeMetrics.eventCount}</div>
                </div>
                <div className="bg-elevated rounded-md p-3">
                  <div className="flex items-center gap-1 text-xs text-text-tertiary mb-1">
                    <ScrollText className="w-3 h-3" aria-hidden="true" />
                    累计任务
                  </div>
                  <div className="text-lg font-semibold text-text-primary">{activeMetrics.samplesCollected.toLocaleString()}</div>
                </div>
                <div className="bg-elevated rounded-md p-3">
                  <div className="text-xs text-text-tertiary mb-1">完成率</div>
                  <div className="text-lg font-semibold text-success">
                    {activeMetrics.completionRate > 0 ? `${activeMetrics.completionRate.toFixed(0)}%` : '—'}
                  </div>
                </div>
              </div>
            </CardBody>
          </Card>
        </motion.div>

        {/* 评估模式审批队列（真实审批门） */}
        <AnimatePresence>
          {activeStage === 'evaluation' && approvals.length > 0 && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
            >
              <Card className="border-warning/30 shadow-soft">
                <CardBody>
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
                      <Shield className="w-4 h-4 text-warning" aria-hidden="true" />
                      评估模式审批队列
                      <span className="px-1.5 py-0.5 rounded-full bg-warning/20 text-warning text-xs font-medium">
                        {approvals.length} 待办
                      </span>
                    </h2>
                  </div>
                  <div className="space-y-2">
                    {approvals.map((approval) => (
                      <div key={approval.id} className="flex items-center gap-3 p-3 bg-warning/10 rounded-md border border-warning/30">
                        <div className="w-9 h-9 rounded-full bg-warning/20 flex items-center justify-center text-warning flex-shrink-0">
                          <Shield className="w-4 h-4" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium text-text-primary truncate">{approval.title}</div>
                          <div className="text-xs text-text-tertiary mt-0.5">
                            {approval.description}
                            {approval.amount && <span className="ml-2 text-warning font-medium">¥{approval.amount.toLocaleString()}</span>}
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <button
                            onClick={() => handleApprove(approval)}
                            disabled={actingApprovalId === approval.id}
                            className="px-2.5 py-1 rounded text-xs bg-success text-white hover:bg-success/90 transition-colors disabled:opacity-50"
                          >
                            通过
                          </button>
                          <button
                            onClick={() => handleReject(approval)}
                            disabled={actingApprovalId === approval.id}
                            className="px-2.5 py-1 rounded text-xs bg-error text-white hover:bg-error/90 transition-colors disabled:opacity-50"
                          >
                            拒绝
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </CardBody>
              </Card>
            </motion.div>
          )}
        </AnimatePresence>

        {/* AI 行为观察日志（真实协作事件流） */}
        <Card className="shadow-soft">
          <CardBody>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
                <Activity className="w-4 h-4 text-brand-500" aria-hidden="true" />
                AI 行为观察日志
              </h2>
              <span className="text-xs text-text-tertiary">最近 {events.length} 条协作事件</span>
            </div>
            {events.length === 0 ? (
              <div className="text-center py-8 text-text-tertiary text-sm">
                暂无协作事件，点击右上角「一键演示」生成事件流
              </div>
            ) : (
              /* 用 EventStream 替代原手写列表：
                 原实现用 JSON.stringify(payload).slice(0,80) 把原始 JSON
                 裸露给用户，且丢弃了 status 与 target_agent_id */
              <EventStream
                events={events.slice(0, 15).map((e) => ({
                  id: e.event_id,
                  event_type: e.event_type,
                  payload: e.payload as Record<string, unknown>,
                  source_agent_id: e.source_agent_id,
                  target_agent_id: e.target_agent_id,
                  status: e.status,
                  created_at: e.created_at,
                }))}
                agentNames={agentNameMap}
                maxHeight={320}
              />
            )}
          </CardBody>
        </Card>

        {/* 补1：影子任务状态机（真实后端数据） */}
        <Card className="shadow-soft">
          <CardBody>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
                <Eye className="w-4 h-4 text-brand-500" aria-hidden="true" />
                影子任务状态机
                <span className="text-xs font-normal text-text-tertiary">shadowing → evaluating → qualified → autonomous</span>
              </h2>
              <span className="text-xs text-text-tertiary">共 {shadowTasks.length} 条</span>
            </div>
            {shadowTasks.length === 0 ? (
              <div className="text-center py-8 text-text-tertiary text-sm">
                暂无影子任务，请通过「一键演示」或后端 API 生成样本
              </div>
            ) : (
              <div className="space-y-3">
                {shadowTasks.slice(0, 10).map((task) => {
                  const st = SHADOW_STATUS[task.status] || SHADOW_STATUS.shadowing
                  const busy = actingShadowId === task.id
                  return (
                    <div key={task.id} className="p-4 bg-elevated rounded-md border border-border-default">
                      <div className="flex items-start justify-between gap-3 mb-3">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium border ${st.bg} ${st.color} flex-shrink-0`}>
                          {st.label}
                        </span>
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          {task.status === 'shadowing' && (
                            <button
                              onClick={() => handleRecordAi(task)}
                              disabled={busy}
                              className="px-2 py-1 rounded text-xs bg-info/10 text-info border border-info/30 hover:bg-info/20 transition-colors disabled:opacity-50"
                              title="调用真实 AI 员工对该问题作答"
                            >
                              {busy ? 'AI 作答中…' : 'AI 作答'}
                            </button>
                          )}
                          {task.status === 'evaluating' && (
                            <>
                              {/* 双向评估：此前只有「评估通过」单向路径，
                                  分歧样本无法记录，一致率必然虚高 */}
                              <button
                                onClick={() => handleEvaluate(task, true)}
                                disabled={busy}
                                className="px-2 py-1 rounded text-xs bg-success/10 text-success border border-success/30 hover:bg-success/20 transition-colors disabled:opacity-50"
                              >
                                判断一致
                              </button>
                              <button
                                onClick={() => handleEvaluate(task, false)}
                                disabled={busy}
                                className="px-2 py-1 rounded text-xs bg-error/10 text-error border border-error/30 hover:bg-error/20 transition-colors disabled:opacity-50"
                              >
                                存在分歧
                              </button>
                            </>
                          )}
                          {task.status === 'qualified' && (
                            <button
                              onClick={() => handlePromote(task)}
                              disabled={busy}
                              className="px-2 py-1 rounded text-xs bg-brand-500/10 text-brand-500 border border-brand-500/30 hover:bg-brand-500/20 transition-colors disabled:opacity-50"
                            >
                              授权自主
                            </button>
                          )}
                          {task.status === 'autonomous' && (
                            <button
                              onClick={() => handleDemote(task)}
                              disabled={busy}
                              className="px-2 py-1 rounded text-xs bg-error/10 text-error border border-error/30 hover:bg-error/20 transition-colors disabled:opacity-50"
                            >
                              异常降级
                            </button>
                          )}
                        </div>
                      </div>

                      {/* 人机对照：影子模式的核心价值所在。
                          此前 human_answer / ai_answer 一个字都没展示，
                          只显示了问题标题与一句「与真人一致」，
                          用户无从判断 AI 到底学得怎么样。 */}
                      <HumanAIDiff
                        question={task.question}
                        humanAnswer={task.human_answer}
                        aiAnswer={task.ai_answer}
                        confidence={task.confidence}
                        evalResult={task.eval_result}
                        taskType={task.task_type}
                      />
                    </div>
                  )
                })}
              </div>
            )}
          </CardBody>
        </Card>

        {/* 当前阶段的员工列表 */}
        <Card className="shadow-soft">
          <CardBody>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
                <Users className="w-4 h-4 text-brand-500" aria-hidden="true" />
                {activeConfig.label}员工
              </h2>
              <span className="text-xs text-text-tertiary">共 {activeAgents.length} 名</span>
            </div>
            {activeAgents.length === 0 ? (
              <div className="text-center py-8 text-text-tertiary text-sm">
                当前阶段暂无员工，请先在企业编译页面生成 AI 员工
              </div>
            ) : (
              <div className="space-y-2">
                {activeAgents.map((agent) => {
                  const completion = agent.tasks_total > 0 ? (agent.tasks_completed / agent.tasks_total) * 100 : 0
                  return (
                    <div key={agent.agent_id} className="flex items-center gap-3 p-3 bg-elevated rounded-md border border-border-default hover:border-border-strong transition-colors">
                      <div className="w-9 h-9 rounded-full bg-gradient-to-br from-brand-400 to-brand-600 flex items-center justify-center text-white text-sm font-semibold flex-shrink-0">
                        {agent.agent_name?.charAt(0) || '?'}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-text-primary truncate">{agent.agent_name}</span>
                          <span className="text-xs text-text-tertiary">{positionToLabel(agent.position)}</span>
                        </div>
                        <div className="flex items-center gap-3 mt-1 text-xs text-text-tertiary">
                          <span>任务 {agent.tasks_completed}/{agent.tasks_total}</span>
                          {agent.avg_satisfaction > 0 && <span>满意度 {formatSatisfaction(agent.avg_satisfaction)}</span>}
                        </div>
                      </div>
                      <div className="flex-shrink-0">
                        {completion >= 80 ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-success/10 text-success border border-success/30">
                            <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
                            表现优秀
                          </span>
                        ) : completion >= 50 ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-info/10 text-info border border-info/30">
                            <Shield className="w-3 h-3" aria-hidden="true" />
                            稳定运行
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-warning/10 text-warning border border-warning/30">
                            <AlertTriangle className="w-3 h-3" aria-hidden="true" />
                            需要关注
                          </span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </Layout>
  )
}

/** 按信任阶段统计员工数 */
function countByStage(agents: AgentRunMetrics[]): Record<TrustStage, number> {
  const counts: Record<TrustStage, number> = { shadow: 0, evaluation: 0, autonomous: 0 }
  agents.forEach((agent) => {
    const stage = STAGES.find((s) => s.lifecycleStages.includes(agent.lifecycle_stage))
    if (stage) counts[stage.key]++
  })
  return counts
}
