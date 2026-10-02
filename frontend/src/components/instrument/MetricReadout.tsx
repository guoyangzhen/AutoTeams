import { useEffect, useRef, useState } from 'react'

/**
 * MetricReadout — 仪表数字读数（UI v4 §4.2）
 *
 * 特征：
 *   - JetBrains Mono + tabular-nums，数字变化时宽度不抖动
 *   - 数值变化时播放滚动动画（L1 层级，240ms）
 *   - 支持趋势指示与信号色语义
 *
 * 数字滚动是「仪表在读数」的核心质感来源，但必须尊重 reduced-motion。
 */

export type ReadoutTone = 'default' | 'alive' | 'focus' | 'alert' | 'fault' | 'idle'

interface MetricReadoutProps {
  /** 仪表标签（自动转大写宽字距） */
  label?: string
  /** 数值：数字则播放滚动动画，字符串直接展示 */
  value: number | string
  /** 单位后缀（%、次/时、人 等） */
  unit?: string
  /** 小数位（仅 number 生效） */
  digits?: number
  tone?: ReadoutTone
  /** 辅助说明（数据来源/口径） */
  hint?: string
  /** 尺寸 */
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const TONE_CLASS: Record<ReadoutTone, string> = {
  default: 'text-[var(--text-primary)]',
  alive: 'sig-alive',
  focus: 'sig-focus',
  alert: 'sig-alert',
  fault: 'sig-fault',
  idle: 'sig-idle',
}

const SIZE_CLASS: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'text-[18px] leading-tight',
  md: 'text-[26px] leading-tight',
  lg: 'text-[38px] leading-none',
}

/** 数字滚动：从旧值缓动到新值 */
function useAnimatedNumber(target: number, digits: number): string {
  const [display, setDisplay] = useState(target)
  const fromRef = useRef(target)
  const rafRef = useRef<number>()

  useEffect(() => {
    const reduced =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const from = fromRef.current
    if (reduced || from === target) {
      fromRef.current = target
      setDisplay(target)
      return
    }
    const start = performance.now()
    const duration = 420
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / duration)
      // easeOutExpo：起步快、收尾稳，符合仪表读数的物理感
      const eased = p === 1 ? 1 : 1 - Math.pow(2, -10 * p)
      setDisplay(from + (target - from) * eased)
      if (p < 1) rafRef.current = requestAnimationFrame(tick)
      else fromRef.current = target
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      fromRef.current = target
    }
  }, [target])

  return display.toFixed(digits)
}

export function MetricReadout({
  label,
  value,
  unit,
  digits = 0,
  tone = 'default',
  hint,
  size = 'md',
  className = '',
}: MetricReadoutProps) {
  const isNum = typeof value === 'number' && Number.isFinite(value)
  const animated = useAnimatedNumber(isNum ? (value as number) : 0, digits)
  const text = isNum ? animated : String(value)

  return (
    <div className={className}>
      {label && <p className="instrument-label mb-1.5">{label}</p>}
      <div className="flex items-baseline gap-1.5">
        <span className={`instrument-readout font-semibold ${SIZE_CLASS[size]} ${TONE_CLASS[tone]}`}>
          {text}
        </span>
        {unit && (
          <span className="text-body-sm text-[var(--text-muted)] font-medium">{unit}</span>
        )}
      </div>
      {hint && <p className="mt-1 text-caption text-[var(--text-muted)]">{hint}</p>}
    </div>
  )
}
