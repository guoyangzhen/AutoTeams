/**
 * 本地守护进程 — WebSocket 客户端
 *
 * 主动外连云端协作服务的 /bridge 端点，完成认领后接收云端下发的本地任务。
 * 具备：鉴权、心跳、断线重连、任务分发。
 */
import WebSocket from 'ws'
import { configureExecutor, executeTask, initLocal } from './executor.js'
import { disposePhysicalSession } from './physical.js'
import { readPhysicalBridgeConfig, startPhysicalBridge, type PhysicalBridge } from './physical-bridge.js'
import { settleCleanup } from './shutdown.js'

/** 桥接地址（ws://host:port/bridge?grant=xx&token=yy） */
let bridgeUrl = process.env.AUTOTEAMS_BRIDGE_URL || ''
/** 授权路径（本地文件夹） */
let grantPath = process.env.AUTOTEAMS_GRANT_PATH || ''
/** 授权范围（read / read_write） */
let grantScope = process.env.AUTOTEAMS_GRANT_SCOPE || 'read'

/** 重连退避（毫秒） */
const RECONNECT_MAX_DELAY = 30_000
const HEARTBEAT_INTERVAL = 15_000
const CLAIM_TIMEOUT = 15_000
const MAX_RECONNECT_ATTEMPTS = 5

let reconnectDelay = 1_000
let closing = false
let heartbeatTimer: NodeJS.Timeout | null = null
let reconnectTimer: NodeJS.Timeout | null = null
let claimTimer: NodeJS.Timeout | null = null
let activeSocket: WebSocket | null = null
let reconnectAttempts = 0
let claimed = false
let localReady = false

/** 具身物理桥接句柄（未配置物理参数时为 null）。 */
let physicalBridge: PhysicalBridge | null = null

/** 打印带时间戳的日志 */
function log(level: 'info' | 'error', message: string): void {
  const ts = new Date().toISOString()
  // eslint-disable-next-line no-console
  console[level === 'error' ? 'error' : 'log'](`[${ts}] [Local Runner] ${message}`)
}

/** 启动心跳保活 */
function startHeartbeat(ws: WebSocket): void {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping' }))
    }
  }, HEARTBEAT_INTERVAL)
}

function stopHeartbeat(): void {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

/** 脱敏连接地址：去掉 query 中的 token，避免令牌泄露到本地日志 */
export function safeBridgeUrl(raw: string): string {
  try {
    const u = new URL(raw)
    const grant = u.searchParams.get('grant')
    u.username = ''
    u.password = ''
    u.search = ''
    u.hash = ''
    if (grant) u.searchParams.set('grant', grant)
    return u.toString()
  } catch {
    return '[invalid bridge URL]'
  }
}

function stopClaimTimer(): void {
  if (claimTimer) clearTimeout(claimTimer)
  claimTimer = null
}

function requireAuthorization(message: string): void {
  log('error', `${message} 请在工作台重新授权目录，使用新生成的连接命令。`)
  shutdown(1)
}

/** 建立连接并处理消息 */
function connect(): void {
  if (closing) return
  const url = `${bridgeUrl}`
  log('info', `正在连接云端桥接服务: ${safeBridgeUrl(url)}`)
  let ws: WebSocket

  try {
    ws = new WebSocket(url, { handshakeTimeout: 10_000 })
    activeSocket = ws
  } catch {
    // Constructor errors can echo a credential-bearing URL.
    log('error', '无法创建连接，请检查桥接地址和连接参数。')
    shutdown(1)
    return
  }

  ws.on('open', () => {
    log('info', '已连接云端桥接服务，等待认领…')
    startHeartbeat(ws)
    claimTimer = setTimeout(() => {
      log('error', '等待认领响应超时。')
      ws.terminate()
    }, CLAIM_TIMEOUT)
  })

  ws.on('message', (data: Buffer) => {
    let msg: unknown
    try {
      msg = JSON.parse(data.toString())
    } catch {
      return
    }
    handleMessage(ws, msg)
  })

  ws.on('close', (code) => {
    stopHeartbeat()
    stopClaimTimer()
    activeSocket = null
    localReady = false
    // Close reasons and server errors are untrusted and may echo credentials.
    log('info', `连接已断开 (code=${code})`)
    if (closing) return
    if (claimed || code === 1008 || code === 4002) {
      requireAuthorization(claimed ? '连接令牌已在认领时消费，无法用于重连。' : '目录授权被拒绝或本地校验失败。')
      return
    }
    scheduleReconnect()
  })

  ws.on('error', () => {
    log('error', '桥接连接失败，请检查服务状态与网络。')
  })

  ws.on('unexpected-response', (_request, response) => {
    const status = response.statusCode ?? 0
    response.resume()
    if ([400, 401, 403, 404, 410, 426].includes(status)) {
      log('error', `桥接握手被拒绝 (HTTP ${status})，请检查地址与授权后重新启动。`)
      shutdown(1)
    } else {
      ws.terminate()
    }
  })
}

/** 处理云端消息 */
function handleMessage(ws: WebSocket, raw: unknown): void {
  const msg = raw as { type?: string }
  if (!msg || typeof msg !== 'object') return

  switch (msg.type) {
    case 'welcome': {
      if (claimed || closing) return
      claimed = true
      stopClaimTimer()
      const body = msg as { grantId?: string; scope?: string; localPath?: string }
      log('info', `认领成功，授权范围: ${body.scope || grantScope}`)
      // 本地校验授权路径，成功后上报就绪
      try {
        const info = initLocal(grantPath, body.scope || grantScope)
        ws.send(
          JSON.stringify({
            type: 'ready',
            runnerId: info.runnerId,
            resolvedPath: info.resolvedPath,
            toolManifest: info.toolManifest,
          }),
        )
        localReady = true
        log('info', `本地授权校验通过，已就绪: ${info.resolvedPath}`)
      } catch (err) {
        log('error', `本地路径校验失败: ${err instanceof Error ? err.message : err}`)
        ws.send(JSON.stringify({ type: 'error', message: `本地路径校验失败: ${err}` }))
        ws.close(4002, '本地路径校验失败')
      }
      break
    }

    case 'ready_ack':
      log('info', '云端已确认就绪，可接收任务')
      break

    case 'ping':
      ws.send(JSON.stringify({ type: 'pong' }))
      break

    case 'pong':
      break

    case 'task': {
      if (!localReady || closing) return
      // 只从不可信网络消息中提取当前执行器实际需要的标量字段。历史 command/
      // prompt/adapter/cwd 一律不进入任务对象，避免未来代码误把已废弃字段当作可执行输入。
      const task = msg as {
        taskId?: unknown
        tool?: unknown
        path?: unknown
        content?: unknown
      }
      const taskId = task.taskId
      const tool = task.tool
      if (typeof taskId !== 'string' || typeof tool !== 'string') return
      const path = typeof task.path === 'string' ? task.path : ''
      const content = typeof task.content === 'string' || task.content === null ? task.content : null
      log('info', `收到任务: ${tool} ${path}`)
      ;(async () => {
        const result = await executeTask({
          taskId,
          tool,
          path,
          content,
        })
        if (ws.readyState === WebSocket.OPEN && !closing) {
          ws.send(JSON.stringify({ type: 'task_result', taskId, ...result }))
        }
      })().catch(() => log('error', '任务处理或结果发送失败，请在工作台检查任务状态。'))
      break
    }

    case 'error': {
      log('error', '云端报告错误，请检查工作台中的连接或任务状态。')
      break
    }
  }
}

/** 断线重连（指数退避） */
function scheduleReconnect(): void {
  if (closing || reconnectTimer) return
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    log('error', '连接重试次数已达上限，请检查服务与网络后重新运行；若令牌已过期，请重新授权。')
    shutdown(1)
    return
  }
  reconnectAttempts += 1
  log('info', `将在 ${reconnectDelay / 1000}s 后重连…`)
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    if (!closing) connect()
  }, reconnectDelay)
  reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_DELAY)
}

/** 优雅退出 */
async function shutdown(exitCode = 0): Promise<void> {
  if (closing) return
  closing = true
  stopHeartbeat()
  stopClaimTimer()
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = null
  activeSocket?.terminate()
  log('info', '正在退出…')
  const bridge = physicalBridge
  if (bridge && !(await settleCleanup(() => bridge.stop()))) {
    log('error', '物理心跳未能及时停止，回执锁可能保留；请检查本机进程。')
  }
  if (!(await settleCleanup(disposePhysicalSession))) {
    log('error', '物理会话清理失败或超时，请检查本机浏览器进程。')
  }
  process.exit(exitCode)
}

/** 启动入口 */
export function start(): void {
  // CLI 入口（index.ts）解析参数后才设置 env，这里在运行期读取以避免模块加载期取值过早
  bridgeUrl = process.env.AUTOTEAMS_BRIDGE_URL || ''
  grantPath = process.env.AUTOTEAMS_GRANT_PATH || ''
  grantScope = process.env.AUTOTEAMS_GRANT_SCOPE || 'read'

  if (!bridgeUrl) {
    log('error', '缺少 AUTOTEAMS_BRIDGE_URL 环境变量（连接地址）')
    process.exit(1)
  }
  if (!grantPath) {
    log('error', '缺少 AUTOTEAMS_GRANT_PATH 环境变量（授权本地路径）')
    process.exit(1)
  }
  configureExecutor((payload) => {
    /* 目前任务结果直接回传，无需额外透传 */
    void payload
  })
  const physicalConfig = readPhysicalBridgeConfig()
  if (physicalConfig) {
    try {
      physicalBridge = startPhysicalBridge(physicalConfig)
    } catch {
      // 回执损坏、锁被占用或数据目录不可写时只关闭物理能力。
      // 目录文件桥接使用独立授权，不应被物理账本故障拖垮。
      log('error', '物理执行未启动：请检查设备回执账本及锁文件；目录连接继续启动。')
    }
  }
  process.on('SIGINT', () => shutdown())
  process.on('SIGTERM', () => shutdown())
  connect()
}
