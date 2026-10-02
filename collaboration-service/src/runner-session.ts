/**
 * 本地守护进程（Local Runner）连接管理 — WebSocket 服务端
 *
 * 职责：
 * - 接受本地 Runner 对 /bridge 端点的外连请求。
 * - 认证：调用后端 /local-paths/{grantId}/claim 校验一次性 setup token，
 *   认领成功后为 Runner 建立受信任会话（绑定 grantId / enterprise / user / scope）。
 * - 心跳：周期性 ping，超时标记离线并通知后端 /connected /offline。
 * - 任务下发：把 REST 层收到的任务经 WS 下发给 Runner，等待 task_result。
 * - 就绪门禁：Runner 上报 ready 后必须等后端 /connected 确认成功，才置为就绪
 *   （计入在线数、允许下发任务、回 ready_ack）；被拒则回 error 并断开连接。
 *   等待期间重复 ready 只上报一次；socket 断开或同 grant 被新会话替换后，
 *   迟到的成功应答不得复活旧会话。
 *
 * 消息协议（Runner ↔ 协作服务）：
 * - 服务端 → Runner：welcome / task / ping / ready_ack
 * - Runner → 服务端：ping / pong / ready / task_result / error
 */
import { randomUUID } from 'crypto'
import type { WebSocket } from 'ws'

const backendUrl = (process.env.AUTOTEAMS_BACKEND_URL || process.env.AUTOFDE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

/** 心跳超时（毫秒），超过未收到 ping 视为离线 */
const HEARTBEAT_TIMEOUT_MS = parseInt(process.env.RUNNER_HEARTBEAT_TIMEOUT || '45', 10) * 1000

/** 单个 Runner 并发任务上限（防滥用/过载本地机器） */
const MAX_CONCURRENT_TASKS = Math.max(1, parseInt(process.env.RUNNER_MAX_CONCURRENT_TASKS || '5', 10))

/** 待处理任务（等待 Runner 返回结果） */
interface PendingTask {
  resolve: (result: unknown) => void
  reject: (error: Error) => void
  timer: NodeJS.Timeout
}

/** 已连接的 Runner 会话 */
export interface RunnerSession {
  grantId: string
  runnerId: string | null
  enterpriseId: string | null
  userId: string | null
  scope: string
  ws: WebSocket
  /** 最后心跳时间戳 */
  lastHeartbeat: number
  /** 是否已通过后端认领（claim 确认前不得接受 ready 等已认证语义的消息） */
  claimed: boolean
  /** 是否已就绪（后端已确认连接状态、本地路径校验通过、已上报工具清单） */
  ready: boolean
  /** 是否正在等待后端确认 ready（等待期间只允许一次上报） */
  readyReporting: boolean
  /** 进行中的任务（key: taskId） */
  pending: Map<string, PendingTask>
  /** 心跳定时器 */
  heartbeatTimer: NodeJS.Timeout | null
}

/** 会话池：grantId → RunnerSession */
const runners = new Map<string, RunnerSession>()

/** 心跳/清理检查定时器（模块级单例） */
let sweepTimer: NodeJS.Timeout | null = null

/** 认领回调（由 server.ts 注入）：调用后端 claim 校验 setup token */
export type ClaimHandler = (grantId: string, setupToken: string) => Promise<{
  grantId: string
  scope: string
  localPath: string
  enterpriseId: string
  userId: string
}>

/** 就绪回调（由 server.ts 注入）：通知后端 Runner 已连接 */
export type ReadyReportHandler = (
  grantId: string,
  runnerId: string,
  resolvedPath: string | null,
  toolManifest: unknown,
) => Promise<void>

/** 离线回调（由 server.ts 注入）：通知后端 Runner 离线 */
export type OfflineHandler = (grantId: string) => Promise<void>

/** Runner 在线数变化时的通知回调（server.ts 注入，用于向前端广播 runner_status） */
export type RunnerStatusListener = (online: number) => void

let claimHandler: ClaimHandler | null = null
let readyHandler: ReadyReportHandler | null = null
let offlineHandler: OfflineHandler | null = null
let statusListener: RunnerStatusListener | null = null

/** 注册回调（server.ts 启动时调用一次） */
export function configureRunnerSession(
  handlers: {
    onClaim: ClaimHandler
    onReady: ReadyReportHandler
    onOffline: OfflineHandler
    /** 在线 Runner 数变化时触发（向前端广播 runner_status） */
    onStatusChange?: RunnerStatusListener
  },
): void {
  claimHandler = handlers.onClaim
  readyHandler = handlers.onReady
  offlineHandler = handlers.onOffline
  statusListener = handlers.onStatusChange ?? null
}

/** 广播当前在线 Runner 数（连接建立/就绪/离线时调用）。仅将「已就绪」的 Runner 计入在线数。 */
function notifyStatusChange(): void {
  if (statusListener) {
    try {
      statusListener(onlineCount())
    } catch {
      // 广播失败不影响 Runner 会话主流程
    }
  }
}

/**
 * 查询在线 Runner 数量，供前端 runner_status 展示。
 *
 * 口径 =「连接仍有效且已就绪」：认领/就绪在途的占位会话、以及已关闭但 close
 * 事件尚未回收的会话都不算在线。
 */
export function onlineCount(): number {
  let count = 0
  for (const session of runners.values()) {
    if (session.ready && isSessionLive(session)) count++
  }
  return count
}

/** 统一向指定 Runner 发送 JSON 消息 */
function send(ws: WebSocket, payload: unknown): void {
  if (ws.readyState === ws.OPEN) {
    ws.send(JSON.stringify(payload))
  }
}

/**
 * 会话是否仍然有效：socket 未断开，且未被同 grant 的新会话替换。
 *
 * 迟到的后端应答必须先过这道检查，否则被撤销授权的旧连接会在 socket
 * 已关闭 / 会话已被顶替之后重新变成「在线」。
 */
function isSessionLive(session: RunnerSession): boolean {
  return runners.get(session.grantId) === session && session.ws.readyState === session.ws.OPEN
}

/** 启动全局心跳清扫（每 10s 检查一次所有连接） */
function ensureSweeper(): void {
  if (sweepTimer) return
  sweepTimer = setInterval(() => {
    const now = Date.now()
    for (const [grantId, session] of runners) {
      if (now - session.lastHeartbeat > HEARTBEAT_TIMEOUT_MS) {
        console.log(`[Runner] 心跳超时，标记离线: grant=${grantId}`)
        session.ws.close(4001, '心跳超时')
      }
    }
  }, 10_000)
  // unref：后台清扫定时器不应阻止进程退出（HTTP 服务本身会保持事件循环存活）
  sweepTimer.unref()
}

/** 清理会话占用的资源 */
function cleanupSession(session: RunnerSession): void {
  if (session.heartbeatTimer) clearInterval(session.heartbeatTimer)
  session.heartbeatTimer = null
  // 拒绝所有进行中的任务
  for (const [, pending] of session.pending) {
    clearTimeout(pending.timer)
    pending.reject(new Error('本地守护进程已断开连接'))
  }
  session.pending.clear()
  if (runners.get(session.grantId) === session) {
    runners.delete(session.grantId)
  }
  // 通知后端离线（仅当此前已连接过）
  if (session.ready && offlineHandler) {
    offlineHandler(session.grantId).catch(() => undefined)
  }
  notifyStatusChange()
}

/**
 * 未通过认证的连接：移出占位、回 error 并断开。
 * 占位清理由 close 事件收尾；提前移出是为了让同 grant 的后续连接立即可重试。
 */
function rejectUnauthenticated(
  session: RunnerSession,
  message: string,
  reason: string,
): void {
  if (runners.get(session.grantId) === session) {
    runners.delete(session.grantId)
    notifyStatusChange()
  }
  send(session.ws, { type: 'error', message })
  session.ws.close(1008, reason)
}

/**
 * 处理一条新的 Runner WebSocket 连接（由 server.ts 的 /bridge 端点调用）。
 *
 * @param ws 已建立的 WebSocket
 * @param grantId 授权记录 ID
 * @param setupToken 一次性 setup token
 */
export function handleRunnerConnection(
  ws: WebSocket,
  grantId: string,
  setupToken: string,
): void {
  if (!claimHandler || !readyHandler || !offlineHandler) {
    send(ws, { type: 'error', message: 'Runner 会话服务未就绪' })
    ws.close(1011, '服务未就绪')
    return
  }

  // 二次保护：确保 grantId / token 非空
  if (!grantId || !setupToken) {
    send(ws, { type: 'error', message: '缺少 grant 或 token' })
    ws.close(1008, '参数缺失')
    return
  }

  // 同一授权已被占用时拒绝重复连接
  if (runners.has(grantId)) {
    send(ws, { type: 'error', message: '该授权已被其他本地守护进程占用' })
    ws.close(1008, '授权已占用')
    return
  }

  const session: RunnerSession = {
    grantId,
    runnerId: null,
    enterpriseId: null,
    userId: null,
    scope: 'read',
    ws,
    lastHeartbeat: Date.now(),
    claimed: false,
    ready: false,
    readyReporting: false,
    pending: new Map(),
    heartbeatTimer: null,
  }

  // 认领竞态防护（P1 安全修复）：立即以未就绪状态占位，防止同一 grant 的
  // 两条连接在异步 claim 期间同时通过 runners.has 检查、后到者覆盖先到者。
  // 占位会话未 ready，dispatchTask 不会向其下发任务；claim 失败时清理占位。
  runners.set(grantId, session)

  ensureSweeper()

  // 异步认领（不阻塞连接建立，但认证前不处理任务）
  claimHandler(grantId, setupToken)
    .then((claim) => {
      // 认领期间连接已断开（sweeper 超时或客户端主动断开）时，放弃注册
      if (runners.get(grantId) !== session) {
        console.log(`[Runner] 认领完成但会话已被替换/清理: grant=${grantId}`)
        return
      }
      session.claimed = true
      session.enterpriseId = claim.enterpriseId
      session.userId = claim.userId
      session.scope = claim.scope
      console.log(`[Runner] 认证成功，已注册会话: grant=${grantId} scope=${claim.scope}`)
      send(ws, { type: 'welcome', grantId, scope: claim.scope, localPath: claim.localPath })

      // 开始心跳保活
      session.heartbeatTimer = setInterval(() => {
        send(ws, { type: 'ping' })
      }, HEARTBEAT_TIMEOUT_MS / 3)
    })
    .catch((err) => {
      console.error(`[Runner] 认证失败: ${err instanceof Error ? err.message : err}`)
      rejectUnauthenticated(session, '认证失败，token 无效或已过期', '认证失败')
    })

  // 处理 Runner 发来的消息
  ws.on('message', (data: Buffer) => {
    let msg: unknown
    try {
      msg = JSON.parse(data.toString())
    } catch {
      send(ws, { type: 'error', message: '无效的 JSON 消息' })
      return
    }
    handleRunnerMessage(session, msg)
  })

  ws.on('close', () => cleanupSession(session))
  ws.on('error', (_err) => {
    // 错误由 close 统一清理
    try {
      ws.close()
    } catch {
      /* ignore */
    }
  })
}

/** 分发 Runner 消息 */
function handleRunnerMessage(session: RunnerSession, raw: unknown): void {
  const msg = raw as { type?: string }
  if (!msg || typeof msg !== 'object') return

  switch (msg.type) {
    case 'ping':
      session.lastHeartbeat = Date.now()
      send(session.ws, { type: 'pong' })
      break

    case 'pong':
      session.lastHeartbeat = Date.now()
      break

    case 'ready': {
      const body = msg as {
        runnerId?: string
        resolvedPath?: string | null
        toolManifest?: unknown
      }
      // socket 已断开或同 grant 已被新会话替换：不再接受任何上报
      if (!isSessionLive(session)) return
      // claim 尚未确认：ws 监听早于认领结果注册，未认证连接不得触发任何后端上报
      if (!session.claimed) {
        rejectUnauthenticated(session, '连接尚未完成认证', '未完成认证')
        return
      }
      const report = readyHandler
      if (!report) return
      // 已就绪：幂等重发 ack，不重复上报后端
      if (session.ready) {
        send(session.ws, { type: 'ready_ack', grantId: session.grantId })
        return
      }
      // 在途去重：等待后端应答期间只允许一次上报，重复的 ready 直接忽略
      if (session.readyReporting) return

      session.readyReporting = true
      session.lastHeartbeat = Date.now()
      const runnerId = body.runnerId || `runner-${session.grantId}`

      // 后端确认前保持未就绪：既不计入在线数，也不会被下发任务
      report(session.grantId, runnerId, body.resolvedPath ?? null, body.toolManifest ?? null)
        .then(() => {
          session.readyReporting = false
          // 迟到的成功：socket 已断开或会话已被替换时不得复活旧会话
          if (!isSessionLive(session)) {
            console.log(`[Runner] 上报成功但会话已失效，丢弃: grant=${session.grantId}`)
            return
          }
          session.runnerId = runnerId
          session.ready = true
          console.log(`[Runner] 已就绪: grant=${session.grantId} runner=${runnerId}`)
          send(session.ws, { type: 'ready_ack', grantId: session.grantId })
          notifyStatusChange()
        })
        .catch((err) => {
          session.readyReporting = false
          console.error(
            `[Runner] 上报连接状态被后端拒绝: ${err instanceof Error ? err.message : err}`,
          )
          // 会话已失效（socket 已关 / 已被顶替）时不再操作，close 事件会做清理
          if (!isSessionLive(session)) return
          // 失败关闭：保持未就绪（不在线、不接任务）并断开，端侧需重新走配对流程
          session.ready = false
          runners.delete(session.grantId)
          notifyStatusChange()
          send(session.ws, {
            type: 'error',
            message: '连接状态上报被后端拒绝（授权可能已失效）',
          })
          try {
            session.ws.close(4003, 'ready 上报被拒绝')
          } catch {
            /* ignore */
          }
        })
      break
    }

    case 'task_result': {
      const body = msg as {
        taskId?: string
        ok?: boolean
        data?: unknown
        error?: string
      }
      if (!body.taskId) return
      session.lastHeartbeat = Date.now()
      const pending = session.pending.get(body.taskId)
      if (!pending) return
      session.pending.delete(body.taskId)
      clearTimeout(pending.timer)
      if (body.ok) {
        pending.resolve(body.data)
      } else {
        pending.reject(new Error(body.error || '本地守护进程执行失败'))
      }
      break
    }

    case 'error': {
      const body = msg as { message?: string }
      console.error(`[Runner] 上报错误: ${body.message || '未知错误'}`)
      session.lastHeartbeat = Date.now()
      break
    }
  }
}

/**
 * 向指定授权下发任务并等待结果。
 *
 * @param grantId 授权记录 ID
  * @param task 任务内容（tool/path/content）

 * @param timeoutMs 超时（毫秒）
 */
export async function dispatchTask(
  grantId: string,
    task: {
    tool: 'list' | 'read' | 'write' | 'delete'
    path?: string
    content?: string | null
  },

  timeoutMs = 120_000,
): Promise<unknown> {
  const session = runners.get(grantId)
  // 与 onlineCount / isRunnerReady 同口径：close 事件到达前 socket 可能已进入
  // CLOSING，此时 send() 会静默丢弃，任务会一直挂到超时，必须在这里就拒绝
  if (!session || !session.ready || !isSessionLive(session)) {
    throw new Error('本地守护进程不在线')
  }

  // 并发限额：超出上限的任务直接拒绝，避免本地机器被任务洪峰打挂
  if (session.pending.size >= MAX_CONCURRENT_TASKS) {
    throw new Error(`本地守护进程忙碌，当前并发任务已达上限（${MAX_CONCURRENT_TASKS}），请稍后再试`)
  }

  const taskId = randomUUID()
  const payload = {
    type: 'task',
    taskId,
    grantId,
    tool: task.tool,
    path: task.path ?? '',
    content: task.content ?? null,
  }

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      session.pending.delete(taskId)
      reject(new Error('本地守护进程执行超时'))
    }, timeoutMs)
    session.pending.set(taskId, { resolve, reject, timer })
    send(session.ws, payload)
  })
}

/** 查询某授权的 Runner 是否在线且就绪（与 onlineCount 同一口径） */
export function isRunnerReady(grantId: string): boolean {
  const session = runners.get(grantId)
  return Boolean(session && session.ready && isSessionLive(session))
}

/**
 * 在线 Runner 会话数（诊断用，/api/local/status 的 connected 字段）。
 *
 * 与 onlineCount 同口径：connected 就是「已建立且当前有效的连接数」，
 * 不再直接暴露 runners.size——那会把认领/就绪在途的占位会话也算成在线。
 */
export function runnerCount(): number {
  return onlineCount()
}

/** 获取某授权的 Runner 会话（诊断用） */
export function getRunnerSession(grantId: string): RunnerSession | null {
  return runners.get(grantId) || null
}
