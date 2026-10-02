/**
 * PipelineStepper · LangGraph 构建流水线只读步进器
 *
 * 将 7 节点流水线以紧凑的垂直时间线呈现（替代原先的 ReactFlow 画布）：
 * - 每个节点显示状态点（已完成/执行中/失败/等待中）+ 名称 + 描述
 * - 当前执行节点高亮，approval 节点展示 HITL 审批检查点
 * - 可点击节点选中（在有 onSelect 回调时），配合右侧日志面板联动
 */
import { Check, X, Loader2, Clock } from 'lucide-react'

export type PipelineStepStatus = 'idle' | 'processing' | 'completed' | 'failed'

export interface PipelineStep {
  id: string
  label: string
  description: string
  status: PipelineStepStatus
}

interface PipelineStepperProps {
  steps: PipelineStep[]
  selectedId?: string | null
  onSelect?: (id: string) => void
  isPaused?: boolean
  approvalAutoApproved?: boolean
}

const STATUS_META: Record<PipelineStepStatus, { text: string; label: string }> = {
  completed: { text: 'text-success', label: '已完成' },
  processing: { text: 'text-brand-500', label: '执行中' },
  failed: { text: 'text-error', label: '失败' },
  idle: { text: 'text-text-tertiary', label: '等待中' },
}

export default function PipelineStepper({
  steps,
  selectedId,
  onSelect,
  isPaused = false,
  approvalAutoApproved = false,
}: PipelineStepperProps) {
  return (
    <ol className="py-2">
      {steps.map((step, i) => {
        const meta = STATUS_META[step.status]
        const isSelected = selectedId === step.id
        const isLast = i === steps.length - 1
        const interactive = typeof onSelect === 'function'
        return (
          <li key={step.id} className="relative flex gap-3">
            {/* 竖直连接线 */}
            {!isLast && (
              <span className="absolute left-[11px] top-6 bottom-0 w-px bg-border-subtle" aria-hidden="true" />
            )}

            {/* 状态点 */}
            <button
              type="button"
              onClick={() => onSelect?.(step.id)}
              disabled={!interactive}
              aria-label={step.label}
              className={`relative z-10 mt-0.5 w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 transition-all ${
                interactive ? 'cursor-pointer hover:scale-110' : 'cursor-default'
              } ${
                step.status === 'completed'
                  ? 'bg-success border-success'
                  : step.status === 'processing'
                    ? 'bg-brand-500 border-brand-500'
                    : step.status === 'failed'
                      ? 'bg-error border-error'
                      : 'bg-surface border-border-strong'
              } ${isSelected ? 'ring-2 ring-brand-500/30' : ''}`}
            >
              {step.status === 'completed' && <Check className="w-3 h-3 text-white" />}
              {step.status === 'processing' && <Loader2 className="w-3 h-3 text-white animate-spin" />}
              {step.status === 'failed' && <X className="w-3 h-3 text-white" />}
            </button>

            {/* 内容 */}
            <div
              className={`flex-1 pb-5 min-w-0 rounded-md transition-colors ${
                isSelected ? 'bg-elevated/70 px-2 py-1 -mx-1' : ''
              }`}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={`text-sm font-medium ${
                    step.status === 'idle' ? 'text-text-secondary' : 'text-text-primary'
                  }`}
                >
                  {step.label}
                </span>
                {step.status !== 'idle' && <span className={`text-xs ${meta.text}`}>{meta.label}</span>}
                {step.id === 'approval' && isPaused && !approvalAutoApproved && (
                  <span className="text-xs text-warning inline-flex items-center gap-1">
                    <Clock className="w-3 h-3" aria-hidden="true" />
                    等待审批
                  </span>
                )}
                {step.id === 'approval' && approvalAutoApproved && (
                  <span className="text-xs text-success">· 启发式自动通过</span>
                )}
              </div>
              <p className="text-xs text-text-tertiary mt-0.5">{step.description}</p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}