/**
 * WT5 通用 API 错误状态组件。
 *
 * 满足 spec.md §2.5 约束：所有 API 连接页面必须实现一致的错误状态——
 * 用户可见告警 + 重试按钮。所有调用后端 API 的页面统一使用本组件，
 * 确保错误处理体验一致。
 */
import { AlertCircle, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/Button'

interface ApiErrorStateProps {
  /** 错误信息 */
  message?: string
  /** 重试回调 */
  onRetry?: () => void
  /** 重试按钮文本 */
  retryText?: string
  /** 是否正在重试中（显示 loading） */
  retrying?: boolean
}

export function ApiErrorState({
  message = '数据加载失败',
  onRetry,
  retryText = '重试',
  retrying = false,
}: ApiErrorStateProps) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-error/30 bg-error/5 p-4 flex items-start gap-3"
    >
      <AlertCircle className="w-5 h-5 text-error flex-shrink-0 mt-0.5" aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-error font-medium">{message}</p>
        {onRetry && (
          <Button
            variant="outline"
            size="sm"
            onClick={onRetry}
            disabled={retrying}
            className="mt-3"
          >
            <RefreshCw className={`w-4 h-4 ${retrying ? 'animate-spin' : ''}`} aria-hidden="true" />
            {retrying ? '重试中...' : retryText}
          </Button>
        )}
      </div>
    </div>
  )
}

export default ApiErrorState
