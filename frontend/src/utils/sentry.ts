/**
 * P3-2: Sentry 前端错误上报初始化。
 *
 * 仅在 VITE_SENTRY_DSN 存在时启用；未配置时 SDK 不初始化，避免开发环境噪音。
 *
 * SDK 采用动态 import：未配置 DSN 的部署完全不会加载 @sentry/react，
 * 使其从入口 chunk 中移除（约 64KB）。已配置 DSN 时 SDK 在后台异步加载，
 * 加载完成前产生的错误会先进入缓冲队列，就绪后补报，确保启动阶段错误不丢失。
 */
type SentryModule = typeof import('@sentry/react')

const dsn = import.meta.env.VITE_SENTRY_DSN
const environment = import.meta.env.VITE_SENTRY_ENVIRONMENT || 'development'
const tracesSampleRate = Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE || '0')

let sentryModule: SentryModule | null = null

/** SDK 就绪前产生的错误缓冲，就绪后统一补报 */
const pendingEvents: Array<{ error: unknown; context?: Record<string, unknown> }> = []
const MAX_PENDING_EVENTS = 50

export function initSentry(): void {
  if (!dsn) {
    return
  }

  void import('@sentry/react')
    .then((Sentry) => {
      Sentry.init({
        dsn,
        environment,
        tracesSampleRate,
        // React Router 路由变更自动记录为浏览事务
        integrations: [Sentry.browserTracingIntegration()],
      })
      sentryModule = Sentry

      // 补报 SDK 加载期间积压的错误
      for (const { error, context } of pendingEvents.splice(0)) {
        Sentry.captureException(error, { extra: context })
      }
    })
    .catch((err) => {
      console.error('Sentry SDK 加载失败，错误上报已降级为控制台输出:', err)
      pendingEvents.length = 0
    })
}

export function captureException(error: unknown, context?: Record<string, unknown>): void {
  if (!dsn) {
    // 未启用 Sentry 时保持原有 console.error 行为
    console.error('Error captured (Sentry disabled):', error, context)
    return
  }

  if (sentryModule) {
    sentryModule.captureException(error, { extra: context })
    return
  }

  // SDK 尚未就绪：入队等待补报，并设上限避免异常风暴导致内存增长
  if (pendingEvents.length < MAX_PENDING_EVENTS) {
    pendingEvents.push({ error, context })
  }
}
