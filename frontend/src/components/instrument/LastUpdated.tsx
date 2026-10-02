import { useEffect, useState } from 'react'
import { RefreshCw, Radio } from 'lucide-react'

/**
 * LastUpdated — 数据新鲜度指示（UI v4 §4.2）
 *
 * 修复审计问题：WorkExecutionPage 有 30s 静默轮询但无任何提示，
 * 数据跳变突兀；AICompanyView 标称「实时运转」却零轮询。
 *
 * 本组件让数据新鲜度可见，同时用「实时/轮询」双模标识
 * 诚实告知当前的数据获取方式。
 */

interface LastUpdatedProps {
  /** 上次成功获取数据的时间戳（ms） */
  timestamp: number | null
  /** 是否正在刷新（静默刷新时给出微弱指示，不打断阅读） */
  refreshing?: boolean
  /** 数据通道：live=SSE 推流 / poll=轮询 */
  mode?: 'live' | 'poll'
  /** 手动刷新 */
  onRefresh?: () => void
  className?: string
}

export function LastUpdated({
  timestamp,
  refreshing = false,
  mode = 'poll',
  onRefresh,
  className = '',
}: LastUpdatedProps) {
  const [, forceTick] = useState(0)

  // 每 10 秒重算一次相对时间文本
  useEffect(() => {
    const timer = setInterval(() => forceTick((n) => n + 1), 10000)
    return () => clearInterval(timer)
  }, [])

  const text = (() => {
    if (!timestamp) return '尚未加载'
    const diff = Math.floor((Date.now() - timestamp) / 1000)
    if (diff < 10) return '刚刚更新'
    if (diff < 60) return `${diff} 秒前更新`
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前更新`
    return `${Math.floor(diff / 3600)} 小时前更新`
  })()

  return (
    <div className={`flex items-center gap-2 ${className}`}>
      {mode === 'live' ? (
        <span className="inline-flex items-center gap-1.5" title="服务端推流，数据实时到达">
          <Radio
            className={`w-3 h-3 sig-alive ${refreshing ? '' : 'sig-breathe'}`}
            aria-hidden="true"
          />
          <span className="instrument-label">LIVE</span>
        </span>
      ) : (
        <span className="instrument-label" title="定时轮询获取">
          POLL
        </span>
      )}
      <span className="text-caption text-[var(--text-muted)]">{text}</span>
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="p-1 rounded-sm hover:bg-[var(--bg-elevated)] text-[var(--text-tertiary)] disabled:opacity-50 transition-colors"
          aria-label="立即刷新"
        >
          <RefreshCw
            className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`}
            aria-hidden="true"
          />
        </button>
      )}
    </div>
  )
}
