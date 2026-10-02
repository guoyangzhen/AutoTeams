/**
 * 回环渲染服务在主进程内的生命周期。
 *
 * 单例原因：`createWindow` 在 macOS「关窗再打开」时会再次执行，
 * 如果每次都新起一个服务，旧实例既不会被关闭也会一直占着端口。
 * 这里保证同一主进程内只有一个回环服务，并在退出时统一关闭。
 */

import { startDesktopServer, type DesktopServer, type DesktopServerOptions } from './loopback-server.js'

let active: DesktopServer | null = null
let inflight: Promise<DesktopServer> | null = null

/** 当前生效的实例；未启动时为 null。主进程用它取 origin。 */
export function activeDesktopServer(): DesktopServer | null {
  return active
}

/**
 * 取得（必要时启动）回环服务。
 * 已在运行或正在启动时直接复用同一实例：渲染层 origin 必须在窗口重建后保持不变，
 * 否则每次重开窗口都会换一个 origin，Cookie 与 WebSocket 连接都要重连。
 */
export function ensureDesktopServer(options: DesktopServerOptions): Promise<DesktopServer> {
  if (active !== null) return Promise.resolve(active)
  if (inflight !== null) return inflight
  inflight = startDesktopServer(options).then(
    (server) => {
      active = server
      inflight = null
      return server
    },
    (error: unknown) => {
      inflight = null
      throw error
    },
  )
  return inflight
}

/** 关闭并清空当前实例。可重复调用；未启动时是空操作。 */
export function shutdownDesktopServer(): Promise<void> {
  const current = active
  active = null
  inflight = null
  return current === null ? Promise.resolve() : current.close()
}
