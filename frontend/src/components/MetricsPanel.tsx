/**
 * MetricsPanel — 指标面板组件。
 *
 * 可复用的指标展示组件，支持：
 * - 指标卡片网格（数值 + 标签 + 趋势）
 * - L1-L5 成熟度等级展示
 * - 用于老板 Dashboard 的 7 核心 + 9 扩展指标呈现
 */
import type { ReactNode } from 'react'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { MaturityLevel } from '@/types'

interface Metric {
  label: string
  value: string | number
  unit?: string
  /** 趋势：正数表示上升，负数表示下降 */
  trend?: number
  /** 颜色主题 */
  color?: 'brand' | 'success' | 'warning' | 'error' | 'info' | 'neutral'
  /** 图标（可选） */
  icon?: ReactNode
}

interface MetricsPanelProps {
  title: string
  description?: string
  metrics: Metric[]
  /** 列数 */
  columns?: 2 | 3 | 4
}

const COLOR_CLASSES: Record<NonNullable<Metric['color']>, string> = {
  brand: 'text-brand-500',
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-error',
  info: 'text-info',
  neutral: 'text-text-primary',
}

/** 着色卡片背景 + 边框（对标 prototype tinted metric card） */
const TINT_CLASSES: Record<NonNullable<Metric['color']>, string> = {
  brand: 'bg-brand-50 border-brand-100',
  success: 'bg-success/5 border-success/20',
  warning: 'bg-warning/5 border-warning/20',
  error: 'bg-error/5 border-error/20',
  info: 'bg-info/5 border-info/20',
  neutral: 'bg-elevated border-border-default',
}

const COL_CLASSES: Record<NonNullable<MetricsPanelProps['columns']>, string> = {
  2: 'grid-cols-2',
  3: 'grid-cols-2 md:grid-cols-3',
  4: 'grid-cols-2 md:grid-cols-4',
}

export function MetricsPanel({
  title,
  description,
  metrics,
  columns = 3,
}: MetricsPanelProps) {
  return (
    <Card>
      <CardHeader>
        <h3 className="text-h4 text-text-primary">{title}</h3>
        {description && (
          <p className="text-sm text-text-tertiary mt-1">{description}</p>
        )}
      </CardHeader>
      <CardBody>
        <div className={`grid gap-3 ${COL_CLASSES[columns]}`}>
          {metrics.map((metric, idx) => {
            const color = metric.color || 'neutral'
            return (
              <div
                key={idx}
                className={`rounded-lg border p-4 ${TINT_CLASSES[color]}`}
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="text-xs text-text-tertiary">{metric.label}</div>
                  {metric.icon && (
                    <span className={`${COLOR_CLASSES[color]} opacity-80`}>{metric.icon}</span>
                  )}
                </div>
                <div className="flex items-baseline gap-1">
                  <span className={`text-2xl font-bold ${COLOR_CLASSES[color]}`}>
                    {metric.value}
                  </span>
                  {metric.unit && (
                    <span className="text-sm font-normal text-text-tertiary">{metric.unit}</span>
                  )}
                </div>
                {metric.trend !== undefined && (
                  <div
                    className={`text-xs mt-1 ${
                      metric.trend >= 0 ? 'text-success' : 'text-error'
                    }`}
                  >
                    {metric.trend >= 0 ? '↑' : '↓'} {Math.abs(metric.trend)}%
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </CardBody>
    </Card>
  )
}

/** L1-L5 成熟度等级展示（PRD §5.5）— 5 段进度条样式，对标 prototype */
const MATURITY_LEVELS: Array<{ level: MaturityLevel; label: string; desc: string }> = [
  { level: 'L1', label: '辅助', desc: 'AI 仅提供建议，人类执行全部' },
  { level: 'L2', label: '协作', desc: 'AI 执行部分，人类审批关键节点' },
  { level: 'L3', label: '自动', desc: 'AI 自主执行标准流程，异常转人工' },
  { level: 'L4', label: '自治', desc: 'AI 自主处理复杂场景，仅战略决策由人类' },
  { level: 'L5', label: '进化', desc: 'AI 自我优化组织结构与流程' },
]

export function MaturityGauge({ level }: { level: MaturityLevel }) {
  const currentIdx = MATURITY_LEVELS.findIndex((m) => m.level === level)
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-h4 text-text-primary">AI 成熟度等级</h3>
            <p className="text-sm text-text-tertiary mt-1">
              当前 {level} · {MATURITY_LEVELS[currentIdx]?.label}
            </p>
          </div>
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-brand-50 text-brand-500 text-sm font-medium">
            当前 {level} · {MATURITY_LEVELS[currentIdx]?.label}
          </span>
        </div>
      </CardHeader>
      <CardBody>
        {/* 5 段进度条（对标 prototype §老板运营中心） */}
        <div className="flex items-center gap-2">
          {MATURITY_LEVELS.map((m, idx) => {
            const reached = idx <= currentIdx
            const isCurrent = idx === currentIdx
            return (
              <div
                key={m.level}
                className={`flex-1 h-2 rounded-full ${
                  isCurrent
                    ? 'bg-brand-500 pulse-navy'
                    : reached
                    ? 'bg-success'
                    : 'bg-border-default'
                }`}
              />
            )
          })}
        </div>
        {/* 下方标签 */}
        <div className="flex justify-between mt-2 text-xs text-text-tertiary">
          {MATURITY_LEVELS.map((m, idx) => (
            <span
              key={m.level}
              className={idx === currentIdx ? 'text-brand-500 font-medium' : ''}
            >
              {m.level} {m.label}
            </span>
          ))}
        </div>
        <p className="text-xs text-text-tertiary mt-3 pt-3 border-t border-border-subtle">
          {MATURITY_LEVELS[currentIdx]?.desc}
        </p>
      </CardBody>
    </Card>
  )
}

export default MetricsPanel
