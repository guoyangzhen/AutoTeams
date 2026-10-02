import { motion } from 'framer-motion'
import { AlertTriangle, TrendingDown, Zap } from 'lucide-react'

/**
 * BottleneckCallout — 流程瓶颈提示（UI v4 §4.2）
 *
 * 修复审计问题：后端 OrgMetrics.process_efficiency 里的
 * bottleneck_step / automation_rate 是**最有价值的经营洞察**，
 * 但前端零展示。老板最想知道的就是「哪里卡住了」。
 *
 * 该组件把它提升为驾驶舱的一等公民。
 */

interface BottleneckCalloutProps {
  /** 瓶颈环节名称 */
  step?: string | null
  /** 该环节的平均耗时描述（如「4.2 小时」） */
  duration?: string | null
  /** 自动化率 0-1 */
  automationRate?: number | null
  /** 事件总数 / 已处理数，用于说明口径 */
  totalEvents?: number
  processedEvents?: number
  /** 点击查看详情 */
  onInspect?: () => void
  className?: string
}

export function BottleneckCallout({
  step,
  duration,
  automationRate,
  totalEvents,
  processedEvents,
  onInspect,
  className = '',
}: BottleneckCalloutProps) {
  // 无瓶颈数据时诚实显示健康态，不编造
  const hasBottleneck = Boolean(step)
  const rate = typeof automationRate === 'number' ? automationRate : null

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.34 }}
      className={`rounded-lg border p-4 ${
        hasBottleneck
          ? 'border-[var(--sig-alert,#D97706)] bg-[rgba(245,165,36,0.08)]'
          : 'border-[var(--border-subtle)] bg-[var(--bg-elevated)]'
      } ${className}`}
    >
      <div className="flex items-start gap-3">
        <span
          className={`w-8 h-8 rounded-md flex items-center justify-center flex-shrink-0 ${
            hasBottleneck ? 'bg-[rgba(245,165,36,0.16)]' : 'bg-[rgba(77,216,192,0.12)]'
          }`}
        >
          {hasBottleneck ? (
            <AlertTriangle className="w-4 h-4 sig-alert" aria-hidden="true" />
          ) : (
            <Zap className="w-4 h-4 sig-alive" aria-hidden="true" />
          )}
        </span>

        <div className="min-w-0 flex-1">
          <p className="instrument-label mb-1">流程瓶颈分析</p>
          {hasBottleneck ? (
            <p className="text-body-sm text-[var(--text-primary)] leading-relaxed">
              当前最长等待出现在
              <span className="font-semibold sig-alert mx-1">{step}</span>
              环节
              {duration && (
                <>
                  ，平均耗时
                  <span className="instrument-readout font-semibold sig-alert mx-1">
                    {duration}
                  </span>
                </>
              )}
            </p>
          ) : (
            <p className="text-body-sm text-[var(--text-secondary)] leading-relaxed">
              各流程环节运转顺畅，未检测到明显瓶颈
            </p>
          )}

          <div className="mt-2 flex items-center gap-4 flex-wrap">
            {rate !== null && (
              <span className="flex items-center gap-1.5">
                <TrendingDown className="w-3 h-3 text-[var(--text-muted)]" aria-hidden="true" />
                <span className="instrument-label">自动化率</span>
                <span className="instrument-readout text-body-sm font-semibold text-[var(--text-primary)]">
                  {(rate * 100).toFixed(0)}%
                </span>
              </span>
            )}
            {typeof totalEvents === 'number' && (
              <span className="text-caption text-[var(--text-muted)]">
                统计口径：{processedEvents ?? 0} / {totalEvents} 个协作事件
              </span>
            )}
          </div>
        </div>

        {onInspect && (
          <button
            type="button"
            onClick={onInspect}
            className="flex-shrink-0 text-caption font-semibold sig-focus hover:underline"
          >
            查看详情
          </button>
        )}
      </div>
    </motion.div>
  )
}
