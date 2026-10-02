/**
 * FlowChain — 横向流程链组件。
 *
 * 对标 prototype 协作流程图：圆形节点 + flow-dash 流动虚线箭头连接。
 * 节点状态：done 已完成（success 勾）/ in_progress 进行中（brand 脉冲）/ pending 未开始（elevated 灰）。
 *
 * 用于：
 * - AICompanyView 7 步业务协作链路（询盘→产品查询→报价→审批→同步→售后）
 * - CompilePage 5 级编译器横向流程视图
 * - CollaborationFlow 协作关系可视化
 */
import { type LucideIcon, CheckCircle2, Clock } from 'lucide-react'

export type FlowStepStatus = 'done' | 'in_progress' | 'pending'

export interface FlowStep {
  id: string
  label: string
  /** 节点图标（默认 done=CheckCircle2, in_progress/pending=Clock） */
  icon?: LucideIcon
  status: FlowStepStatus
  /** 副标题（如时间、执行者） */
  hint?: string
}

interface FlowChainProps {
  steps: FlowStep[]
  /** 节点圆形尺寸（默认 w-10 h-10） */
  size?: 'sm' | 'md' | 'lg'
  /** 是否可横向滚动（默认 true，适配窄屏） */
  scrollable?: boolean
  className?: string
}

const SIZE_CLASS = {
  sm: { node: 'w-8 h-8', icon: 'w-4 h-4', gap: 'w-6' },
  md: { node: 'w-10 h-10', icon: 'w-5 h-5', gap: 'w-8' },
  lg: { node: 'w-12 h-12', icon: 'w-6 h-6', gap: 'w-10' },
}

function StepNode({
  step,
  sizeKey,
}: {
  step: FlowStep
  sizeKey: 'sm' | 'md' | 'lg'
}) {
  const size = SIZE_CLASS[sizeKey]
  const CustomIcon = step.icon
  const DefaultIcon = step.status === 'done' ? CheckCircle2 : Clock

  const nodeClass =
    step.status === 'done'
      ? 'bg-success text-white'
      : step.status === 'in_progress'
        ? 'bg-brand-500 text-white pulse-navy'
        : 'bg-elevated text-text-muted border border-border-default'

  const Icon = CustomIcon || DefaultIcon

  return (
    <div className="flex flex-col items-center gap-1.5 flex-shrink-0">
      <div
        className={`${size.node} rounded-full flex items-center justify-center transition-colors ${nodeClass}`}
        aria-label={step.label}
      >
        <Icon className={size.icon} aria-hidden="true" />
      </div>
      <span
        className={`text-xs whitespace-nowrap ${
          step.status === 'done'
            ? 'text-text-primary'
            : step.status === 'in_progress'
              ? 'text-brand-500 font-medium'
              : 'text-text-tertiary'
        }`}
      >
        {step.label}
      </span>
      {step.hint && (
        <span className="text-[10px] text-text-muted whitespace-nowrap">{step.hint}</span>
      )}
    </div>
  )
}

function StepConnector({ status, sizeKey }: { status: FlowStepStatus; sizeKey: 'sm' | 'md' | 'lg' }) {
  const size = SIZE_CLASS[sizeKey]
  // 进行中阶段的连接线用 flow-dash 动画；已完成用实线；未开始用虚线灰
  const lineClass =
    status === 'in_progress'
      ? 'flow-dash'
      : status === 'done'
        ? ''
        : ''
  const strokeColor =
    status === 'done'
      ? '#16A34A'
      : status === 'in_progress'
        ? '#1E3A5F'
        : '#E5E3DC'
  return (
    <svg
      className={`${size.gap} h-0.5 self-start mt-5 flex-shrink-0`}
      viewBox="0 0 32 2"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <line
        x1="0"
        y1="1"
        x2="32"
        y2="1"
        stroke={strokeColor}
        strokeWidth="2"
        strokeLinecap="round"
        className={lineClass}
      />
    </svg>
  )
}

export function FlowChain({
  steps,
  size = 'md',
  scrollable = true,
  className = '',
}: FlowChainProps) {
  if (steps.length === 0) return null

  return (
    <div
      className={`flex items-start gap-1 ${scrollable ? 'overflow-x-auto scrollbar-thin pb-2' : ''} ${className}`}
      role="list"
    >
      {steps.map((step, idx) => (
        <div key={step.id} className="flex items-start flex-shrink-0" role="listitem">
          <StepNode step={step} sizeKey={size} />
          {idx < steps.length - 1 && (
            <StepConnector status={step.status} sizeKey={size} />
          )}
        </div>
      ))}
    </div>
  )
}

export default FlowChain
