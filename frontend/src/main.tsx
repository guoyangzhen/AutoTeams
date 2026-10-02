import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import { initSentry, captureException } from './utils/sentry'
import { dispatchAppError } from './utils/errors'
import './index.css'
// D5: UI 细化覆盖层（普通 CSS 优先级高于 Tailwind utilities layer，无需 !important）
import './styles/refinement.css'
// AutoTeams 4.0 克制编辑式设计系统（作用域化 token 层，见 styles/editorial.css 头注）
import './styles/editorial.css'

// P3-2: 尽早初始化 Sentry，确保能捕获启动阶段错误
initSentry()

// P2-T18 + P1-FE: 全局未捕获 Promise rejection 处理（同时提示用户并上报）
window.addEventListener('unhandledrejection', (event) => {
  console.error('未捕获的 Promise rejection:', event.reason)
  captureException(event.reason instanceof Error ? event.reason : new Error(String(event.reason)), {
    context: 'unhandledrejection',
  })
  const reason = event.reason
  const message =
    typeof reason === 'string'
      ? reason
      : reason instanceof Error
        ? reason.message
        : '发生未知错误，请刷新页面重试'
  dispatchAppError(message)
  // 阻止浏览器默认的控制台错误输出（已手动记录）
  event.preventDefault()
})

// P2-T18 + P1-FE: 全局未捕获错误处理（同时提示用户并上报）
window.addEventListener('error', (event) => {
  console.error('未捕获的错误:', event.error || event.message)
  captureException(event.error instanceof Error ? event.error : new Error(event.message || '未知全局错误'), {
    context: 'global_error',
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
  })
  dispatchAppError(event.message || '发生未知错误，请刷新页面重试')
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  // 说明：未包裹 React.StrictMode。开发模式下 StrictMode 会「挂载→卸载→再挂载」组件，
  // 导致 SSE 实时流建立后立即被清理 abort（控制台出现 net::ERR_ABORTED），并产生重复请求。
  // 对演示交付场景，去掉 StrictMode 以保持控制台干净、SSE 单连接稳定。
  <ErrorBoundary>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </ErrorBoundary>,
)
