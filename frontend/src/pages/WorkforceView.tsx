/**
 * WorkforceView — AI Workforce 可视化页面（重新设计版）。
 *
 * 对应 PRD §6.5 + §5.11 AI 数字员工生成逻辑 + spec.md §10.7 WT3 端点。
 *
 * 功能：
 * - 精美员工卡片网格（头像渐变 + 中文名 + 岗位中文 + 生命周期标签 + 简短描述）
 * - 点击卡片打开弹窗详情（基本信息 / 工作任务 / 最近完成 / 贡献产出 / 能力标签 / 成长记录）
 * - 岗位中文名映射（pos_xxx → 中文），隐藏所有 pos_xxx 英文标识
 * - 推荐确认流程（generate → 推荐 → 确认 → 创建）
 *
 * 错误状态：API 失败时显示告警 + 重试按钮（spec.md §2.5 约束）。
 */
import { useState, useEffect, useCallback, useMemo, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  Sparkles, Check, UserCheck, User, Briefcase, ClipboardList,
  CheckCircle2, TrendingUp, Tag, Clock, AlertCircle, Zap, MessagesSquare,
  LayoutGrid, Wrench, BookOpen, BrainCircuit, Users, Plus,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { Button } from '@/components/ui/Button'
import { PageHeader } from '@/components/ui/PageHeader'
import { Spinner } from '@/components/ui/Spinner'
import { Dialog } from '@/components/ui/Dialog'
import { Card, CardBody } from '@/components/ui/Card'
import { ApiErrorState } from '@/components/ApiErrorState'
import { MetricGrid, type Metric } from '@/components/ui/MetricGrid'
import { SectionGroup } from '@/components/ui/SectionGroup'
import { EmptyState } from '@/components/ui/EmptyState'
import { Drawer } from '@/components/ui/Drawer'
import { InlineTabs } from '@/components/ui/InlineTabs'
import * as workforceApi from '@/api/workforce'
import * as runtimeApi from '@/api/runtime'
import * as templateApi from '@/api/templates'
import { getSkills, createSkill } from '@/api/skills'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { kpiToLabel } from '@/utils/fieldMappings'
import { formatSatisfaction } from '@/utils/format'
import { LIFECYCLE_LABELS, LIFECYCLE_STYLES } from '@/utils/lifecycle'
import type {
  AgentRunMetrics,
  PositionCapability,
  AgentConfigTemplate,
  LifecycleState,
  AgentMemory,
  Skill,
} from '@/types'

// ============================================================================
// 岗位中文名映射工具
// 将后端 pos_xxx / role-xxx 英文标识转为中文岗位名，隐藏所有 pos_xxx 英文字段
// ============================================================================

/** pos_xxx 英文标识 → 中文岗位名 */
const POSITION_LABELS: Record<string, string> = {
  pos_sales: '销售经理',
  pos_sales_manager: '销售经理',
  pos_sales_rep: '销售代表',
  pos_pre_sales: '售前工程师',
  pos_presales: '售前工程师',
  pos_presales_engineer: '售前工程师',
  pos_presales_tech: '售前技术支持',
  pos_finance: '财务专员',
  pos_finance_manager: '财务经理',
  pos_customer_service: '客服专员',
  pos_cs_specialist: '客服专员',
  pos_after_sales: '售后工程师',
  pos_aftersales: '售后工程师',
  pos_aftersales_engineer: '售后工程师',
  pos_aftersales_specialist: '售后服务专员',
  pos_hr: '人事专员',
  pos_procurement: '采购专员',
  pos_marketing: '市场专员',
  pos_logistics: '物流专员',
  pos_technician: '技术员',
}

/** role-xxx 标识 → 中文岗位名（兼容后端 role_id 形态） */
const ROLE_LABELS: Record<string, string> = {
  'role-sales-rep': '销售代表',
  'role-sales-manager': '销售经理',
  'role-presales-tech': '售前技术支持',
  'role-presales-engineer': '售前工程师',
  'role-finance-manager': '财务经理',
  'role-cs-specialist': '客服专员',
  'role-aftersales-specialist': '售后服务专员',
  'role-aftersales-engineer': '售后工程师',
}

/** 中文岗位名 → 简短描述（基于岗位的工作内容） */
const POSITION_DESCRIPTIONS: Record<string, string> = {
  '销售经理': '负责客户开发、商机跟进与报价生成，对接客户需求并推动成交',
  '销售代表': '负责客户开发、商机跟进与报价生成，对接客户需求并推动成交',
  '售前工程师': '负责产品参数查询与技术咨询支持，协助销售解答技术问题',
  '售前技术支持': '负责产品参数查询与技术咨询支持，协助销售解答技术问题',
  '财务专员': '负责报价审核与费用审批管理，按金额分级校验审批流程',
  '财务经理': '负责报价审核与费用审批管理，按金额分级校验审批流程',
  '客服专员': '负责订单同步与客户档案管理，成交后发送交付通知',
  '售后工程师': '负责售后跟进与客户反馈记录，按分级标准处理售后工单',
  '售后服务专员': '负责售后跟进与客户反馈记录，按分级标准处理售后工单',
  '人事专员': '负责员工入职离职流程与系统权限管理',
  '采购专员': '负责供应商管理与采购审批流程',
  '市场专员': '负责市场推广与品牌活动策划',
}

/** 中文岗位名 → 该岗位的通用职责条目。
 *  注意：这是**岗位说明词典**，不是任何员工的真实工作记录，
 *  展示时必须如实标注为「岗位职责范围」，不得冒充履历。 */
const POSITION_DUTIES: Record<string, string[]> = {
  '销售经理': ['响应客户询盘并确认需求', '生成产品报价单', '跟进商机并更新 CRM', '提交报价审批'],
  '销售代表': ['响应客户询盘并确认需求', '生成产品报价单', '跟进商机并更新 CRM', '提交报价审批'],
  '售前工程师': ['查询产品参数并回复咨询', '提供技术方案支持', '核对产品规格书'],
  '售前技术支持': ['查询产品参数并回复咨询', '提供技术方案支持', '核对产品规格书'],
  '财务专员': ['审核报价单金额分级', '处理费用报销审批', '校验审批流程合规性'],
  '财务经理': ['审核报价单金额分级', '处理费用报销审批', '校验审批流程合规性'],
  '客服专员': ['同步成交订单信息', '创建客户档案', '发送交付通知'],
  '售后工程师': ['跟进售后工单处理', '记录客户反馈', '按分级标准处理问题'],
  '售后服务专员': ['跟进售后工单处理', '记录客户反馈', '按分级标准处理问题'],
}

// ----------------------------------------------------------------------------
// 部门归类：将具体岗位映射到 ≤10 个部门，参考企业组织架构图。
// 用于卡片分组展示，避免几十个重复角色铺满屏幕。
// ----------------------------------------------------------------------------

/** 部门定义（顺序即展示顺序，参考企业组织架构自上而下） */
const DEPARTMENT_ORDER = [
  '管理层',
  '销售部',
  '产品部',
  '研发部',
  '技术支持部',
  '客服部',
  '售后服务部',
  '财务部',
  '人事行政部',
  '生产质检部',
  '知识助理',
] as const

/** 部门 → 简短职责描述（用于卡片副标题，区分各部门） */
const DEPARTMENT_DESCRIPTIONS: Record<string, string> = {
  '管理层': '负责企业战略决策、经营统筹与跨部门协调',
  '销售部': '负责客户开发、商机跟进、报价生成与售前支持',
  '产品部': '负责产品规划、需求管理与版本迭代',
  '研发部': '负责软硬件研发、技术攻关与工程实现',
  '技术支持部': '负责技术咨询、故障排查与设备维护',
  '客服部': '负责客户咨询响应、订单同步与档案管理',
  '售后服务部': '负责售后工单处理、客户反馈与问题分级处置',
  '财务部': '负责报价审核、费用审批与会计核算',
  '人事行政部': '负责员工入离调转、行政事务与系统权限管理',
  '生产质检部': '负责生产管理、工艺执行与质量检验',
  '知识助理': '基于企业知识库提供专项问答与文档摘要支持',
}

/** 关键词 → 部门（按优先级匹配，首个命中即归属） */
const DEPARTMENT_KEYWORDS: { dept: string; keywords: string[] }[] = [
  { dept: '管理层', keywords: ['CEO', '总经理', '法定代表人', '总监', '主管', '经理', '部门经理', '部门总监', '会议总结', '经营'] },
  { dept: '销售部', keywords: ['销售', '售前', '商机', '客户开发'] },
  { dept: '产品部', keywords: ['产品'] },
  { dept: '研发部', keywords: ['研发', '软件工程师', '硬件工程师'] },
  { dept: '技术支持部', keywords: ['技术支持', '技术问答', '维修', '工程师'] },
  { dept: '客服部', keywords: ['客服'] },
  { dept: '售后服务部', keywords: ['售后'] },
  { dept: '财务部', keywords: ['财务', '会计', '审计', '出纳'] },
  { dept: '人事行政部', keywords: ['人事', '行政', 'HR', '招聘'] },
  { dept: '生产质检部', keywords: ['生产', '质检', '工艺', '设备', '安全'] },
]

/**
 * 根据角色名/岗位名推断所属部门。
 * 依次按关键词匹配；未命中则归入"知识助理"。
 */
function inferDepartment(roleName: string): string {
  if (!roleName) return '知识助理'
  for (const { dept, keywords } of DEPARTMENT_KEYWORDS) {
    if (keywords.some((k) => roleName.includes(k))) return dept
  }
  return '知识助理'
}

/** 头像纯色背景色组（对标 prototype：圆角方形 + 纯色，非渐变） */
const AVATAR_SOLID_COLORS: string[] = [
  'bg-brand-500',
  'bg-info',
  'bg-success',
  'bg-warning',
  'bg-error',
  'bg-brand-400',
  'bg-info/80',
  'bg-success/80',
]

/**
 * 从 agent_name 中提取角色名（去除"（AI）"/"(AI)"/"- AI"等后缀）。
 * 例："客服专员（AI）" → "客服专员"，"CEO / 总经理（AI）" → "CEO / 总经理"
 */
function getRoleFromAgentName(agentName: string | undefined | null): string {
  if (!agentName) return ''
  return agentName
    .replace(/（AI）/g, '')
    .replace(/\(AI\)/g, '')
    .replace(/\s*[-—]\s*AI\s*$/g, '')
    .trim()
}

/**
 * 将后端岗位标识（pos_xxx / role-xxx / role_xxx / UUID / 英文）转为中文岗位名。
 * 优先查映射表；未命中时从 agent_name 派生角色名；仍无则回退"数字员工"。
 * agentName 参数用于 role_<hex> 这类无法直接映射的标识。
 */
function getPositionLabel(position: string | undefined | null, agentName?: string): string {
  if (!position) {
    // position 为空时从 agent_name 派生
    const fromName = getRoleFromAgentName(agentName)
    return fromName || '数字员工'
  }
  // pos_xxx 形态
  if (position.startsWith('pos_')) {
    return POSITION_LABELS[position] || getRoleFromAgentName(agentName) || '数字员工'
  }
  // role-xxx 或 role_xxx 形态（兼容后端两种分隔符）
  if (position.startsWith('role-') || position.startsWith('role_')) {
    return ROLE_LABELS[position] || getRoleFromAgentName(agentName) || '数字员工'
  }
  // UUID 形态 → 从 agent_name 派生
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(position)) {
    return getRoleFromAgentName(agentName) || '数字员工'
  }
  // 已是中文，直接返回
  if (/[\u4e00-\u9fa5]/.test(position)) return position
  // 其他未识别的英文/标识 → 从 agent_name 派生，最后回退"数字员工"
  return getRoleFromAgentName(agentName) || '数字员工'
}

/**
 * 根据岗位 + agent_name 推断所属部门（用于卡片分组）。
 */
function getDepartment(position: string | undefined | null, agentName?: string): string {
  const roleOrName = getPositionLabel(position, agentName)
  return inferDepartment(roleOrName)
}

/** 根据岗位 + agent_name 返回简短工作描述（部门级，避免雷同） */
function getPositionDescription(position: string | undefined | null, agentName?: string): string {
  const label = getPositionLabel(position, agentName)
  // 优先用具体岗位描述
  if (POSITION_DESCRIPTIONS[label]) return POSITION_DESCRIPTIONS[label]
  // 回退到部门级描述
  const dept = inferDepartment(label)
  return DEPARTMENT_DESCRIPTIONS[dept] || '负责相关业务流程的自动化处理与协作'
}

/** 根据岗位 + agent_name 返回该岗位的通用职责条目（非真实履历） */
function getPositionDuties(position: string | undefined | null, agentName?: string): string[] {
  const label = getPositionLabel(position, agentName)
  return POSITION_DUTIES[label] || ['完成业务流程任务', '处理协作请求', '更新业务记录']
}

/** 根据 agent_id 哈希选择稳定的头像纯色（对标 prototype） */
function getAvatarColor(agentId: string): string {
  let hash = 0
  for (let i = 0; i < agentId.length; i++) {
    hash = agentId.charCodeAt(i) + ((hash << 5) - hash)
  }
  return AVATAR_SOLID_COLORS[Math.abs(hash) % AVATAR_SOLID_COLORS.length]
}

/** 取员工名称首字符作为头像文字（去除 (AI) 等后缀） */
function getInitial(name: string): string {
  if (!name) return '?'
  const trimmed = name.replace(/[（(].*$/, '').trim()
  return trimmed.charAt(0) || '?'
}

// ============================================================================
// 记忆可视化辅助函数（三层记忆：短期上下文 / 长期经验 / 实体偏好）
// 记忆条目为 Record<string, unknown>，此处安全读取，绝不造假数据。
// ============================================================================

/** 从记忆条目中安全读取字符串字段 */
function memoryField(entry: Record<string, unknown>, key: string): string {
  const v = entry[key]
  return typeof v === 'string' ? v : ''
}

/** 实体类型英文 → 中文 */
function entityTypeLabel(type: string): string {
  const map: Record<string, string> = {
    customer: '客户',
    opportunity: '商机',
    product: '产品',
    contact: '联系人',
  }
  return map[type] || type || '实体'
}

/** 渲染实体偏好（key-value 徽章列表） */
function renderEntityPreferences(item: Record<string, unknown>): ReactNode {
  const prefs = item.preferences
  if (!prefs || typeof prefs !== 'object' || Array.isArray(prefs)) {
    return <p className="text-xs text-text-tertiary">暂无偏好信息</p>
  }
  const entries = Object.entries(prefs as Record<string, unknown>)
  if (entries.length === 0) {
    return <p className="text-xs text-text-tertiary">暂无偏好信息</p>
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-brand-50 text-brand-500"
        >
          {key}: {typeof value === 'object' ? JSON.stringify(value) : String(value)}
        </span>
      ))}
    </div>
  )
}

// ============================================================================
// 子组件：员工卡片
// ============================================================================

interface EmployeeCardProps {
  agent: AgentRunMetrics
  config?: AgentConfigTemplate | null
  index: number
  /** 团队规模（同部门合并后的员工数，>1 时显示徽章） */
  teamSize?: number
  /** 卡片分类标签（部门名），覆盖岗位名作为副标题展示 */
  categoryLabel?: string
  onClick: () => void
}

function EmployeeCard({ agent, config, index, teamSize, categoryLabel, onClick }: EmployeeCardProps) {
  const navigate = useNavigate()
  const positionLabel = categoryLabel || getPositionLabel(agent.position, agent.agent_name)
  const description = categoryLabel
    ? (DEPARTMENT_DESCRIPTIONS[categoryLabel] || getPositionDescription(agent.position, agent.agent_name))
    : getPositionDescription(agent.position, agent.agent_name)
  const avatarColor = getAvatarColor(agent.agent_id)
  // 生产中或评估中的员工可发起协作
  const canCollaborate = agent.lifecycle_stage === 'production' || agent.lifecycle_stage === 'evaluation' || agent.lifecycle_stage === 'continuous_learning'

  return (
    <motion.div
      className="h-full"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.05 }}
    >
      <Card hover className={`cursor-pointer overflow-hidden h-full ${agent.lifecycle_stage === 'production' ? '!border-brand-100 shadow-soft' : ''}`} onClick={onClick}>
        <CardBody className="space-y-3">
          {/* 头部：头像 + 中文名 + 阶段标签 */}
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-3 min-w-0">
              <div
                className={`w-12 h-12 rounded-lg ${avatarColor} flex items-center justify-center text-white text-lg font-semibold flex-shrink-0`}
                aria-hidden="true"
              >
                {getInitial(agent.agent_name)}
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <h4 className="text-base font-semibold text-text-primary truncate">
                    {agent.agent_name}
                  </h4>
                  {teamSize && teamSize > 1 && (
                    <span className="flex-shrink-0 inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-brand-50 text-brand-500 text-xs font-semibold" title={`该部门合并了 ${teamSize} 名同岗员工`}>
                      ×{teamSize}
                    </span>
                  )}
                </div>
                <p className="text-sm text-text-secondary truncate">{positionLabel}</p>
              </div>
            </div>
            <span
              className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0 ${LIFECYCLE_STYLES[agent.lifecycle_stage]}`}
            >
              {LIFECYCLE_LABELS[agent.lifecycle_stage]}
            </span>
          </div>

          {/* 简短描述 */}
          <p className="text-sm text-text-tertiary line-clamp-2 min-h-[2.5rem]">
            {description}
          </p>

          {/* 关键指标 */}
          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-lg bg-elevated/50 py-2 px-1 text-center">
              <div className="text-xs text-text-tertiary">总任务</div>
              <div className="text-base font-semibold text-text-primary">{agent.tasks_total}</div>
            </div>
            <div className="rounded-lg bg-elevated/50 py-2 px-1 text-center">
              <div className="text-xs text-text-tertiary">已完成</div>
              <div className="text-base font-semibold text-success">{agent.tasks_completed}</div>
            </div>
            <div className="rounded-lg bg-elevated/50 py-2 px-1 text-center">
              <div className="text-xs text-text-tertiary">满意度</div>
              <div className="text-base font-semibold text-text-primary">
                {/* 后端为 0-5 分制，此前误当 0-1 比例乘 100，4.6 分会显示成 460% */}
                {formatSatisfaction(agent.avg_satisfaction)}
              </div>
            </div>
          </div>

          {/* 部门职级 */}
          {config && (
            <div className="flex items-center gap-1.5 text-xs text-text-tertiary pt-2 border-t border-border-subtle">
              <Briefcase className="w-3.5 h-3.5 flex-shrink-0" aria-hidden="true" />
              <span className="truncate">{config.department}</span>
              <span>·</span>
              <span className="truncate">{config.level}</span>
            </div>
          )}

          {/* 发起协作按钮 */}
          {canCollaborate && (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={(e) => {
                e.stopPropagation()
                navigate(`/chat/${agent.agent_id}`)
              }}
            >
              <MessagesSquare className="w-4 h-4" aria-hidden="true" />
              发起协作
            </Button>
          )}
        </CardBody>
      </Card>
    </motion.div>
  )
}

// ============================================================================
// 子组件：详情抽屉内容（InlineTabs 分页，替代超长滚动 Dialog）
// ============================================================================

/** 详情抽屉内容 — 4 个 Tab：概览 / 工作任务 / 能力配置 / 成长记录 */
function EmployeeDetailContent({
  agent,
  config,
  lifecycle,
}: {
  agent: AgentRunMetrics
  config?: AgentConfigTemplate | null
  lifecycle?: LifecycleState | null
}) {
  const [tab, setTab] = useState<'overview' | 'tasks' | 'capability' | 'growth' | 'memory'>('overview')
  const positionLabel = getPositionLabel(agent.position, agent.agent_name)
  const description = getPositionDescription(agent.position, agent.agent_name)
  const avatarColor = getAvatarColor(agent.agent_id)
  const completionRate = agent.tasks_total > 0
    ? (agent.tasks_completed / agent.tasks_total) * 100
    : 0
  const inProgress = Math.max(0, agent.tasks_total - agent.tasks_completed - agent.tasks_failed)
  const kpiValues = Object.values(agent.kpi_performance)
  const kpiAvg = kpiValues.length > 0
    ? kpiValues.reduce((a, b) => a + b, 0) / kpiValues.length
    : 0
  const totalToolUsage = Object.values(agent.tool_usage).reduce((a, b) => a + b, 0)
  const workSamples = getPositionDuties(agent.position, agent.agent_name)
  const hasLifecycle = !!(lifecycle && lifecycle.history.length > 0)

  // 三层记忆：懒加载（占位会话 id 'default'），失败优雅降级为空态，不阻塞详情
  const [memory, setMemory] = useState<AgentMemory | null>(null)
  const [memoryLoading, setMemoryLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setMemoryLoading(true)
    setMemory(null)
    Promise.allSettled([workforceApi.getAgentMemory(agent.agent_id, 'default')]).then(
      ([result]) => {
        if (cancelled) return
        if (result.status === 'fulfilled') setMemory(result.value)
        setMemoryLoading(false)
      },
    )
    return () => {
      cancelled = true
    }
  }, [agent.agent_id])

  // ---------- 能力配置：加载该员工已绑定的技能 ----------
  const [agentSkills, setAgentSkills] = useState<Skill[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)

  const loadAgentSkills = useCallback(async () => {
    setSkillsLoading(true)
    try {
      const list = await getSkills(agent.agent_id)
      setAgentSkills(list ?? [])
    } catch {
      setAgentSkills([])
    } finally {
      setSkillsLoading(false)
    }
  }, [agent.agent_id])

  useEffect(() => {
    loadAgentSkills()
  }, [loadAgentSkills])

  // ---------- 添加技能（系统内置 + 已导入） ----------
  interface SkillOption {
    key: string
    name: string
    description: string
    skill_type: string
    source: 'builtin' | 'imported'
    config?: Record<string, unknown>
    template?: templateApi.SkillTemplate
    existing?: Skill
  }
  const [addSkillOpen, setAddSkillOpen] = useState(false)
  const [skillOptions, setSkillOptions] = useState<SkillOption[]>([])
  const [selectedAdd, setSelectedAdd] = useState<Set<string>>(new Set())
  const [addingSkill, setAddingSkill] = useState(false)

  const openAddSkill = useCallback(async () => {
    setAddSkillOpen(true)
    setSelectedAdd(new Set())
    // 并行加载：系统内置技能模板 + 企业已导入技能
    const [builtins, all] = await Promise.allSettled([
      templateApi.getSkillTemplates(),
      getSkills(),
    ])
    const options: SkillOption[] = []
    if (builtins.status === 'fulfilled') {
      builtins.value.forEach((st) => {
        options.push({
          key: `builtin:${st.code}`,
          name: st.name,
          description: st.description || '',
          skill_type: st.skill_type,
          source: 'builtin',
          config: st.config || {},
          template: st,
        })
      })
    }
    if (all.status === 'fulfilled') {
      const existingIds = new Set(agentSkills.map((s) => s.name))
      all.value
        .filter((s) => s.source === 'imported')
        .forEach((s) => {
          if (existingIds.has(s.name)) return // 已绑定，跳过
          options.push({
            key: `imported:${s.id}`,
            name: s.name,
            description: s.description || '',
            skill_type: s.skill_type,
            source: 'imported',
            config: s.config || {},
            existing: s,
          })
        })
    }
    // 去重（同名保留首个）
    const seen = new Set<string>()
    setSkillOptions(options.filter((o) => {
      if (seen.has(o.name)) return false
      seen.add(o.name)
      return true
    }))
  }, [agentSkills])

  const toggleSkillOption = (key: string) => {
    setSelectedAdd((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const confirmAddSkills = async () => {
    const chosen = skillOptions.filter((o) => selectedAdd.has(o.key))
    if (!chosen.length) return
    setAddingSkill(true)
    try {
      for (const opt of chosen) {

        await createSkill({
          agent_id: agent.agent_id,
          name: opt.name,
          description: opt.description,
          skill_type: opt.skill_type,
          config: opt.config,
        })
      }
      await loadAgentSkills()
      setAddSkillOpen(false)
    } catch (err) {
      console.error('添加技能失败:', err)
    } finally {
      setAddingSkill(false)
    }
  }

  return (
    <div className="space-y-5">
      {/* 头部：头像 + 名称 + 阶段 */}
      <div className="flex items-center gap-4">
        <div
          className={`w-16 h-16 rounded-xl ${avatarColor} flex items-center justify-center text-white text-2xl font-semibold flex-shrink-0`}
          aria-hidden="true"
        >
          {getInitial(agent.agent_name)}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-semibold text-text-primary">{agent.agent_name}</h3>
          <div className="flex flex-wrap items-center gap-2 mt-1">
            <span className="text-sm text-text-secondary">{positionLabel}</span>
            <span
              className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${LIFECYCLE_STYLES[agent.lifecycle_stage]}`}
            >
              {LIFECYCLE_LABELS[agent.lifecycle_stage]}
            </span>
          </div>
        </div>
      </div>

      {/* Tab 切换 */}
      <InlineTabs
        tabs={[
          { key: 'overview', label: '概览', icon: User },
          { key: 'tasks', label: '工作任务', icon: ClipboardList },
          { key: 'capability', label: '能力配置', icon: Tag },
          { key: 'growth', label: '成长记录', icon: Clock, disabled: !hasLifecycle },
          { key: 'memory', label: '记忆', icon: BrainCircuit },
        ]}
        activeKey={tab}
        onChange={(k) => setTab(k as 'overview' | 'tasks' | 'capability' | 'growth' | 'memory')}
      />

      {/* 概览 Tab */}
      {tab === 'overview' && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
          className="space-y-5"
        >
          <p className="text-sm text-text-tertiary leading-relaxed">{description}</p>

          {/* 基本信息 */}
          <div>
            <h4 className="text-sm font-medium text-text-primary flex items-center gap-2 mb-2">
              <User className="w-4 h-4 text-brand-500" aria-hidden="true" />
              基本信息
            </h4>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <dt className="text-xs text-text-tertiary">岗位</dt>
                <dd className="text-text-secondary">{positionLabel}</dd>
              </div>
              {config && (
                <div>
                  <dt className="text-xs text-text-tertiary">部门</dt>
                  <dd className="text-text-secondary">{config.department}</dd>
                </div>
              )}
              {config && (
                <div>
                  <dt className="text-xs text-text-tertiary">职级</dt>
                  <dd className="text-text-secondary">{config.level}</dd>
                </div>
              )}
              <div>
                <dt className="text-xs text-text-tertiary">最近活跃</dt>
                <dd className="text-text-secondary text-xs">
                  {new Date(agent.last_active_at).toLocaleString('zh-CN')}
                </dd>
              </div>
            </dl>
          </div>

          {/* 岗位职责范围
              原为「最近完成的工作」+ ✓ 勾选样式，内容却是按岗位名查
              POSITION_WORK_SAMPLES 表得到的固定文案 —— 把通用岗位说明
              冒充成该员工的真实履历（UI v4 §一 罪一点名问题）。
              现改为如实标题「岗位职责范围」并去掉 ✓ 语义，真实完成量
              由下方 tasks_completed 单独给出。 */}
          <div>
            <h4 className="text-sm font-medium text-text-primary flex items-center gap-2 mb-2">
              <ClipboardList className="w-4 h-4 text-brand-500" aria-hidden="true" />
              岗位职责范围
            </h4>
            <ul className="space-y-1.5 text-sm">
              {workSamples.map((item, idx) => (
                <li key={idx} className="flex items-start gap-2 text-text-secondary">
                  <span
                    className="w-1 h-1 rounded-full bg-text-tertiary flex-shrink-0 mt-2"
                    aria-hidden="true"
                  />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-text-tertiary">
              以上为该岗位的通用职责说明。
              {agent.tasks_completed > 0
                ? `该员工实际累计完成 ${agent.tasks_completed} 项任务，完成率 ${completionRate.toFixed(0)}%。`
                : '该员工尚无已完成任务记录。'}
            </p>
          </div>
        </motion.div>
      )}

      {/* 工作任务 Tab */}
      {tab === 'tasks' && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
          className="space-y-5"
        >
          <div>
            <h4 className="text-sm font-medium text-text-primary flex items-center gap-2 mb-3">
              <ClipboardList className="w-4 h-4 text-brand-500" aria-hidden="true" />
              任务概况
            </h4>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div className="rounded-lg bg-elevated/50 p-2.5">
                <div className="text-xs text-text-tertiary">总任务数</div>
                <div className="text-lg font-semibold text-text-primary">{agent.tasks_total}</div>
              </div>
              <div className="rounded-lg bg-elevated/50 p-2.5">
                <div className="text-xs text-text-tertiary">进行中</div>
                <div className="text-lg font-semibold text-warning">{inProgress}</div>
              </div>
              <div className="rounded-lg bg-elevated/50 p-2.5">
                <div className="text-xs text-text-tertiary">已完成</div>
                <div className="text-lg font-semibold text-success">{agent.tasks_completed}</div>
              </div>
              <div className="rounded-lg bg-elevated/50 p-2.5">
                <div className="text-xs text-text-tertiary">失败</div>
                <div className="text-lg font-semibold text-error">{agent.tasks_failed}</div>
              </div>
            </div>
            {agent.tasks_failed > 0 && (
              <div className="mt-2 flex items-center gap-1 text-xs text-error">
                <AlertCircle className="w-3.5 h-3.5" aria-hidden="true" />
                存在 {agent.tasks_failed} 个失败任务，需关注
              </div>
            )}
          </div>

          {/* 贡献和产出 */}
          <div>
            <h4 className="text-sm font-medium text-text-primary flex items-center gap-2 mb-3">
              <TrendingUp className="w-4 h-4 text-brand-500" aria-hidden="true" />
              贡献和产出
            </h4>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div className="rounded-lg bg-elevated/50 p-2.5">
                <div className="text-xs text-text-tertiary">任务完成率</div>
                <div className="text-lg font-semibold text-success">{completionRate.toFixed(0)}%</div>
              </div>
              <div className="rounded-lg bg-elevated/50 p-2.5">
                <div className="text-xs text-text-tertiary">KPI 达成率</div>
                <div className="text-lg font-semibold text-text-primary">
                  {kpiValues.length > 0 ? `${(kpiAvg * 100).toFixed(0)}%` : '-'}
                </div>
              </div>
              <div className="rounded-lg bg-elevated/50 p-2.5">
                <div className="text-xs text-text-tertiary">平均响应时长</div>
                <div className="text-lg font-semibold text-text-primary">
                  {(agent.avg_response_time_ms / 1000).toFixed(1)}s
                </div>
              </div>
              <div className="rounded-lg bg-elevated/50 p-2.5">
                <div className="text-xs text-text-tertiary">工具调用次数</div>
                <div className="text-lg font-semibold text-text-primary">{totalToolUsage}</div>
              </div>
            </div>
            {kpiValues.length > 0 && (
              <div className="mt-3 space-y-1.5">
                {Object.entries(agent.kpi_performance).map(([kpiId, value]) => (
                  <div key={kpiId} className="flex items-center justify-between text-xs gap-2">
                    <span className="text-text-tertiary truncate">{kpiToLabel(kpiId)}</span>
                    <div className="flex items-center gap-2 flex-1 max-w-[60%]">
                      <div className="flex-1 h-1.5 rounded-full bg-elevated overflow-hidden">
                        <div
                          className="h-full bg-brand-500 rounded-full"
                          style={{ width: `${Math.min(100, value * 100)}%` }}
                        />
                      </div>
                      <span className="text-text-secondary w-10 text-right">
                        {(value * 100).toFixed(0)}%
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </motion.div>
      )}

      {/* 能力配置 Tab：查看/添加技能（系统内置 + 已导入） */}
      {tab === 'capability' && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
          className="space-y-5"
        >
          {/* 添加技能入口 */}
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium text-text-primary flex items-center gap-2">
              <Tag className="w-4 h-4 text-brand-500" aria-hidden="true" />
              技能
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={openAddSkill}
              disabled={skillsLoading}
            >
              <Plus className="w-4 h-4" />
              添加技能
            </Button>
          </div>

          {/* 已绑定技能 */}
          {skillsLoading ? (
            <div className="flex justify-center py-6">
              <Spinner size="sm" />
            </div>
          ) : agentSkills.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {agentSkills.map((s) => (
                <span
                  key={s.id}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-success/10 text-success"
                >
                  <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
                  {s.name}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-sm text-text-tertiary">该员工暂无绑定技能，点击「添加技能」从系统内置或已导入技能中选择</p>
          )}

          {/* 运行时配置技能（来自 Agent 运行模板） */}
          {config && config.skills.length > 0 && (
            <div>
              <div className="text-xs text-text-tertiary mb-2 flex items-center gap-1.5">
                <BookOpen className="w-3.5 h-3.5 text-info" aria-hidden="true" />
                运行时技能（模板绑定）
              </div>
              <div className="flex flex-wrap gap-1.5">
                {config.skills.map((s) => (
                  <span
                    key={s.skill_id}
                    className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-info/10 text-info"
                  >
                    {s.name}
                    {s.enabled ? '' : '（禁用）'}
                  </span>
                ))}
              </div>
            </div>
          )}

          {config && config.tools.length > 0 && (
            <div>
              <div className="text-xs text-text-tertiary mb-2 flex items-center gap-1.5">
                <Wrench className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
                工具 / MCP
              </div>
              <div className="flex flex-wrap gap-1.5">
                {config.tools.map((t) => (
                  <span
                    key={t.tool_id}
                    className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-brand-50 text-brand-500"
                  >
                    <Zap className="w-3 h-3 mr-0.5" aria-hidden="true" />
                    {t.name}
                  </span>
                ))}
              </div>
            </div>
          )}
          {config && config.knowledge_bases.length > 0 && (
            <div>
              <div className="text-xs text-text-tertiary mb-2 flex items-center gap-1.5">
                <BookOpen className="w-3.5 h-3.5 text-info" aria-hidden="true" />
                知识库
              </div>
              <div className="flex flex-wrap gap-1.5">
                {config.knowledge_bases.map((kb) => (
                  <span
                    key={kb}
                    className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-info/10 text-info"
                  >
                    {kb}
                  </span>
                ))}
              </div>
            </div>
          )}
          {!config && (
            <p className="text-xs text-text-tertiary">
              该员工暂无运行模板信息，可先通过「添加技能」为员工配置能力。
            </p>
          )}
        </motion.div>
      )}

      {/* 添加技能对话框 */}
      <Dialog
        open={addSkillOpen}
        title="添加技能"
        description="从系统内置技能或已导入技能中选择，绑定到该员工"
        confirmText={addingSkill ? '添加中...' : '添加'}
        cancelText="取消"
        onConfirm={confirmAddSkills}
        onCancel={() => setAddSkillOpen(false)}
      >
        <div className="max-h-72 overflow-y-auto space-y-2 mt-2">
          {skillOptions.length === 0 && (
            <p className="text-sm text-text-tertiary text-center py-6">没有可添加的技能</p>
          )}
          {skillOptions.map((opt) => {
            const selected = selectedAdd.has(opt.key)
            return (
              <button
                key={opt.key}
                type="button"
                onClick={() => toggleSkillOption(opt.key)}
                className={`w-full flex items-center gap-3 p-2.5 rounded-lg border text-left transition-colors ${
                  selected
                    ? 'border-brand-300 bg-brand-50/30'
                    : 'border-border-default hover:bg-elevated/50'
                }`}
              >
                <div
                  className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 ${
                    selected ? 'bg-brand-500' : 'bg-elevated border border-border-default'
                  }`}
                >
                  {selected && <Check className="w-3.5 h-3.5 text-white" aria-hidden="true" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-primary">{opt.name}</span>
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs ${
                      opt.source === 'builtin'
                        ? 'bg-brand-50 text-brand-500'
                        : 'bg-success/10 text-success'
                    }`}>
                      {opt.source === 'builtin' ? '系统内置' : '已导入'}
                    </span>
                  </div>
                  {opt.description && (
                    <p className="text-xs text-text-tertiary mt-0.5 line-clamp-1">{opt.description}</p>
                  )}
                </div>
              </button>
            )
          })}
        </div>
      </Dialog>

      {/* 成长记录 Tab */}
      {tab === 'growth' && lifecycle && lifecycle.history.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
        >
          <div className="space-y-2">
            {lifecycle.history.map((entry, idx) => (
              <div key={idx} className="flex items-start gap-2 text-sm">
                <div className="flex flex-col items-center flex-shrink-0">
                  <div className="w-2 h-2 rounded-full bg-brand-500 mt-1.5" />
                  {idx < lifecycle.history.length - 1 && (
                    <div className="w-0.5 h-4 bg-border-default" />
                  )}
                </div>
                <div>
                  <span className="text-text-primary font-medium">
                    {LIFECYCLE_LABELS[entry.stage]}
                  </span>
                  <span className="text-xs text-text-tertiary ml-2">
                    {new Date(entry.stage_entered_at).toLocaleDateString('zh-CN')}
                  </span>
                  {entry.transition_reason && (
                    <p className="text-xs text-text-tertiary mt-0.5">{entry.transition_reason}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </motion.div>
      )}

      {/* 记忆 Tab：三层记忆可视化（短期上下文 / 长期经验 / 实体偏好） */}
      {tab === 'memory' && (
        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
          className="space-y-5"
        >
          {memoryLoading ? (
            <div className="flex items-center justify-center py-12">
              <Spinner size="lg" />
            </div>
          ) : memory && (memory.short_term.length > 0 || memory.long_term.length > 0 || memory.entity.length > 0) ? (
            <>
              {/* 短期对话上下文 */}
              <div>
                <h4 className="text-sm font-medium text-text-primary flex items-center gap-2 mb-2">
                  <MessagesSquare className="w-4 h-4 text-info" aria-hidden="true" />
                  短期对话上下文
                  <span className="text-xs text-text-tertiary font-normal">({memory.short_term.length})</span>
                </h4>
                {memory.short_term.length > 0 ? (
                  <div className="space-y-2">
                    {memory.short_term.map((item, idx) => (
                      <div key={idx} className="rounded-lg bg-elevated/50 p-3">
                        <div className="flex items-center justify-between gap-2 mb-1">
                          <span className="text-xs font-medium text-text-secondary">
                            {item.role === 'assistant'
                              ? 'AI 回复'
                              : item.role === 'user'
                                ? '用户'
                                : memoryField(item, 'role') || '对话'}
                          </span>
                          {memoryField(item, 'timestamp') && (
                            <span className="text-xs text-text-tertiary">
                              {new Date(memoryField(item, 'timestamp')).toLocaleString('zh-CN')}
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-text-secondary">
                          {memoryField(item, 'content') || JSON.stringify(item)}
                        </p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-text-tertiary">暂无短期对话上下文</p>
                )}
              </div>

              {/* 长期经验摘要 */}
              <div>
                <h4 className="text-sm font-medium text-text-primary flex items-center gap-2 mb-2">
                  <BookOpen className="w-4 h-4 text-brand-500" aria-hidden="true" />
                  长期经验摘要
                  <span className="text-xs text-text-tertiary font-normal">({memory.long_term.length})</span>
                </h4>
                {memory.long_term.length > 0 ? (
                  <div className="space-y-2">
                    {memory.long_term.map((item, idx) => (
                      <div key={idx} className="rounded-lg bg-elevated/50 p-3">
                        <p className="text-sm text-text-secondary">
                          {memoryField(item, 'summary') || JSON.stringify(item)}
                        </p>
                        {memoryField(item, 'timestamp') && (
                          <p className="text-xs text-text-tertiary mt-1">
                            {new Date(memoryField(item, 'timestamp')).toLocaleDateString('zh-CN')}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-text-tertiary">暂无长期经验摘要</p>
                )}
              </div>

              {/* 实体偏好 */}
              <div>
                <h4 className="text-sm font-medium text-text-primary flex items-center gap-2 mb-2">
                  <Users className="w-4 h-4 text-success" aria-hidden="true" />
                  实体偏好
                  <span className="text-xs text-text-tertiary font-normal">({memory.entity.length})</span>
                </h4>
                {memory.entity.length > 0 ? (
                  <div className="space-y-2">
                    {memory.entity.map((item, idx) => (
                      <div key={idx} className="rounded-lg bg-elevated/50 p-3">
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="text-sm font-medium text-text-primary">
                            {memoryField(item, 'entity_id')}
                          </span>
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-success/10 text-success">
                            {entityTypeLabel(memoryField(item, 'entity_type'))}
                          </span>
                        </div>
                        {renderEntityPreferences(item)}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-text-tertiary">暂无实体偏好</p>
                )}
              </div>
            </>
          ) : (
            <Card>
              <CardBody>
                <EmptyState
                  icon={BrainCircuit}
                  title="暂无记忆数据"
                  description="该 AI 员工暂未产生记忆数据，或记忆接口暂不可用。"
                  variant="brand"
                />
              </CardBody>
            </Card>
          )}
        </motion.div>
      )}
    </div>
  )
}

// ============================================================================
// 主页面组件
// ============================================================================

export default function WorkforceView() {
  const enterpriseId = useEnterpriseId()
  const [agents, setAgents] = useState<AgentRunMetrics[]>([])
  const [configs, setConfigs] = useState<Record<string, AgentConfigTemplate>>({})
  const [recommendations, setRecommendations] = useState<PositionCapability[]>([])
  const [loading, setLoading] = useState(false)
  const [genLoading, setGenLoading] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showRecommend, setShowRecommend] = useState(false)
  const [selectedPositions, setSelectedPositions] = useState<Set<string>>(new Set())
  const [lifecycleMap, setLifecycleMap] = useState<Record<string, LifecycleState>>({})
  // 详情弹窗状态
  const [detailAgentId, setDetailAgentId] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    if (!enterpriseId) return
    setLoading(true)
    setError(null)
    try {
      const [wf, templates] = await Promise.all([
        workforceApi.listWorkforce(enterpriseId),
        runtimeApi.getAgentTemplates(enterpriseId),
      ])
      setAgents(wf.items)
      const configMap: Record<string, AgentConfigTemplate> = {}
      templates.agents.forEach((t) => {
        configMap[t.agent_id] = t
      })
      setConfigs(configMap)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载 Workforce 数据失败')
    } finally {
      setLoading(false)
    }
  }, [enterpriseId])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleGenerate = async () => {
    if (!enterpriseId) return
    setGenLoading(true)
    setError(null)
    try {
      const result = await workforceApi.generateWorkforce({ enterprise_id: enterpriseId })
      setRecommendations(result.recommendations)
      setSelectedPositions(new Set(result.recommendations.map((r) => r.position_id)))
      setShowRecommend(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成推荐失败')
    } finally {
      setGenLoading(false)
    }
  }

  const handleConfirm = async () => {
    if (!enterpriseId) return
    setConfirmLoading(true)
    try {
      await workforceApi.confirmWorkforce({
        enterprise_id: enterpriseId,
        confirmed_position_ids: Array.from(selectedPositions),
      })
      setShowRecommend(false)
      await loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : '确认创建失败')
    } finally {
      setConfirmLoading(false)
    }
  }

  const handleOpenDetail = async (agentId: string) => {
    setDetailAgentId(agentId)
    // 懒加载生命周期数据，失败不阻塞详情展示
    if (!lifecycleMap[agentId]) {
      try {
        // 原先在 VITE_USE_MOCK 模式下回退 mockLifecycleState，
        // 属于条件性假数据兜底（UI v4 §一 罪一），已删除。
        // 加载失败时详情面板会显示诚实空态，不再用假数据掩盖。
        const state = await workforceApi.getLifecycle(agentId)
        setLifecycleMap((prev) => ({ ...prev, [agentId]: state }))
      } catch {
        // 生命周期加载失败不阻塞详情展示
      }
    }
  }

  const detailAgent = detailAgentId
    ? agents.find((a) => a.agent_id === detailAgentId) || null
    : null
  const detailConfig = detailAgentId ? configs[detailAgentId] : null

  // 按部门排序展示全部 AI 员工（后端已确保每岗位仅 1 个 Agent，无需前端合并）
  const sortedAgents = useMemo(() => {
    return agents
      .slice()
      .sort((a, b) => {
        const aDept = getDepartment(a.position, a.agent_name)
        const bDept = getDepartment(b.position, b.agent_name)
        const ai = DEPARTMENT_ORDER.indexOf(aDept as typeof DEPARTMENT_ORDER[number])
        const bi = DEPARTMENT_ORDER.indexOf(bDept as typeof DEPARTMENT_ORDER[number])
        return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
      })
  }, [agents])

  // 按部门分组（用于 SectionGroup 分类展示，避免平铺导致 endless scrolling）
  const groupedByDept = useMemo(() => {
    const groups = new Map<string, AgentRunMetrics[]>()
    for (const agent of sortedAgents) {
      const dept = getDepartment(agent.position, agent.agent_name)
      const list = groups.get(dept) || []
      list.push(agent)
      groups.set(dept, list)
    }
    // 按 DEPARTMENT_ORDER 排序部门
    return DEPARTMENT_ORDER
      .filter((dept) => groups.has(dept))
      .map((dept) => ({ dept, agents: groups.get(dept)! }))
  }, [sortedAgents])

  // 概览指标（展现后端 Workforce 运行数据）
  const overviewMetrics: Metric[] = useMemo(() => {
    if (agents.length === 0) return []
    const inProduction = agents.filter((a) => a.lifecycle_stage === 'production').length
    const inTraining = agents.filter((a) =>
      a.lifecycle_stage === 'training' || a.lifecycle_stage === 'evaluation' || a.lifecycle_stage === 'recruit'
    ).length
    const withFailures = agents.filter((a) => a.tasks_failed > 0).length
    return [
      { key: 'total', icon: User, value: agents.length, label: 'AI 员工总数', tone: 'brand' },
      { key: 'production', icon: CheckCircle2, value: inProduction, label: '生产中', tone: 'success' },
      { key: 'training', icon: Clock, value: inTraining, label: '培训/评估中', tone: 'warning' },
      { key: 'anomalies', icon: AlertCircle, value: withFailures, label: '存在失败任务', tone: withFailures > 0 ? 'error' : 'info' },
    ]
  }, [agents])

  if (loading && agents.length === 0) {
    return (
      <Layout>
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      </Layout>
    )
  }

  // 容器入场动画 variants
  const containerVariants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
  }
  const itemVariants = {
    hidden: { opacity: 0, y: 12 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: 'easeOut' as const } },
  }

  return (
    <Layout>
      <motion.div
        className="max-w-[1600px] mx-auto w-full px-4 sm:px-6 py-8 space-y-6"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {/* 页头 */}
        <motion.div variants={itemVariants}>
          <PageHeader
            title="AI 员工"
            subtitle="AI 数字员工团队的状态、组成与协作关系"
            actions={
              <Button variant="primary" onClick={handleGenerate} disabled={genLoading}>
                <Sparkles className="w-4 h-4" aria-hidden="true" />
                {genLoading ? '生成中...' : '生成 AI 员工推荐'}
              </Button>
            }
          />
        </motion.div>

        {error && (
          <motion.div variants={itemVariants}>
            <ApiErrorState message={error} onRetry={loadData} retrying={loading} />
          </motion.div>
        )}

        {/* 概览指标 */}
        {agents.length > 0 && (
          <motion.div variants={itemVariants}>
            <MetricGrid metrics={overviewMetrics} columns={4} />
          </motion.div>
        )}

        {/* 员工卡片（按部门 SectionGroup 分组，可折叠） */}
        {agents.length === 0 ? (
          <motion.div variants={itemVariants}>
            <Card>
              <CardBody>
                <EmptyState
                  icon={User}
                  title="暂无 AI 员工"
                  description="系统基于岗位能力矩阵推荐 AI 数字员工。点击「生成 AI 员工推荐」创建你的数字员工团队。"
                  variant="brand"
                  action={{ label: '生成 AI 员工推荐', onClick: handleGenerate }}
                />
              </CardBody>
            </Card>
          </motion.div>
        ) : (
          <motion.div variants={itemVariants} className="space-y-3">
            {groupedByDept.map(({ dept, agents: deptAgents }, groupIdx) => (
              <SectionGroup
                key={dept}
                title={dept}
                icon={<LayoutGrid className="w-4 h-4" aria-hidden="true" />}
                badge={
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-brand-50 text-brand-500 text-xs font-medium">
                    {deptAgents.length} 人
                  </span>
                }
                description={DEPARTMENT_DESCRIPTIONS[dept]}
                defaultOpen={true}
              >
                <div className="p-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {deptAgents.map((agent, idx) => (
                    <EmployeeCard
                      key={agent.agent_id}
                      agent={agent}
                      config={configs[agent.agent_id] || null}
                      index={groupIdx * 3 + idx}
                      categoryLabel={dept}
                      onClick={() => handleOpenDetail(agent.agent_id)}
                    />
                  ))}
                </div>
              </SectionGroup>
            ))}
          </motion.div>
        )}
      </motion.div>

      {/* 员工详情抽屉（Drawer + InlineTabs，替代超长滚动 Dialog） */}
      <Drawer
        open={detailAgent !== null}
        onClose={() => setDetailAgentId(null)}
        title={detailAgent ? detailAgent.agent_name : ''}
        description={detailAgent ? getPositionLabel(detailAgent.position, detailAgent.agent_name) : undefined}
        width={560}
      >
        {detailAgent && (
          <EmployeeDetailContent
            agent={detailAgent}
            config={detailConfig || null}
            lifecycle={lifecycleMap[detailAgent.agent_id] || null}
          />
        )}
      </Drawer>

      {/* 推荐确认对话框 */}
      <Dialog
        open={showRecommend}
        title="确认 AI 员工推荐"
        description="系统基于岗位能力矩阵推荐以下 AI 数字员工，确认后将正式创建。"
        confirmText={confirmLoading ? '创建中...' : '确认创建'}
        cancelText="取消"
        onConfirm={handleConfirm}
        onCancel={() => setShowRecommend(false)}
      >
        <div className="max-h-80 overflow-y-auto space-y-2 mt-2">
          {recommendations.map((pos) => {
            const selected = selectedPositions.has(pos.position_id)
            return (
              <button
                key={pos.position_id}
                onClick={() => {
                  const next = new Set(selectedPositions)
                  if (next.has(pos.position_id)) next.delete(pos.position_id)
                  else next.add(pos.position_id)
                  setSelectedPositions(next)
                }}
                className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors text-left ${
                  selected
                    ? 'border-brand-300 bg-brand-50/30'
                    : 'border-border-default hover:bg-elevated/50'
                }`}
              >
                <div
                  className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 ${
                    selected ? 'bg-brand-500' : 'bg-elevated border border-border-default'
                  }`}
                >
                  {selected && <Check className="w-3.5 h-3.5 text-white" aria-hidden="true" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-primary">{pos.position_name}</span>
                    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs ${
                      pos.priority === 'P0'
                        ? 'bg-error/10 text-error'
                        : 'bg-warning/10 text-warning'
                    }`}>
                      {pos.priority}
                    </span>
                  </div>
                  <div className="text-xs text-text-tertiary mt-0.5">
                    {pos.department} · {pos.level} · 技能 {pos.required_skills.length} · 知识库 {pos.required_knowledge.length}
                  </div>
                </div>
              </button>
            )
          })}
        </div>
        <div className="mt-3 flex items-center gap-2 text-sm text-text-secondary">
          <UserCheck className="w-4 h-4" aria-hidden="true" />
          已选择 {selectedPositions.size} / {recommendations.length} 个岗位
        </div>
      </Dialog>
    </Layout>
  )
}
