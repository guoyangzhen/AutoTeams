/** P1-FE: 全局错误事件工具。
 *
 * 用法：
 *   dispatchAppError('某功能加载失败')
 *
 * App.tsx 中的 GlobalErrorToaster 会监听该事件并用 sonner 弹出提示。
 */
export const APP_ERROR_EVENT = 'app-error'

export interface AppErrorEventDetail {
  message: string
  type?: 'error' | 'warning' | 'info'
}

export function dispatchAppError(message: string, type: AppErrorEventDetail['type'] = 'error') {
  window.dispatchEvent(
    new CustomEvent<AppErrorEventDetail>(APP_ERROR_EVENT, {
      detail: { message, type },
    })
  )
}
