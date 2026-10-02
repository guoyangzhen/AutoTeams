import { Component, ErrorInfo, ReactNode } from 'react'
import { captureException } from '../utils/sentry'
import { getLastRequestId } from '../api/client'
import { Button } from './ui/Button'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
  showDetails: boolean
}

/**
 * P1-04-C: 路由级 ErrorBoundary
 *
 * 捕获子组件渲染过程中的 JavaScript 错误，显示降级 UI，
 * 防止整个应用白屏。用户可点击"重试"重新渲染（重置 state）。
 *
 * 同时展示诊断信息（错误摘要、最近后端 RequestId、组件堆栈），
 * 方便用户向支持团队反馈或开发排障。
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null, errorInfo: null, showDetails: false }
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // P3-2: 接入 Sentry 错误上报
    captureException(error, {
      componentStack: errorInfo.componentStack,
      requestId: getLastRequestId(),
    })
    this.setState({ errorInfo })
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null, errorInfo: null, showDetails: false })
  }

  toggleDetails = (): void => {
    this.setState((prev) => ({ showDetails: !prev.showDetails }))
  }

  render(): ReactNode {
    if (this.state.hasError) {
      const { error, errorInfo, showDetails } = this.state
      const requestId = getLastRequestId()
      const errorName = error?.name || 'Error'
      const errorMessage = error?.message || '发生未知错误'
      const componentStack = errorInfo?.componentStack || ''
      const stack = error?.stack || ''

      return (
        <div className="min-h-screen flex items-center justify-center bg-canvas p-4">
          <div className="max-w-lg w-full text-center">
            <div className="mb-6">
              <svg
                className="mx-auto h-16 w-16 text-error"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
                />
              </svg>
            </div>
            <h1 className="text-xl font-semibold text-text-primary mb-2">
              页面出错了
            </h1>
            <p className="text-sm text-text-secondary mb-4">
              发生未知错误，请重试或刷新页面。如果问题持续存在，请联系支持团队。
            </p>

            {/* P1-FE: 诊断信息卡片 */}
            <div className="rounded-lg border border-error/20 bg-error/5 p-4 mb-6 text-left">
              <p className="text-xs font-semibold text-error uppercase tracking-wide mb-2">
                诊断信息
              </p>
              <div className="space-y-1 text-sm text-text-secondary font-mono break-all">
                <p>
                  <span className="text-text-tertiary">错误类型：</span>
                  {errorName}
                </p>
                <p>
                  <span className="text-text-tertiary">错误消息：</span>
                  {errorMessage}
                </p>
                {requestId && (
                  <p>
                    <span className="text-text-tertiary">RequestId：</span>
                    {requestId}
                  </p>
                )}
              </div>
            </div>

            <div className="flex flex-wrap gap-3 justify-center mb-4">
              <Button variant="primary" size="sm" onClick={this.handleRetry}>
                重试
              </Button>
              <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
                刷新页面
              </Button>
              <Button variant="ghost" size="sm" onClick={this.toggleDetails}>
                {showDetails ? '隐藏详情' : '查看详情'}
              </Button>
            </div>

            {showDetails && (
              <div className="text-left rounded-lg border border-border-default bg-surface p-4 max-h-80 overflow-auto">
                {componentStack && (
                  <div className="mb-4">
                    <p className="text-xs font-semibold text-text-tertiary mb-1">组件堆栈</p>
                    <pre className="text-xs text-text-secondary whitespace-pre-wrap font-mono">
                      {componentStack}
                    </pre>
                  </div>
                )}
                {stack && (
                  <div>
                    <p className="text-xs font-semibold text-text-tertiary mb-1">错误堆栈</p>
                    <pre className="text-xs text-text-secondary whitespace-pre-wrap font-mono">
                      {stack}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )
    }

    return this.props.children
  }
}

export default ErrorBoundary
