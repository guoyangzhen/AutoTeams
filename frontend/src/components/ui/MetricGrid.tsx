/**
 * MetricGrid — 统计卡组合器。
 *
 * 包装 StatCard，统一管理各页面重复的"grid grid-cols-4 统计卡"块。
 * 支持 horizontal（左图标+右数字，首页业务概览）与 compact（图标右上+数字左下+trend）
 * 两种变体混排，自动响应式列数。
 *
 * 对标 prototype 今日AI公司顶部 4 卡 + 知识库统计 4 卡 + 老板运营中心核心指标 8 卡。
 */
import { type LucideIcon } from 'lucide-react'
import { type ReactNode } from 'react'
import { StatCard } from './StatCard'

type Tone = 'brand' | 'success' | 'warning' | 'error' | 'info'
type Variant = 'horizontal' | 'compact'

export interface Metric {
  /** 唯一 key */
  key: string
  /** 图标组件 */
  icon: LucideIcon
  /** 主数值（无数据时传 '—'） */
  value: ReactNode
  /** 标签 */
  label: string
  /** 语气色 */
  tone?: Tone
  /** 变体（默认 horizontal） */
  variant?: Variant
  /** 数值后缀（如"人"、"%"） */
  unit?: string
  /** 趋势百分比（仅 compact 变体显示） */
  trend?: number
  /** 点击回调 */
  onClick?: () => void
}

interface MetricGridProps {
  metrics: Metric[]
  /** 列数（默认 md:grid-cols-4，可传 2/3/4） */
  columns?: 2 | 3 | 4
  className?: string
}

const COL_CLASS: Record<number, string> = {
  2: 'grid-cols-2',
  3: 'grid-cols-2 md:grid-cols-3',
  4: 'grid-cols-2 md:grid-cols-4',
}

export function MetricGrid({ metrics, columns = 4, className = '' }: MetricGridProps) {
  return (
    <div className={`grid ${COL_CLASS[columns]} gap-4 ${className}`}>
      {metrics.map((m) => (
        <StatCard
          key={m.key}
          icon={m.icon}
          value={m.value}
          label={m.label}
          tone={m.tone}
          variant={m.variant}
          unit={m.unit}
          trend={m.trend}
          onClick={m.onClick}
        />
      ))}
    </div>
  )
}

export default MetricGrid
