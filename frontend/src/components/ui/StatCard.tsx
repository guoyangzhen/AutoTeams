/**
 * StatCard — 统一统计卡片（融合 prototype 视觉模式）。
 *
 * 两种变体：
 * - horizontal（默认）：左侧彩色图标 + 右侧大数字 + 标签。用于首页业务概览。
 * - compact：图标右上角 + 大数字左下角 + 可选 trend。用于知识库、LoopDashboard 等。
 */
import { LucideIcon } from 'lucide-react'
import { ReactNode } from 'react'

type Tone = 'brand' | 'success' | 'warning' | 'error' | 'info'

const TONE_CLASSES: Record<Tone, { iconBg: string; iconText: string; value?: string }> = {
  brand: { iconBg: 'bg-brand-50', iconText: 'text-brand-500' },
  success: { iconBg: 'bg-success/10', iconText: 'text-success' },
  warning: { iconBg: 'bg-warning/10', iconText: 'text-warning' },
  error: { iconBg: 'bg-error/10', iconText: 'text-error' },
  info: { iconBg: 'bg-info/10', iconText: 'text-info' },
}

interface StatCardProps {
  /** 图标组件 */
  icon: LucideIcon
  /** 主数值 */
  value: ReactNode
  /** 标签 */
  label: string
  /** 语气色（默认 brand） */
  tone?: Tone
  /** 数值后缀（如"人"、"%"） */
  unit?: string
  /** 变体：horizontal 左图标右数字；compact 图标右上+数字左下（对标 prototype 知识库页） */
  variant?: 'horizontal' | 'compact'
  /** 趋势百分比（正数上升绿色，负数下降红色）— 仅 compact 变体显示 */
  trend?: number
  className?: string
  /** 点击回调（可选） */
  onClick?: () => void
}

export function StatCard({
  icon: Icon,
  value,
  label,
  tone = 'brand',
  unit,
  variant = 'horizontal',
  trend,
  className = '',
  onClick,
}: StatCardProps) {
  const toneClass = TONE_CLASSES[tone]
  const Comp = onClick ? 'button' : 'div'

  // compact 变体（对标 prototype §知识库 统计卡：图标右上 + 大数字左下 + trend）
  if (variant === 'compact') {
    return (
      <Comp
        className={`bg-surface border border-border-default rounded-xl p-4 text-left transition-colors ${
          onClick ? 'hover:border-border-strong cursor-pointer' : ''
        } ${className}`}
        onClick={onClick}
        type={onClick ? 'button' : undefined}
      >
        <div className="flex items-center justify-between">
          <div className="text-xs text-text-tertiary">{label}</div>
          <Icon className={`w-4 h-4 ${toneClass.iconText}`} aria-hidden="true" />
        </div>
        <div className="text-2xl font-bold text-text-primary tabular-nums mt-2">
          {value}
          {unit && <span className="text-sm font-normal text-text-muted ml-1">{unit}</span>}
        </div>
        {trend !== undefined && (
          <div className={`text-xs mt-1 ${trend >= 0 ? 'text-success' : 'text-error'}`}>
            {trend >= 0 ? '↑' : '↓'} {Math.abs(trend)}% vs 上周期
          </div>
        )}
      </Comp>
    )
  }

  // horizontal 变体（默认：左图标 + 右数字，对标 prototype §今日AI公司 顶部统计）
  return (
    <Comp
      className={`bg-surface border border-border-default rounded-lg p-4 flex items-center gap-3 text-left transition-colors ${
        onClick ? 'hover:border-border-strong cursor-pointer' : ''
      } ${className}`}
      onClick={onClick}
      type={onClick ? 'button' : undefined}
    >
      <div className={`w-10 h-10 rounded-lg ${toneClass.iconBg} flex items-center justify-center flex-shrink-0`}>
        <Icon className={`w-5 h-5 ${toneClass.iconText}`} aria-hidden="true" />
      </div>
      <div className="min-w-0">
        <div className="text-2xl font-bold text-text-primary tabular-nums">
          {value}
          {unit && <span className="text-sm font-normal text-text-tertiary ml-1">{unit}</span>}
        </div>
        <div className="text-xs text-text-tertiary mt-0.5">{label}</div>
      </div>
    </Comp>
  )
}

export default StatCard
