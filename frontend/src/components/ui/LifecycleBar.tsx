/**
 * LifecycleBar — 生命周期/成熟度水平段条组件。
 *
 * 对标 prototype 数字员工生命周期与 AI 成熟度等级 L1-L5：
 * 多段水平进度条，已完成段 success 色，当前段 brand 色 + pulse-navy 脉冲，
 * 未达成段 border-default 灰色。底部图例说明各阶段。
 *
 * 用于：
 * - WorkforceView 员工生命周期（试运行→评估→生产→退役）
 * - AICompanyView 运营概览 AI 成熟度 L1-L5
 * - CompilePage 编译阶段进度
 */
import { type ReactNode } from 'react'

export type StageStatus = 'done' | 'current' | 'pending' | 'warning' | 'error'

export interface LifecycleStage {
  key: string
  label: string
  /** 状态：done 已完成 / current 当前进行 / pending 未开始 / warning 异常 / error 错误 */
  status: StageStatus
  /** 简短说明（如"L3 自主"） */
  hint?: string
}

interface LifecycleBarProps {
  /** 阶段列表（按顺序） */
  stages: LifecycleStage[]
  /** 行标签（如员工名，用于多行场景） */
  label?: string
  /** 右侧状态文字（如"生产环境"） */
  statusText?: string
  /** 是否显示底部图例（默认 true） */
  showLegend?: boolean
  /** 自定义图例（覆盖默认） */
  legend?: ReactNode
  className?: string
}

const STATUS_BAR_CLASS: Record<StageStatus, string> = {
  done: 'bg-success',
  current: 'bg-brand-500 pulse-navy',
  pending: 'bg-border-default',
  warning: 'bg-warning',
  error: 'bg-error',
}

const STATUS_TEXT_CLASS: Record<StageStatus, string> = {
  done: 'text-success',
  current: 'text-brand-500 font-medium',
  pending: 'text-text-muted',
  warning: 'text-warning',
  error: 'text-error',
}

export function LifecycleBar({
  stages,
  label,
  statusText,
  showLegend = true,
  legend,
  className = '',
}: LifecycleBarProps) {
  return (
    <div className={className}>
      <div className="flex items-center gap-3">
        {label && (
          <span className="text-xs text-text-tertiary w-16 flex-shrink-0 truncate">
            {label}
          </span>
        )}
        <div className="flex-1 flex items-center gap-1">
          {stages.map((stage) => (
            <div
              key={stage.key}
              className={`flex-1 h-2 rounded ${STATUS_BAR_CLASS[stage.status]}`}
              title={stage.hint || stage.label}
            />
          ))}
        </div>
        {statusText && (
          <span
            className={`text-xs w-20 text-right flex-shrink-0 ${
              STATUS_TEXT_CLASS[stages.find((s) => s.status === 'current')?.status || 'pending']
            }`}
          >
            {statusText}
          </span>
        )}
      </div>
      {showLegend && (
        <div className="flex items-center gap-4 mt-3 text-xs text-text-tertiary flex-wrap">
          {legend || (
            <>
              {stages.map((stage) => (
                <span key={stage.key} className="flex items-center gap-1.5">
                  <span
                    className={`w-3 h-3 rounded ${STATUS_BAR_CLASS[stage.status]}`}
                  />
                  {stage.label}
                </span>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default LifecycleBar
