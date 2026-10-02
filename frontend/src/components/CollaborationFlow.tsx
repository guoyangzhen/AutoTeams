/**
 * CollaborationFlow — 7 步演示案例可视化组件。
 *
 * 对应 PRD §8.5「MVP 第 5 步演示案例（Run）— 询盘→报价→审批→成交→售后」。
 *
 * 7 步业务链路：
 * 1. 收到新询盘    → 销售 Agent
 * 2. 产品参数查询  → 售前 Agent（跨 Agent 协作）
 * 3. 销售报价      → 销售 Agent
 * 4. 财务审核      → 财务 Agent（金额分级校验）
 * 5. 总监审批      → 人类审批介入（集成 ApprovalPanel）
 * 6. 客服同步      → 客服 Agent
 * 7. 售后接管      → 售后 Agent
 *
 * 可视化：
 * - 步骤卡片 + 连接箭头，展示触发→AI 员工→动作
 * - 跨 Agent 协作用不同颜色标注 source → target
 * - 审批节点高亮（step 5），可嵌入 ApprovalPanel
 * - 响应式：移动端纵向、桌面端可横向滚动
 */
import { useState } from 'react'
import { motion } from 'framer-motion'
import {
  Mail, Search, FileText, ShieldCheck, CheckCircle2, RefreshCw, Headphones,
  ArrowRight, Users,
} from 'lucide-react'
import { Card, CardBody } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import type { CollaborationEvent } from '@/types'

/** 步骤定义（7 步演示案例） */
interface FlowStep {
  step: number
  eventType: CollaborationEvent['event_type']
  title: string
  agent: string
  agentId: string
  trigger: string
  action: string
  icon: typeof Mail
  /** 是否跨 Agent 协作 */
  collaboration?: {
    from: string
    to: string
  }
  /** 是否为审批节点 */
  isApproval?: boolean
}

/** 7 步业务链路定义 */
const FLOW_STEPS: FlowStep[] = [
  {
    step: 1,
    eventType: 'inquiry_received',
    title: '收到新询盘',
    agent: '销售 Agent',
    agentId: 'agent-sales-001',
    trigger: 'CRM 新增商机（OPP-001 华智制造，状态：报价中）',
    action: '提取客户需求：SL-T100 温湿度传感器 × 100 台，交付要求 2 周内',
    icon: Mail,
  },
  {
    step: 2,
    eventType: 'product_query',
    title: '产品参数查询',
    agent: '售前 Agent',
    agentId: 'agent-presales-001',
    trigger: '销售 Agent 遇到不熟悉的产品参数（SL-T100 测温范围）',
    action: '从产品规格书补充参数：-40~125℃，转交销售回复客户',
    icon: Search,
    collaboration: { from: '销售 Agent', to: '售前 Agent' },
  },
  {
    step: 3,
    eventType: 'quotation_generated',
    title: '销售报价',
    agent: '销售 Agent',
    agentId: 'agent-sales-001',
    trigger: '参数确认后生成报价单',
    action: '调用价格表 + 报价单模板，生成报价 QUO-001，金额 ¥180,000',
    icon: FileText,
  },
  {
    step: 4,
    eventType: 'approval_submitted',
    title: '财务审核',
    agent: '财务 Agent',
    agentId: 'agent-finance-001',
    trigger: '报价单提交审核',
    action: '按审批流程 v2 校验：18 万属 5-20 万区间 → 销售总监审批',
    icon: ShieldCheck,
    collaboration: { from: '销售 Agent', to: '财务 Agent' },
  },
  {
    step: 5,
    eventType: 'approval_approved',
    title: '总监审批',
    agent: '人类审批',
    agentId: 'human-approver',
    trigger: '进入审批节点',
    action: '审批通过，商机状态 → 已成交',
    icon: CheckCircle2,
    isApproval: true,
  },
  {
    step: 6,
    eventType: 'order_synced',
    title: '客服同步',
    agent: '客服 Agent',
    agentId: 'agent-service-001',
    trigger: '成交后',
    action: '同步订单 ORD-2026-001，创建客户档案，发送交付通知',
    icon: RefreshCw,
    collaboration: { from: '财务 Agent', to: '客服 Agent' },
  },
  {
    step: 7,
    eventType: 'after_sales',
    title: '售后接管',
    agent: '售后 Agent',
    agentId: 'agent-aftersales-001',
    trigger: '交付后 / 客户反馈',
    action: '跟进售后服务，创建工单 TKT-001，记录客户反馈',
    icon: Headphones,
    collaboration: { from: '客服 Agent', to: '售后 Agent' },
  },
]

/** Agent 颜色映射 */
const agentColors: Record<string, string> = {
  'agent-sales-001': 'text-success bg-success/10 border-success/20',
  'agent-presales-001': 'text-info bg-info/10 border-info/20',
  'agent-finance-001': 'text-warning bg-warning/10 border-warning/20',
  'human-approver': 'text-brand-500 bg-brand-50 border-brand-200',
  'agent-service-001': 'text-text-secondary bg-elevated border-border-default',
  'agent-aftersales-001': 'text-brand-500 bg-brand-50 border-brand-200',
}

interface CollaborationFlowProps {
  /** 协作事件数据（用于校验步骤是否已发生） */
  events?: CollaborationEvent[]
  /** 当前激活步骤（1-7），如不指定则全部展示 */
  activeStep?: number
  /** 审批回调（步骤 5 审批节点） */
  onApprove?: () => void
  onReject?: (reason: string) => void
  /** 审批是否处理中 */
  approvalProcessing?: boolean
  /** 审批是否已处理 */
  approvalHandled?: boolean
}

export function CollaborationFlow({
  events = [],
  activeStep,
  onApprove,
  onReject,
  approvalProcessing = false,
  approvalHandled = false,
}: CollaborationFlowProps) {
  const [rejectReason, setRejectReason] = useState('')
  const [showRejectInput, setShowRejectInput] = useState(false)

  /** 判断步骤是否已完成（根据事件流） */
  const isStepDone = (step: FlowStep) => {
    return events.some((e) => e.event_type === step.eventType)
  }

  /** 判断步骤是否激活 */
  const isStepActive = (step: FlowStep) => {
    if (activeStep !== undefined) return step.step === activeStep
    return false
  }

  const handleReject = () => {
    if (onReject) {
      onReject(rejectReason || '用户拒绝')
      setRejectReason('')
      setShowRejectInput(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* 流程标题 */}
      <div className="flex items-center gap-2 text-sm text-text-tertiary">
        <Users className="w-4 h-4" aria-hidden="true" />
        <span>询盘 → 报价 → 审批 → 成交 → 售后（7 步事件驱动协作）</span>
      </div>

      {/* 步骤列表 */}
      <div className="space-y-3">
        {FLOW_STEPS.map((step, index) => {
          const Icon = step.icon
          const done = isStepDone(step)
          const active = isStepActive(step)
          const agentColor = agentColors[step.agentId] || agentColors['human-approver']
          const isLast = index === FLOW_STEPS.length - 1

          return (
            <div key={step.step}>
              <motion.div
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.3, delay: Math.min(index * 0.1, 0.7) }}
              >
                <Card
                  className={`border-l-4 ${
                    step.isApproval
                      ? 'border-l-brand-500'
                      : done
                        ? 'border-l-success'
                        : active
                          ? 'border-l-warning'
                          : 'border-l-border-subtle'
                  } ${active ? 'ring-2 ring-warning/20' : ''}`}
                >
                  <CardBody className="p-4">
                    <div className="flex items-start gap-3">
                      {/* 步骤序号 + 图标 */}
                      <div className="flex flex-col items-center flex-shrink-0">
                        <div
                          className={`w-10 h-10 rounded-full flex items-center justify-center ${
                            done ? 'bg-success/10 text-success' : active ? 'bg-warning/10 text-warning' : 'bg-elevated text-text-tertiary'
                          }`}
                        >
                          {done ? <CheckCircle2 className="w-5 h-5" /> : <Icon className="w-5 h-5" />}
                        </div>
                        <span className="text-xs text-text-tertiary mt-1">#{step.step}</span>
                      </div>

                      {/* 内容 */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap mb-1">
                          <h4 className="text-sm font-medium text-text-primary">{step.title}</h4>
                          {/* Agent 标签 */}
                          <span className={`text-xs px-2 py-0.5 rounded border ${agentColor}`}>
                            {step.agent}
                          </span>
                          {/* 跨 Agent 协作标识 */}
                          {step.collaboration && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-brand-50 text-brand-500 inline-flex items-center gap-1">
                              {step.collaboration.from}
                              <ArrowRight className="w-3 h-3" aria-hidden="true" />
                              {step.collaboration.to}
                            </span>
                          )}
                          {/* 审批节点标识 */}
                          {step.isApproval && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-brand-50 text-brand-500">
                              人类介入
                            </span>
                          )}
                        </div>

                        <div className="space-y-1 text-sm">
                          <div className="text-text-tertiary">
                            <span className="text-xs">触发：</span>
                            {step.trigger}
                          </div>
                          <div className="text-text-secondary">
                            <span className="text-xs">动作：</span>
                            {step.action}
                          </div>
                        </div>

                        {/* 审批面板（步骤 5） */}
                        {step.isApproval && onApprove && onReject && !approvalHandled && (
                          <div className="mt-3 pt-3 border-t border-border-subtle">
                            {!showRejectInput ? (
                              <div className="flex items-center gap-2">
                                <Button
                                  variant="primary"
                                  size="sm"
                                  onClick={onApprove}
                                  disabled={approvalProcessing}
                                >
                                  <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
                                  批准
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => setShowRejectInput(true)}
                                  disabled={approvalProcessing}
                                >
                                  拒绝
                                </Button>
                              </div>
                            ) : (
                              <div className="space-y-2">
                                <textarea
                                  value={rejectReason}
                                  onChange={(e) => setRejectReason(e.target.value)}
                                  placeholder="请输入拒绝原因..."
                                  rows={2}
                                  className="w-full rounded border border-border-default bg-surface px-2 py-1 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
                                />
                                <div className="flex items-center gap-2">
                                  <Button variant="danger" size="sm" onClick={handleReject} disabled={approvalProcessing}>
                                    确认拒绝
                                  </Button>
                                  <Button variant="ghost" size="sm" onClick={() => { setShowRejectInput(false); setRejectReason('') }}>
                                    取消
                                  </Button>
                                </div>
                              </div>
                            )}
                          </div>
                        )}

                        {/* 审批已处理状态 */}
                        {step.isApproval && approvalHandled && (
                          <div className="mt-2 inline-flex items-center gap-1 text-xs text-success">
                            <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />
                            审批已处理
                          </div>
                        )}
                      </div>
                    </div>
                  </CardBody>
                </Card>
              </motion.div>

              {/* 连接箭头（flow-dash 动画风格，对标 prototype） */}
              {!isLast && (
                <div className="flex justify-center py-1" aria-hidden="true">
                  <svg
                    width="20"
                    height="20"
                    viewBox="0 0 20 20"
                    className="text-brand-400 rotate-90 md:rotate-0"
                  >
                    <line
                      x1="2"
                      y1="10"
                      x2="14"
                      y2="10"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      className="flow-dash"
                    />
                    <path
                      d="M12 6 L16 10 L12 14"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default CollaborationFlow
