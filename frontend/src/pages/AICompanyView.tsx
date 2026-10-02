/**
 * AICompanyView — 今日 AI 公司视图（V3.2 重构版）。
 *
 * 对标 PRD §6.2 + prototype page-company，打造"企业在运转的界面"。
 *
 * 重构要点（解决 endless scrolling，对标原型设计语言）：
 * - 顶部 4 卡统计 → MetricGrid（horizontal 变体）
 * - 任务流/协作流程/协作关系 → 单 Card + InlineTabs 三视图切换（原三块平铺）
 * - 协作流程 → FlowChain 组件（SVG flow-dash 动画箭头）
 * - 协作关系图 → 派生 SVG 节点图（Agent 圆形 + 事件箭头）
 * - 待审批/告警 → SectionGroup 折叠（告警默认折叠）
 * - 运营概览：8 项核心指标 → MetricGrid compact；L1-L5 → LifecycleBar
 * - 空态 → EmptyState + CTA 引导（绝不造假数据）
 * - 动效 → Framer Motion 错峰入场
 *
 * 数据来源：collaboration events + workforce + pending approvals + runtime + orgMetrics。
 * 错误状态：API 失败时显示告警 + 重试按钮（spec.md §2.5 约束）。
 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { motion } from 'framer-motion'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import {
  Activity, CheckCircle2, Clock, AlertTriangle,
  ArrowRight, Bell, Zap, Play, Users, FileText,
  DollarSign, TrendingUp as TrendingUpIcon, Wrench, Network,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { ValueChainPanel } from '@/components/ValueChainPanel'
import { CompanyValueTour } from '@/components/CompanyValueTour'
import { ProductJourneyInsights } from '@/components/ProductJourneyInsights'

import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'
import { ApiErrorState } from '@/components/ApiErrorState'
import { MetricGrid } from '@/components/ui/MetricGrid'
import { FlowChain, type FlowStep } from '@/components/ui/FlowChain'
import { SectionGroup } from '@/components/ui/SectionGroup'
import { EmptyState } from '@/components/ui/EmptyState'
import { LifecycleBar, type LifecycleStage } from '@/components/ui/LifecycleBar'
import { TaskKanban } from '@/components/TaskKanban'
import * as collaborationApi from '@/api/collaboration'
import * as workforceApi from '@/api/workforce'
import * as runtimeApi from '@/api/runtime'
import * as evolutionApi from '@/api/evolution'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { useLiveCompany } from '@/hooks/useLiveCompany'
import {
  VitalPulse,
  InstrumentPanel,
  MetricReadout,
  LastUpdated,
  BottleneckCallout,
  SignalBadge,
} from '@/components/instrument'
import { formatRatio } from '@/utils/format'
import {
  trackCompanyProductEvent,
  trackCompanyProductEventOnce,
  type CompanyBriefAction,
  type CompanyJourneyState,
} from '@/utils/productAnalytics'

import type {
  CollaborationEvent,
  AgentRunMetrics,
  ApprovalRequest,
  CollaborationEventType,
  OrgMetrics,
  RuntimeCompileResult,
} from '@/types'

const EVENT_LABELS: Record<CollaborationEventType, string> = {
  inquiry_received: '收到询盘',
  product_query: '产品参数查询',
  quotation_generated: '销售报价',
  approval_submitted: '财务审核',
  approval_approved: '审批通过',
  order_synced: '客服同步',
  after_sales: '售后接管',
  handoff: '转交',
  escalation: '升级',
  error: '错误',
  opportunity_created: '商机创建',
  approval_flow_created: '审批流创建',
  deal_closed: '成交',
}

const EVENT_ICONS: Record<CollaborationEventType, typeof Clock> = {
  inquiry_received: Clock,
  product_query: ArrowRight,
  quotation_generated: FileText,
  approval_submitted: AlertTriangle,
  approval_approved: CheckCircle2,
  order_synced: CheckCircle2,
  after_sales: AlertTriangle,
  handoff: ArrowRight,
  escalation: AlertTriangle,
  error: AlertTriangle,
  opportunity_created: Zap,
  approval_flow_created: AlertTriangle,
  deal_closed: CheckCircle2,
}

type FlowStepStatus = 'done' | 'in_progress' | 'pending'

interface DerivedFlowStep {
  type: CollaborationEventType
  label: string
  event: CollaborationEvent | undefined
  status: FlowStepStatus
}

type OperatingBriefAction =
  | 'compile'
  | 'staff'
  | 'start-demo'
  | 'review-approvals'
  | 'inspect-execution'
  | 'view-impact'

interface OperatingBriefPlan {
  eyebrow: string
  title: string
  description: string
  evidence: string
  actionLabel: string
  action: OperatingBriefAction
  icon: typeof Activity
  tone: 'brand' | 'success' | 'warning' | 'danger'
}

function getJourneyState(action: OperatingBriefAction): CompanyJourneyState {
  const stateByAction: Record<OperatingBriefAction, CompanyJourneyState> = {
    compile: 'needs_compile',
    staff: 'needs_staff',
    'review-approvals': 'needs_approval',
    'inspect-execution': 'needs_attention',
    'start-demo': 'needs_first_run',
    'view-impact': 'creating_value',
  }
  return stateByAction[action]
}

function getAnalyticsAction(action: OperatingBriefAction): CompanyBriefAction {
  const analyticsActionByAction: Record<OperatingBriefAction, CompanyBriefAction> = {
    compile: 'compile',
    staff: 'staff',
    'review-approvals': 'review_approvals',
    'inspect-execution': 'inspect_execution',
    'start-demo': 'start_demo',
    'view-impact': 'view_impact',
  }
  return analyticsActionByAction[action]
}

// L1-L5 成熟度阶段定义（对标 prototype §老板运营中心）
const MATURITY_STAGES: LifecycleStage[] = [
  { key: 'L1', label: 'L1 辅助', status: 'done' },
  { key: 'L2', label: 'L2 协作', status: 'done' },
  { key: 'L3', label: 'L3 自主', status: 'current' },
  { key: 'L4', label: 'L4 优化', status: 'pending' },
  { key: 'L5', label: 'L5 进化', status: 'pending' },
]

// 错峰入场动画容器
const staggerContainer = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.05 },
  },
}
const staggerItem = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.2 } },
}

export default function AICompanyView() {
  const enterpriseId = useEnterpriseId()
  const navigate = useNavigate()
  // UI v4：实时通道（SSE 优先，失败自动降级轮询）——
  // 修复此前「标称实时运转但零轮询」的名实不符问题
  const live = useLiveCompany(enterpriseId)
  const [agents, setAgents] = useState<AgentRunMetrics[]>([])
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([])
  const [orgMetrics, setOrgMetrics] = useState<OrgMetrics | null>(null)
  const [runtime, setRuntime] = useState<RuntimeCompileResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // #2 顶部三 Tab：实时运转 / 运营概览 / 业务效果
  // 支持 ?tab= 深链（如 /company?tab=business），与 URL 双向同步
  const [searchParams] = useSearchParams()
  const [activeTab, setActiveTab] = useState<'runtime' | 'overview' | 'business'>(() => {
    const t = searchParams.get('tab')
    if (t === 'overview' || t === 'business') return t
    return 'runtime'
  })
  const [processingApprovalId, setProcessingApprovalId] = useState<string | null>(null)
  const [demoLoading, setDemoLoading] = useState(false)

  // 响应外部 ?tab= 深链变化（如 /company?tab=business），保持页内 Tab 与 URL 同步
  useEffect(() => {
    const t = searchParams.get('tab')
    const next = t === 'overview' || t === 'business' ? t : 'runtime'
    setActiveTab(next)
  }, [searchParams])

  // 事件流由实时通道提供（SSE/轮询自动切换）。
  // 页面内多处按「最近优先」消费，故此处反转为倒序视图。
  const events = useMemo<CollaborationEvent[]>(
    () => [...live.events].reverse(),
    [live.events],
  )
  const vitals = live.vitals

  const loadData = useCallback(async () => {
    if (!enterpriseId) return
    setLoading(true)
    setError(null)
    try {
      // 事件流已由 useLiveCompany 负责，此处只取员工/审批/运行时/指标。
      // 全部使用 allSettled 容错：任一失败不阻塞驾驶舱主视图
      const [wfRes, apprRes, rtRes, metricsRes] = await Promise.allSettled([
        workforceApi.listWorkforce(enterpriseId),
        collaborationApi.getPendingApprovals(enterpriseId),
        runtimeApi.getRuntime(enterpriseId),
        evolutionApi.getOrgMetrics(enterpriseId),
      ])
      if (wfRes.status === 'fulfilled') setAgents(wfRes.value.items)
      if (apprRes.status === 'fulfilled') setApprovals(apprRes.value)
      setRuntime(rtRes.status === 'fulfilled' ? rtRes.value : null)
      setOrgMetrics(metricsRes.status === 'fulfilled' ? metricsRes.value : null)
      if (wfRes.status === 'rejected' && apprRes.status === 'rejected') {
        throw new Error('加载今日公司数据失败')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载今日公司数据失败')
    } finally {
      setLoading(false)
    }
  }, [enterpriseId])

  // WT5: 审批处理（批准/拒绝）。按 approvalId 跟踪，只让被点击的那一项转圈，
  // 其余项不受影响；批准/拒绝成功后先乐观移除该项，再刷新待审批列表。
  const handleApprove = useCallback(async (approvalId: string) => {
    setProcessingApprovalId(approvalId)
    try {
      await collaborationApi.approveRequest(approvalId)
      // 乐观移除：即使后续刷新稍慢/失败，该项也不会残留在待审批中
      setApprovals((prev) => prev.filter((a) => a.id !== approvalId))
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : '审批操作失败')
    } finally {
      setProcessingApprovalId(null)
    }
  }, [loadData])

  const handleReject = useCallback(async (approvalId: string, reason: string) => {
    setProcessingApprovalId(approvalId)
    try {
      await collaborationApi.rejectRequest(approvalId, { reason })
      setApprovals((prev) => prev.filter((a) => a.id !== approvalId))
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : '审批操作失败')
    } finally {
      setProcessingApprovalId(null)
    }
  }, [loadData])

  useEffect(() => {
    loadData()
  }, [loadData])

  // 触发协作流程：从 workforce 中按 position 挑选 5 个角色 Agent
  const handleTriggerDemo = useCallback(async () => {
    if (!enterpriseId || agents.length === 0) return
    const pick = (pos: string) =>
      agents.find((a) => a.position === pos)?.agent_id || agents.find((a) => a.position?.includes(pos))?.agent_id
    const sales = pick('pos_sales')
    const presales = pick('pos_pre_sales')
    const finance = pick('pos_finance')
    const cs = pick('pos_customer_service')
    const aft = pick('pos_after_sales')
    if (!sales || !presales || !finance || !cs || !aft) {
      setError('未找到完整的 5 个角色 Agent（销售/售前/财务/客服/售后），请先在 AI 员工页生成完整团队')
      return
    }
    setDemoLoading(true)
    setError(null)
    try {
      await collaborationApi.triggerDemoCase(enterpriseId, {
        sales_agent_id: sales,
        product_expert_agent_id: presales,
        finance_agent_id: finance,
        customer_service_agent_id: cs,
        after_sales_agent_id: aft,
      })
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : '启动协作流程失败')
    } finally {
      setDemoLoading(false)
    }
  }, [enterpriseId, agents, loadData])

  // 异常与告警：从 agents 中筛选失败任务（≥3 失败为 error 级别，1-2 为 warning）
  const alerts = agents
    .filter((a) => a.tasks_failed > 0)
    .map((a) => ({
      agent_name: a.agent_name,
      message: `${a.tasks_failed} 个任务失败，需关注`,
      level: (a.tasks_failed >= 3 ? 'error' : 'warning') as 'error' | 'warning',
    }))

  // 业务运行状态概览
  const totalTasks = agents.reduce((sum, a) => sum + a.tasks_total, 0)
  const completedTasks = agents.reduce((sum, a) => sum + a.tasks_completed, 0)
  const onlineAgents = agents.filter((a) => a.lifecycle_stage === 'production' || a.lifecycle_stage === 'training').length

  // 动态协作流程：从 events 派生最近一条协作链路的进度节点。
  // events 已按「最新在前」排序；对每个环节节点取「该类型最新一条事件」作为节点时间，
  // 确保显示的是最新进度而非历史首条。
  const flowSteps = useMemo<FlowStep[]>(() => {
    const stepOrder: CollaborationEventType[] = [
      'inquiry_received', 'product_query', 'quotation_generated',
      'approval_submitted', 'approval_approved', 'order_synced', 'after_sales',
    ]
    const steps: DerivedFlowStep[] = stepOrder.map((type) => {
      const evt = events.find((e) => e.event_type === type)
      return { type, label: EVENT_LABELS[type], event: evt, status: evt ? 'done' : 'pending' }
    })
    // 将第一个尚未发生的环节标为「进行中」，直观呈现链路当前推进位置
    const firstPendingIdx = steps.findIndex((s) => s.status === 'pending')
    if (firstPendingIdx >= 0) {
      steps[firstPendingIdx] = { ...steps[firstPendingIdx], status: 'in_progress' }
    }
    return steps.map((s) => ({
      id: s.type,
      label: s.label,
      icon: EVENT_ICONS[s.type],
      status: s.status,
      hint: s.event ? new Date(s.event.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : undefined,
    }))
  }, [events])

  // 「运营概览」Tab 派生数据
  const businessCompletionRate = totalTasks > 0 ? (completedTasks / totalTasks) * 100 : 0
  const completeness = runtime?.completeness || 0
  // #7 AI 员工数量取真实 workforce 数（与 AI 员工页一致），不再依赖 runtime.agents.length
  const agentCount = agents.length
  const workloadData = agents.map((a) => ({
    name: a.agent_name.replace('（AI）', ''),
    tasks: a.tasks_total,
    completed: a.tasks_completed,
  }))
  // #8 工具使用按后端聚合契约渲染（total_tools 工具总数 / installed 已安装 / verified 已验证）
  const toolUsage = orgMetrics?.tool_usage
    ? {
        total: orgMetrics.tool_usage.total_tools ?? 0,
        installed: orgMetrics.tool_usage.installed ?? 0,
        verified: orgMetrics.tool_usage.verified ?? 0,
        installRate: orgMetrics.tool_usage.install_rate ?? 0,
      }
    : null

  // 成熟度阶段（根据 orgMetrics 动态计算）
  const maturityStages = useMemo<LifecycleStage[]>(() => {
    const level = orgMetrics?.maturity_level || 'L1'
    const idx = MATURITY_STAGES.findIndex((s) => s.key === level)
    return MATURITY_STAGES.map((s, i) => ({
      ...s,
      status: i < idx ? 'done' : i === idx ? 'current' : 'pending',
    }))
  }, [orgMetrics])

  // 知识文件数：取自 Runtime 知识索引（替代此前硬编码的 fileCount={0}）。
  // 无 runtime 时为 0 —— 诚实反映「尚未编译」，不编造数字。
  const knowledgeFileCount = useMemo(() => {
    const idx = runtime?.knowledge_index as
      | { document_count?: number; chunk_count?: number }
      | undefined
    return idx?.document_count ?? idx?.chunk_count ?? 0
  }, [runtime])

  // agent_id → 显示名映射
  const agentNameMap = useMemo(() => {
    const map: Record<string, string> = {}
    agents.forEach((a) => {
      map[a.agent_id] = a.agent_name
    })
    return map
  }, [agents])

  // 流程效率：从 orgMetrics.process_efficiency 读取真实聚合（后端为单对象，非每流程映射）。
  // 后端结构：{ total_events, processed_count, automation_rate, event_type_distribution, unit }。
  // 旧前端误把它当 Record<流程名,{...}> 遍历，导致英文键名被渲染成图表分类（Total events…）。
  type ProcessEfficiency = {
    total_events?: number
    processed_count?: number
    automation_rate?: number
    event_type_distribution?: Record<string, number>
  }
  const processEfficiency = orgMetrics?.process_efficiency as ProcessEfficiency | undefined

  // 瓶颈/流程健康：后端无每流程瓶颈数据，诚实展示整体自动化率与事件处理口径，不编造瓶颈。
  const bottleneck = useMemo(() => {
    const pe = orgMetrics?.process_efficiency as ProcessEfficiency | undefined
    const rate =
      pe && typeof pe.automation_rate === 'number' ? pe.automation_rate : null
    return {
      step: null as string | null,
      duration: null as string | null,
      rate,
      totalEvents: pe?.total_events,
      processedEvents: pe?.processed_count,
    }
  }, [orgMetrics])

  // 业务流程自动化率图表数据（供「业务效果」Tab 展示真实流程效率）。
  // 按后端 event_type_distribution 渲染各业务环节事件量，分类名用中文；不再把 JSON 键当分类。
  const processChartData = useMemo(() => {
    const dist = processEfficiency?.event_type_distribution
    if (!dist) return []
    return Object.entries(dist)
      .map(([type, count]) => ({
        name: EVENT_LABELS[type as CollaborationEventType] ?? type,
        事件数: count,
      }))
      .sort((a, b) => b.事件数 - a.事件数)
  }, [processEfficiency])
  // 业务流程自动化率概览（自动化率 = 已处理事件 / 总事件）
  const processSummary = useMemo(() => {
    const pe = processEfficiency
    return {
      rate:
        pe && typeof pe.automation_rate === 'number'
          ? Math.round(pe.automation_rate * 100)
          : null,
      total: pe?.total_events ?? 0,
      processed: pe?.processed_count ?? 0,
    }
  }, [processEfficiency])
  // 业务效果口径说明（基于真实运行时聚合数据推导）
  const bizImpact = orgMetrics?.business_impact
  const bizTooltip: Record<string, string> = {
    output: `业务产出（output_value）＝近 30 天已完成协作/成交按客单价折算的累计业务价值 ¥${bizImpact ? (bizImpact.output_value / 10000).toFixed(1) : '—'} 万。由运行时事件流（成交/报价/订单）乘以产品单价聚合得出，非估算值。`,
    saved: `成本节约（cost_saved）＝替代人时 ${bizImpact?.hours_replaced ?? '—'} 小时 × 人工单位成本（约 60 元/小时）≈ ¥${bizImpact ? (bizImpact.cost_saved / 10000).toFixed(1) : '—'} 万。反映自动化替代人工的直接成本节省。`,
    hours: `替代人时（hours_replaced）＝已解决协作数 ×（人工平均处理时长 − AI 平均处理时长）累计节省的工时 ${bizImpact?.hours_replaced ?? '—'} 小时。数据来自协作事件与 RAG 处理耗时对比。`,
    roi: `综合 ROI＝成本节约 ¥${bizImpact ? (bizImpact.cost_saved / 10000).toFixed(1) : '—'} 万 ÷ 自动化投入 ¥${bizImpact?.ai_cost != null ? (bizImpact.ai_cost / 10000).toFixed(1) : '—'} 万，衡量每投入 1 元自动化成本节省的人工成本倍数（由后端推导，不把企业营收混入分子，避免口径失真）。`,
  }

  

  // 完全空态：无员工无事件 → 引导去编译/生成员工
  const isFullyEmpty = !loading && !live.loading && agents.length === 0 && events.length === 0

  // 将真实运行态转化为单一、可执行的下一步。优先消除运行底座、团队、审批和异常
  // 等阻塞因素，而不是让用户在多块指标之间自行猜测下一步。
  const operatingBrief = useMemo<OperatingBriefPlan>(() => {
    if (!runtime) {
      return {
        eyebrow: '价值路径 · 第 1 步',
        title: '先把企业知识编译成可运行的底座',
        description: '上传资料并完成一次企业编译后，系统才能生成结构化运行时、识别流程缺口，并为 AI 团队提供共同上下文。',
        evidence: knowledgeFileCount > 0 ? `已发现 ${knowledgeFileCount} 份知识资料，等待编译` : '尚未生成 Enterprise Runtime',
        actionLabel: '开始企业编译',
        action: 'compile',
        icon: Network,
        tone: 'brand',
      }
    }
    if (agentCount === 0) {
      return {
        eyebrow: '价值路径 · 第 2 步',
        title: '将运行时转化为可协作的 AI 团队',
        description: '根据已编译的岗位、流程和知识配置生成 AI 员工，随后才能让任务在真实业务链路中流转。',
        evidence: `Runtime ${runtime.version} 已就绪 · 完成度 ${Math.round(runtime.completeness * 100)}%`,
        actionLabel: '生成 AI 员工',
        action: 'staff',
        icon: Users,
        tone: 'brand',
      }
    }
    if (approvals.length > 0) {
      return {
        eyebrow: '需要你的决策',
        title: `${approvals.length} 项业务审批正在等待`,
        description: 'AI 团队已将需要人工判断的节点送达。先完成审批，可让协作流程继续向订单、交付或售后推进。',
        evidence: '审批不会被自动跳过，保留人为治理与审计边界',
        actionLabel: '查看待审批事项',
        action: 'review-approvals',
        icon: Bell,
        tone: 'warning',
      }
    }
    if (alerts.length > 0 || (vitals?.events.failed ?? 0) > 0) {
      const failedEvents = vitals?.events.failed ?? alerts.length
      return {
        eyebrow: '运行需要关注',
        title: '先处理异常，再扩大自动化范围',
        description: '系统识别到失败任务或异常事件。查看执行详情可定位责任 Agent、事件链路和可恢复操作。',
        evidence: `${failedEvents} 个异常信号等待复核`,
        actionLabel: '查看执行详情',
        action: 'inspect-execution',
        icon: AlertTriangle,
        tone: 'danger',
      }
    }
    if (events.length === 0) {
      return {
        eyebrow: '价值路径 · 第 3 步',
        title: '让 AI 团队开始一条可观测的协作链路',
        description: '运行协作流程后，询盘、报价、审批、同步与售后将形成可追踪的事件流，并沉淀为运营与业务效果指标。',
        evidence: `${onlineAgents} 名 AI 员工已在岗，尚无协作事件`,
        actionLabel: '运行协作流程',
        action: 'start-demo',
        icon: Play,
        tone: 'success',
      }
    }
    return {
      eyebrow: '系统正在创造价值',
      title: '从实时运转进一步验证业务效果',
      description: '团队、流程与事件链路均在运行。进入业务效果可查看自动化率、替代人时、成本节约与 ROI 的真实聚合口径。',
      evidence: `近 24 小时 ${vitals?.events.last_24h ?? events.length} 个协作事件 · ${onlineAgents} 名员工在岗`,
      actionLabel: '查看业务效果',
      action: 'view-impact',
      icon: CheckCircle2,
      tone: 'success',
    }
  }, [agentCount, alerts.length, approvals.length, events.length, knowledgeFileCount, onlineAgents, runtime, vitals?.events.failed, vitals?.events.last_24h])

  const journeyState = getJourneyState(operatingBrief.action)

  useEffect(() => {
    trackCompanyProductEventOnce(`company-brief-viewed-${journeyState}`, 'company_brief_viewed', {
      journey_state: journeyState,
      action: getAnalyticsAction(operatingBrief.action),
    })
  }, [journeyState, operatingBrief.action])

  const handleOperatingAction = useCallback(() => {
    trackCompanyProductEvent('company_brief_action_clicked', {
      journey_state: journeyState,
      action: getAnalyticsAction(operatingBrief.action),
    })
    switch (operatingBrief.action) {
      case 'compile':
        navigate('/build?tab=compile')
        return
      case 'staff':
        navigate('/employees?tab=workforce')
        return
      case 'start-demo':
        void handleTriggerDemo()
        return
      case 'review-approvals':
        document.getElementById('company-task-flow')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
        return
      case 'inspect-execution':
        navigate('/work-execution')
        return
      case 'view-impact':
        navigate('/company?tab=business')
        return
    }
  }, [handleTriggerDemo, journeyState, navigate, operatingBrief.action])

  if ((loading || live.loading) && events.length === 0 && agents.length === 0) {
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
      {/* 嵌入 HubPage 时，HubPage 已提供 max-w-7xl + px 容器；此处不再重复加边距，避免侧边距过宽 */}
      <div className="w-full space-y-8">
        {/* 企业运行态势动线 + 价值链仅在「实时运转」Tab 展示，运营概览/业务效果聚焦各自指标 */}
        {activeTab === 'runtime' && (<>
        {/* ============================================================
            生命体征带（UI v4 §六 战场一）
            让「这家 AI 公司此刻正活着」在 3 秒内可见。
            心跳波形由真实数据驱动：振幅=在岗员工数、频率=事件流速率、
            颜色=健康度；编译进行中自动切换为扫描线形态。
            ============================================================ */}
        <InstrumentPanel
          label="企业运行态势"
          igniteIndex={0}
          flush
          action={
            <LastUpdated
              timestamp={live.lastUpdated}
              refreshing={live.refreshing}
              mode={live.mode}
              onRefresh={() => {
                live.refresh()
                loadData()
              }}
            />
          }
        >
          <div className="px-5 pb-5">
            <VitalPulse
              activeCount={vitals?.agents.production ?? onlineAgents}
              eventsPerHour={vitals?.events.per_hour ?? 0}
              tone={vitals?.health.tone ?? 'idle'}
              scanning={vitals?.compile.running ?? false}
              height={64}
            />
            <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
              <MetricReadout
                label="在岗员工"
                value={vitals?.agents.production ?? onlineAgents}
                unit="人"
                tone="alive"
                size="md"
                hint={
                  vitals
                    ? `编制 ${vitals.agents.total} · 培训 ${vitals.agents.training}`
                    : undefined
                }
              />
              <MetricReadout
                label="事件速率"
                value={vitals?.events.per_hour ?? 0}
                unit="件/时"
                tone="focus"
                digits={vitals && vitals.events.per_hour % 1 !== 0 ? 1 : 0}
                hint={vitals ? `近 24h 共 ${vitals.events.last_24h} 件` : undefined}
              />
              <MetricReadout
                label="待审批"
                value={vitals?.approvals.pending ?? approvals.length}
                unit="项"
                tone={(vitals?.approvals.pending ?? approvals.length) > 0 ? 'alert' : 'idle'}
                hint="需人工决策"
              />
              <MetricReadout
                label="信任度"
                // 真实计算（一致判定/已评估），无样本时诚实显示「样本不足」
                value={
                  vitals?.shadow.trust_score != null
                    ? formatRatio(vitals.shadow.trust_score)
                    : '样本不足'
                }
                tone={
                  vitals?.shadow.trust_score == null
                    ? 'idle'
                    : vitals.shadow.trust_score >= 0.85
                      ? 'alive'
                      : vitals.shadow.trust_score >= 0.7
                        ? 'alert'
                        : 'fault'
                }
                hint={
                  vitals && vitals.shadow.evaluated > 0
                    ? `${vitals.shadow.match}/${vitals.shadow.evaluated} 人机一致`
                    : '影子模式尚无评估样本'
                }
              />
              <MetricReadout
                label="系统健康"
                value={vitals ? formatRatio(vitals.health.score) : '—'}
                tone={vitals?.health.tone === 'alive' ? 'alive' : vitals?.health.tone ?? 'idle'}
                hint={
                  vitals && vitals.events.failed > 0
                    ? `${vitals.events.failed} 个失败事件`
                    : '运转正常'
                }
              />
            </div>
          </div>
        </InstrumentPanel>

        {/* 价值链：文件数与员工总数改用真实统计，去除 fileCount={0}/agentTotal={10} 硬编码 */}
        <OperatingBrief plan={operatingBrief} onAction={handleOperatingAction} running={demoLoading} />

        <CompanyValueTour
          journeyState={journeyState}
          onStartRecommendedAction={handleOperatingAction}
          recommendedActionLabel={operatingBrief.actionLabel}
          actionIsRunning={demoLoading && operatingBrief.action === 'start-demo'}
        />

        <ValueChainPanel
          fileCount={knowledgeFileCount}
          fileStatus="done"
          agentReady={vitals?.agents.production ?? agentCount}
          agentTotal={vitals?.agents.total ?? agents.length}
          runtimeStatus={runtime ? 'online' : 'offline'}
          runtimeVersion={runtime?.version}
        />

        <ProductJourneyInsights />
        </>)}

        {/* 顶部操作区（Tab 切换由 HubPage 的 SubTabBar 承载，此处不再渲染重复的内联 Tab 栏） */}
        {activeTab === 'runtime' && (
          <div className="flex flex-wrap items-center gap-2">
            {events.length === 0 && agents.length > 0 && (
              <Button variant="outline" size="sm" onClick={handleTriggerDemo} disabled={demoLoading}>
                {demoLoading ? <Spinner size="sm" /> : <Play className="w-4 h-4" aria-hidden="true" />}
                {demoLoading ? '生成中...' : '运行协作流程'}
              </Button>
            )}
            {/* 状态徽章由真实生命体征驱动（编译中 / 有失败事件 / 有待审批 / 无员工 各自呈现不同信号） */}
            {vitals?.compile.running ? (
              <SignalBadge tone="focus" pulse size="md">
                编译中 {formatRatio(vitals.compile.progress ?? 0)}
              </SignalBadge>
            ) : vitals && vitals.events.failed > 0 ? (
              <SignalBadge tone="fault" size="md">
                {vitals.events.failed} 个事件异常
              </SignalBadge>
            ) : vitals && vitals.approvals.pending > 0 ? (
              <SignalBadge tone="alert" pulse size="md">
                {vitals.approvals.pending} 项待审批
              </SignalBadge>
            ) : vitals && vitals.agents.production > 0 ? (
              <SignalBadge tone="alive" pulse size="md">
                运行中
              </SignalBadge>
            ) : (
              <SignalBadge tone="idle" size="md">
                待启动
              </SignalBadge>
            )}
          </div>
        )}

        {error && (
          <ApiErrorState message={error} onRetry={loadData} retrying={loading} />
        )}

        {/* 完全空态引导 */}
        {isFullyEmpty ? (
          <Card>
            <EmptyState
              icon={Network}
              title="还未启动协作流程"
              description="编译企业运行时并生成 AI 员工后，即可在此查看实时协作动态、任务流转与审批事项。"
              action={{ label: '去构建企业', to: '/build' }}
              secondaryAction={{ label: '生成 AI 员工', to: '/employees' }}
            />
          </Card>
        ) : (
          <motion.div variants={staggerContainer} initial="hidden" animate="show" className="space-y-8">
            {/* Tab 1：实时运转（#3 看板 / #5 告警置顶） */}
            {activeTab === 'runtime' && (
              <div className="space-y-6">
                {/* 业务运行状态概览 → MetricGrid */}
                <motion.div variants={staggerItem}>
                  <MetricGrid
                    metrics={[
                      { key: 'online', icon: Activity, value: onlineAgents, label: '在岗 AI 员工', tone: 'success', unit: '人' },
                      { key: 'tasks', icon: Zap, value: totalTasks, label: '今日任务', tone: 'brand' },
                      { key: 'done', icon: CheckCircle2, value: completedTasks, label: '已完成', tone: 'info' },
                      { key: 'approvals', icon: Bell, value: approvals.length, label: '待审批', tone: 'warning' },
                    ]}
                  />
                </motion.div>

                {/* #5 异常与告警 → 显眼位置（顶部高亮卡，不再沉底） */}
                <motion.div variants={staggerItem}>
                  <AlertCallout alerts={alerts} />
                </motion.div>

                {/* #3 协作流程：可折叠，置于看板上方 */}
                <motion.div variants={staggerItem}>
                  <SectionGroup
                    title="协作流程"
                    icon={<Activity className="w-4 h-4" aria-hidden="true" />}
                    description="7 步业务链路实时进度（询盘→产品查询→报价→审批→同步→售后）"
                    defaultOpen={true}
                  >
                    <div className="p-4">
                      {flowSteps.length > 0 && events.length > 0 ? (
                        <div className="space-y-3">
                          {/* 实时更新状态徽标：突出协作流程为实时数据源 */}
                          <div className="flex items-center justify-center gap-2">
                            <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 border border-success/30 px-3 py-1 text-xs font-medium text-success">
                              <span className="relative flex h-2 w-2">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75" />
                                <span className="relative inline-flex rounded-full h-2 w-2 bg-success" />
                              </span>
                              实时运转 · 节点时间即最新一条事件
                            </span>
                          </div>
                          {/* 协作流程：整体居中，节点时间突出实时进度；窄屏可横向滚动不被裁剪 */}
                          <div className="overflow-x-auto scrollbar-thin pb-2">
                            <div className="w-fit mx-auto">
                              <FlowChain steps={flowSteps} scrollable={false} />
                            </div>
                          </div>
                        </div>
                      ) : (
                        <EmptyState
                          icon={Activity}
                          title="协作流程尚未启动"
                          description="运行协作流程后，将在此展示 7 步业务链路的实时进度（询盘→产品查询→报价→审批→同步→售后）。"
                          action={agents.length > 0 ? { label: '运行协作流程', onClick: handleTriggerDemo } : undefined}
                          variant="info"
                        />
                      )}
                    </div>
                  </SectionGroup>
                </motion.div>

                {/* #3 任务流 → 看板（未开始/进行中/已完成/待审批），协作关系融合进看板卡片 */}
                <motion.div variants={staggerItem}>
                  <Card id="company-task-flow">
                    <CardHeader>
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-h4 text-text-primary flex items-center gap-2">
                          <Activity className="w-5 h-5 text-brand-500" aria-hidden="true" />
                          任务流
                        </h3>
                        <span className="text-xs text-text-tertiary">看板视图 · 未开始 / 进行中 / 已完成 / 待审批</span>
                      </div>
                    </CardHeader>
                    <CardBody>
                      <TaskKanban
                        events={live.events}
                        approvals={approvals}
                        typeLabels={EVENT_LABELS}
                        agentNames={agentNameMap}
                        processingApprovalId={processingApprovalId}
                        onApprove={handleApprove}
                        onReject={handleReject}
                        agents={agents}
                        onTriggerDemo={handleTriggerDemo}
                      />
                    </CardBody>
                  </Card>
                </motion.div>
              </div>
            )}

            {/* Tab 2：运营概览 */}
            {activeTab === 'overview' && (
              <div className="space-y-6">
                {/* 核心指标 8 项 → MetricGrid compact */}
                <motion.div variants={staggerItem}>
                  <MetricGrid
                    columns={4}
                    metrics={[
                      { key: 'agents', icon: Users, value: agentCount, label: 'AI 员工数量', tone: 'brand', variant: 'compact', unit: '人' },
                      { key: 'completion', icon: CheckCircle2, value: totalTasks > 0 ? businessCompletionRate.toFixed(0) : '—', label: '业务完成率', tone: 'success', variant: 'compact', unit: totalTasks > 0 ? '%' : '' },
                      { key: 'completeness', icon: TrendingUpIcon, value: Math.round(completeness * 100), label: '完成度', tone: completeness >= 0.8 ? 'success' : 'warning', variant: 'compact', unit: '%' },
                      { key: 'events', icon: Bell, value: events.length, label: '今日协作事件', tone: 'info', variant: 'compact', unit: '次' },
                      { key: 'output', icon: DollarSign, value: orgMetrics?.business_impact?.output_value != null ? `${(orgMetrics.business_impact.output_value / 10000).toFixed(1)}` : '—', label: '业务产出', tone: 'success', variant: 'compact', unit: '万' },
                      { key: 'saved', icon: DollarSign, value: orgMetrics?.business_impact?.cost_saved != null ? `${(orgMetrics.business_impact.cost_saved / 10000).toFixed(1)}` : '—', label: '成本节约', tone: 'success', variant: 'compact', unit: '万' },
                      { key: 'hours', icon: Clock, value: orgMetrics?.business_impact?.hours_replaced ?? '—', label: '替代人时', tone: 'brand', variant: 'compact', unit: orgMetrics?.business_impact?.hours_replaced != null ? '小时' : '' },
                      { key: 'roi', icon: TrendingUpIcon, value: orgMetrics?.business_impact?.roi != null ? `${orgMetrics.business_impact.roi}` : '—', label: '综合 ROI', tone: 'success', variant: 'compact', unit: 'x' },
                    ]}
                  />
                </motion.div>

                {/* AI 成熟度等级 → LifecycleBar */}
                <motion.div variants={staggerItem}>
                  {/* 流程瓶颈：启用此前完全丢弃的 process_efficiency 洞察。
                      老板最想知道的就是「哪里卡住了」，此前零展示。 */}
                  <BottleneckCallout
                    step={bottleneck.step}
                    duration={bottleneck.duration}
                    automationRate={bottleneck.rate}
                    totalEvents={bottleneck.totalEvents}
                    processedEvents={bottleneck.processedEvents}
                  />
                </motion.div>

                <motion.div variants={staggerItem}>
                  <Card>
                    <CardHeader>
                      <div className="flex items-center justify-between">
                        <div>
                          <h3 className="text-h4 text-text-primary">AI 成熟度等级</h3>
                          <p className="text-sm text-text-tertiary mt-1">企业 AI 自主化进程 L1→L5</p>
                        </div>
                        {orgMetrics && (
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-brand-50 text-brand-500 text-sm font-medium">
                            当前 {orgMetrics.maturity_level}
                          </span>
                        )}
                      </div>
                    </CardHeader>
                    <CardBody>
                      {orgMetrics ? (
                        <LifecycleBar stages={maturityStages} />
                      ) : (
                        <EmptyState
                          icon={TrendingUpIcon}
                          title="暂无运营数据"
                          description="编译企业运行时并运行协作流程后，将在此展示 L1-L5 成熟度等级与运营指标。"
                          action={{ label: '去编译', to: '/build?tab=compile' }}
                          variant="info"
                        />
                      )}
                    </CardBody>
                  </Card>
                </motion.div>

                {/* Agent 工作量分布（recharts 堆叠柱状图） */}
                <motion.div variants={staggerItem}>
                  <Card>
                    <CardHeader>
                      <h3 className="text-h4 text-text-primary flex items-center gap-2">
                        <Activity className="w-5 h-5 text-brand-500" aria-hidden="true" />
                        Agent 工作量分布
                      </h3>
                      <p className="text-sm text-text-tertiary mt-1">各 Agent 的负载情况</p>
                    </CardHeader>
                    <CardBody>
                      {workloadData.length === 0 ? (
                        <EmptyState icon={Activity} title="暂无工作量数据" description="AI 员工开始执行任务后，将在此展示工作量分布。" variant="info" />
                      ) : (
                        <div style={{ height: '240px' }}>
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={workloadData} margin={{ top: 10, right: 10, left: 0, bottom: 10 }}>
                              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                              <XAxis dataKey="name" tick={{ fontSize: 12 }} stroke="var(--text-tertiary)" />
                              <YAxis tick={{ fontSize: 12 }} stroke="var(--text-tertiary)" />
                              <Tooltip
                                contentStyle={{
                                  background: 'var(--bg-elevated)',
                                  border: '1px solid var(--border-default)',
                                  borderRadius: '8px',
                                  fontSize: '12px',
                                }}
                              />
                              <Bar dataKey="tasks" name="总任务" fill="var(--brand-500)" radius={[4, 4, 0, 0]} />
                              <Bar dataKey="completed" name="已完成" fill="var(--success)" radius={[4, 4, 0, 0]} />
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                      )}
                    </CardBody>
                  </Card>
                </motion.div>

                {/* 工具使用热度（#8：按后端聚合契约渲染中文） */}
                <motion.div variants={staggerItem}>
                  <Card>
                    <CardHeader>
                      <h3 className="text-h4 text-text-primary flex items-center gap-2">
                        <Wrench className="w-5 h-5 text-info" aria-hidden="true" />
                        工具使用热度
                      </h3>
                      <p className="text-sm text-text-tertiary mt-1">工具注册与启用情况（工具总数 / 已安装 / 已验证）</p>
                    </CardHeader>
                    <CardBody>
                      {!toolUsage || toolUsage.total === 0 ? (
                        <EmptyState icon={Wrench} title="暂无工具使用数据" description="编译企业运行时后，将在此展示工具总数、已安装与已验证情况。" variant="info" />
                      ) : (
                        <div>
                          <div className="grid grid-cols-3 gap-3">
                            <div className="rounded-lg border border-border-default bg-elevated/40 p-3 text-center">
                              <div className="text-2xl font-bold text-brand-500">{toolUsage.total}</div>
                              <div className="text-xs text-text-tertiary mt-1">工具总数（total_tools）</div>
                            </div>
                            <div className="rounded-lg border border-border-default bg-elevated/40 p-3 text-center">
                              <div className="text-2xl font-bold text-success">{toolUsage.installed}</div>
                              <div className="text-xs text-text-tertiary mt-1">已安装（installed）</div>
                            </div>
                            <div className="rounded-lg border border-border-default bg-elevated/40 p-3 text-center">
                              <div className="text-2xl font-bold text-info">{toolUsage.verified}</div>
                              <div className="text-xs text-text-tertiary mt-1">已验证（verified）</div>
                            </div>
                          </div>
                          <div className="mt-4">
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-sm text-text-secondary">安装率（installed / total）</span>
                              <span className="text-sm font-medium text-text-primary">
                                {Math.round(toolUsage.installRate * 100)}%
                              </span>
                            </div>
                            <div className="h-2 bg-elevated rounded-full overflow-hidden">
                              <div
                                className="h-full bg-brand-500 rounded-full transition-all duration-500"
                                style={{ width: `${Math.min(100, toolUsage.installRate * 100)}%` }}
                              />
                            </div>
                          </div>
                        </div>
                      )}
                    </CardBody>
                  </Card>
                </motion.div>
              </div>
            )}

            {/* Tab 3：业务效果（承接业务效果仪表盘，含悬浮口径说明 + 真实流程图表） */}
            {activeTab === 'business' && (
              <div className="space-y-6">
                {/* 四大核心指标卡：悬浮说明推导口径（基于实时运行时真实数据） */}
                <motion.div variants={staggerItem}>
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                    <BizMetricCard
                      icon={DollarSign}
                      tone="success"
                      label="业务产出"
                      value={bizImpact?.output_value != null ? `¥${(bizImpact.output_value / 10000).toFixed(1)} 万` : '—'}
                      tooltip={bizTooltip.output}
                    />
                    <BizMetricCard
                      icon={DollarSign}
                      tone="success"
                      label="成本节约"
                      value={bizImpact?.cost_saved != null ? `¥${(bizImpact.cost_saved / 10000).toFixed(1)} 万` : '—'}
                      tooltip={bizTooltip.saved}
                    />
                    <BizMetricCard
                      icon={Clock}
                      tone="brand"
                      label="替代人时"
                      value={bizImpact?.hours_replaced != null ? `${bizImpact.hours_replaced} 小时` : '—'}
                      tooltip={bizTooltip.hours}
                    />
                    <BizMetricCard
                      icon={TrendingUpIcon}
                      tone="success"
                      label="综合 ROI"
                      value={bizImpact?.roi != null ? `${bizImpact.roi}x` : '—'}
                      tooltip={bizTooltip.roi}
                    />
                  </div>
                </motion.div>

                {/* 业务流程自动化率：真实流程效率（自动化率概览 + 各业务环节事件量） */}
                <motion.div variants={staggerItem}>
                  <Card>
                    <CardHeader>
                      <h3 className="text-h4 text-text-primary flex items-center gap-2">
                        <Wrench className="w-5 h-5 text-brand-500" aria-hidden="true" />
                        协作事件自动化率
                      </h3>
                      <p className="text-sm text-text-tertiary mt-1">自动化率（Automation Rate）＝已自动化处理事件 ÷ 协作事件总数，反映 AI 对各业务环节事件的处理比重，按业务环节展示真实事件量</p>
                    </CardHeader>
                    <CardBody>
                      {processChartData.length === 0 ? (
                        <EmptyState
                          icon={Wrench}
                          title="暂无流程效率数据"
                          description="编译企业运行时并运行协作流程后，将在此展示业务流程自动化率与各业务环节事件量。"
                          variant="info"
                        />
                      ) : (
                        <div>
                          {/* 自动化率概览 */}
                          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
                            <div className="rounded-lg border border-border-default bg-elevated/40 p-4 text-center">
                              <div className="text-2xl font-bold text-brand-500">
                                {processSummary.rate != null ? `${processSummary.rate}%` : '—'}
                              </div>
                              <div className="text-xs text-text-tertiary mt-1">协作事件自动化率</div>
                            </div>
                            <div className="rounded-lg border border-border-default bg-elevated/40 p-4 text-center">
                              <div className="text-2xl font-bold text-success">{processSummary.processed}</div>
                              <div className="text-xs text-text-tertiary mt-1">已自动化处理（件）</div>
                            </div>
                            <div className="rounded-lg border border-border-default bg-elevated/40 p-4 text-center">
                              <div className="text-2xl font-bold text-text-primary">{processSummary.total}</div>
                              <div className="text-xs text-text-tertiary mt-1">协作事件总数（件）</div>
                            </div>
                          </div>
                          {/* 各业务环节事件量 */}
                          <ResponsiveContainer width="100%" height={240}>
                            <BarChart data={processChartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-subtle)" />
                              <XAxis dataKey="name" tick={{ fill: 'var(--text-tertiary)', fontSize: 12 }} tickLine={false} axisLine={false} interval={0} />
                              <YAxis tick={{ fill: 'var(--text-tertiary)', fontSize: 12 }} tickLine={false} axisLine={false} unit="件" />
                              <Tooltip
                                contentStyle={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-default)', borderRadius: 8, color: 'var(--text-primary)' }}
                                labelStyle={{ color: 'var(--text-secondary)' }}
                                itemStyle={{ color: 'var(--text-primary)' }}
                                formatter={(v) => `${v} 件`}
                              />
                              <Bar dataKey="事件数" fill="#1E3A5F" radius={[4, 4, 0, 0]} maxBarSize={56} />
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                      )}
                    </CardBody>
                  </Card>
                </motion.div>

                {/* 业务效果概览 + 数据口径 */}
                <motion.div variants={staggerItem}>
                  <Card>
                    <CardHeader>
                      <h3 className="text-h4 text-text-primary flex items-center gap-2">
                        <TrendingUpIcon className="w-5 h-5 text-brand-500" aria-hidden="true" />
                        业务效果概览
                      </h3>
                      <p className="text-sm text-text-tertiary mt-1">企业经营效果与 AI 投入回报（数据来自真实运行时指标）</p>
                    </CardHeader>
                    <CardBody>
                      {orgMetrics?.business_impact ? (
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                          <div className="rounded-lg border border-border-default bg-elevated/40 p-4">
                            <div className="text-xs text-text-tertiary">业务产出（output_value）</div>
                            <div className="text-2xl font-bold text-success mt-1">¥{(orgMetrics.business_impact.output_value / 10000).toFixed(1)} 万</div>
                            <div className="text-xs text-text-tertiary mt-1">AI 助力达成的业务价值</div>
                          </div>
                          <div className="rounded-lg border border-border-default bg-elevated/40 p-4">
                            <div className="text-xs text-text-tertiary">成本节约（cost_saved）</div>
                            <div className="text-2xl font-bold text-success mt-1">¥{(orgMetrics.business_impact.cost_saved / 10000).toFixed(1)} 万</div>
                            <div className="text-xs text-text-tertiary mt-1">自动化替代人力的成本节省</div>
                          </div>
                          <div className="rounded-lg border border-border-default bg-elevated/40 p-4">
                            <div className="text-xs text-text-tertiary">替代人时（hours_replaced）</div>
                            <div className="text-2xl font-bold text-info mt-1">{orgMetrics.business_impact.hours_replaced}</div>
                            <div className="text-xs text-text-tertiary mt-1">累计节省的人工工时（小时）</div>
                          </div>
                        </div>
                      ) : (
                        <EmptyState
                          icon={TrendingUpIcon}
                          title="暂无业务效果数据"
                          description="编译企业运行时并运行协作流程后，将在此展示业务产出、成本节约与替代人时。"
                          action={{ label: '去构建企业', to: '/build' }}
                          variant="info"
                        />
                      )}
                    </CardBody>
                  </Card>
                </motion.div>
              </div>
            )}
          </motion.div>
        )}
      </div>
    </Layout>
  )
}

// ============================================================
// OperatingBrief — 将系统真实状态收敛为一个解释性强、可执行的下一步。
// ============================================================
function OperatingBrief({
  plan,
  onAction,
  running,
}: {
  plan: OperatingBriefPlan
  onAction: () => void
  running: boolean
}) {
  const Icon = plan.icon
  const toneStyles: Record<OperatingBriefPlan['tone'], { icon: string; marker: string; action: 'primary' | 'secondary' | 'outline' | 'danger' }> = {
    brand: {
      icon: 'bg-brand-500/10 text-brand-500 border-brand-500/15',
      marker: 'bg-brand-500',
      action: 'primary',
    },
    success: {
      icon: 'bg-success/10 text-success border-success/20',
      marker: 'bg-success',
      action: 'secondary',
    },
    warning: {
      icon: 'bg-warning/10 text-warning border-warning/20',
      marker: 'bg-warning',
      action: 'outline',
    },
    danger: {
      icon: 'bg-error/10 text-error border-error/20',
      marker: 'bg-error',
      action: 'danger',
    },
  }
  const style = toneStyles[plan.tone]
  const isStartAction = plan.action === 'start-demo'

  return (
    <section aria-labelledby="operating-brief-title" className="ui-card overflow-hidden rounded-2xl border border-border-default">
      <div className="relative grid gap-5 p-5 sm:p-6 lg:grid-cols-[auto_minmax(0,1fr)_auto] lg:items-center">
        <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border ${style.icon}`}>
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="ui-context-kicker">{plan.eyebrow}</span>
            <span className="inline-flex items-center gap-1.5 text-xs text-text-tertiary" aria-live="polite">
              <span className={`h-1.5 w-1.5 rounded-full ${style.marker}`} aria-hidden="true" />
              {plan.evidence}
            </span>
          </div>
          <h2 id="operating-brief-title" className="mt-1.5 text-lg font-semibold tracking-[-0.025em] text-text-primary">
            {plan.title}
          </h2>
          <p className="ui-page-description mt-1.5 max-w-3xl text-sm">{plan.description}</p>
        </div>
        <div className="lg:justify-self-end">
          <Button variant={style.action} size="md" onClick={onAction} disabled={running && isStartAction}>
            {running && isStartAction ? <Spinner size="sm" /> : <ArrowRight className="h-4 w-4" aria-hidden="true" />}
            {running && isStartAction ? '正在启动…' : plan.actionLabel}
          </Button>
        </div>
      </div>
    </section>
  )
}

// ============================================================
// BizMetricCard — 业务效果指标卡（悬浮显示推导口径）
// ============================================================
type BizTone = 'success' | 'brand'

function BizMetricCard({
  icon: Icon,
  tone,
  label,
  value,
  tooltip,
}: {
  icon: typeof Clock
  tone: BizTone
  label: string
  value: string
  tooltip?: string
}) {
  const toneIcon =
    tone === 'success'
      ? 'bg-success/10 text-success'
      : 'bg-brand-50 text-brand-500'
  return (
    <div className="group relative bg-surface border border-border-default rounded-xl shadow-soft p-4">
      <div className="flex items-center justify-between mb-3">
        <div className={`rounded-md p-2 ${toneIcon}`}>
          <Icon className="w-5 h-5" aria-hidden="true" />
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
      <div className="mt-1 font-mono tabular-nums text-2xl font-bold text-text-primary truncate">
        {value}
      </div>
    </div>
  )
}

// ============================================================
// AlertCallout — 异常与告警（#5 置于显眼位置）
//
// 有告警时呈现顶部高亮卡（error 红色 / warning 黄色），
// 无告警时呈现一个低调的健康状态条，避免抢占注意力。
// ============================================================
interface AlertItem {
  agent_name: string
  message: string
  level: 'error' | 'warning'
}

function AlertCallout({ alerts }: { alerts: AlertItem[] }) {
  if (alerts.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-success/30 bg-success/5 px-4 py-3 text-sm text-success">
        <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
        系统运行正常，暂无异常与告警
      </div>
    )
  }
  const hasError = alerts.some((a) => a.level === 'error')
  return (
    <div
      className={`rounded-lg border px-4 py-3 ${
        hasError ? 'border-error/40 bg-error/5' : 'border-warning/40 bg-warning/5'
      }`}
    >
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className={`w-4 h-4 ${hasError ? 'text-error' : 'text-warning'}`} aria-hidden="true" />
        <span className={`text-sm font-semibold ${hasError ? 'text-error' : 'text-warning'}`}>
          异常与告警（{alerts.length}）
        </span>
      </div>
      <div className="space-y-1.5">
        {alerts.map((alert, idx) => (
          <div key={idx} className="flex items-start gap-2 text-sm">
            <span
              className={`mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                alert.level === 'error' ? 'bg-error' : 'bg-warning'
              }`}
            />
            <span className="text-text-primary font-medium">{alert.agent_name}</span>
            <span className={alert.level === 'error' ? 'text-error' : 'text-warning'}>{alert.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
