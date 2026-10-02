/**
 * 端侧物理桥接（Local Runner 2.0 → 云端 REST）
 *
 * WebSocket 桥接只承载文件类任务；具身物理任务走独立的 REST 链路：
 * 启动时用「设备 ID + 长期凭据」换短期访问令牌，之后所有机器调用都带
 * `Authorization: Bearer <device_token>`。
 *
 * AUD-04：端侧**不再**使用全局共享的 `X-Bridge-Secret`。该密钥属于
 * collaboration-service ↔ backend 的服务间通道，与设备身份完全分离；
 * 拿到共享密钥不再能冒充任何设备。
 *
 * AUD-10：线上契约统一 snake_case，视窗帧字段是 `payload_b64`（与共享样例
 * `contract/runner_v2_payloads.json` 一致），不再有 `payloadB64`。
 *
 * AUD-18：每个 step 执行前先写本地回执账本（`receipt-ledger.ts`）。断网或
 * 进程被杀之后重连，只补发已完成 step 的回执，**不重新执行**动作。
 *
 * 高危操作的双因子第二因子在本机完成：云端完成意图确认后，端侧心跳才会拿到
 * 确认码，Runner 在本机控制台提示员工输入，校验通过后任务才真正下发。
 */
import { createInterface } from 'node:readline'
import { createHash } from 'node:crypto'
import { homedir } from 'node:os'
import { lstatSync } from 'node:fs'
import { join } from 'node:path'
import {
  executePhysicalTask,
  fileCredentialStore,
  FRAME_HEIGHT,
  FRAME_WIDTH,
  type PhysicalChannel,
  type PhysicalTask,
  type PhysicalStep,
} from './physical.js'
import {
  acquireReceiptLedgerLease,
  fileReceiptLedger,
  ReceiptLedgerFullError,
  type ReceiptLedger,
} from './receipt-ledger.js'

/** 心跳轮询间隔（毫秒）。低于云端 45s 的保活窗口，容忍偶发抖动。 */
export const HEARTBEAT_INTERVAL_MS = 10_000

/** 设备访问令牌的提前刷新窗口（秒）：到期前主动换新，避免请求中途失效。 */
export const TOKEN_REFRESH_MARGIN_SECONDS = 120

/** 单次心跳的视窗帧上报上限，避免弱网下无限积压。 */
const MAX_FRAMES_PER_TASK = 120

/**
 * 云端"不会再接受这条回执"的状态码。
 *
 * 404：任务不存在（清理或从未下发）；409：任务已终结 / 当前状态不接受回执。
 * 动作此时早已执行过，继续重发只会每 10 秒刷一次日志，永远不会被接受。
 */
const TERMINAL_RECEIPT_REJECTIONS: Record<number, true> = { 404: true, 409: true }

export interface HeartbeatPayload {
  server_time: string
  heartbeat_ttl_seconds: number
  runner: { runner_id: string; online: boolean }
  pending_tasks: Array<{ task_id: string; channel: string; steps: PhysicalStep[] }>
  notifications: Array<{
    challenge_id: string
    task_id: string
    state: string
    reason: string
    device_code?: string
  }>
}

export interface PhysicalBridgeConfig {
  apiUrl: string
  deviceId: string
  deviceSecret: string
  runnerId: string
  scopes: string[]
  vaultPath: string
  platform: string
  version: string
  /** 按设备持久化的回执账本；物理执行没有内存降级路径。 */
  receiptLedgerPath: string
}

/** 物理桥接运行时句柄。 */
export interface PhysicalBridge {
  stop(): Promise<void>
}

function ledgerPathExists(path: string): boolean {
  try {
    lstatSync(path)
    return true
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return false
    throw err // An unreadable legacy path must never silently select a fresh ledger.
  }
}

export function resolveDefaultReceiptLedger(homeDirectory: string, ledgerName: string): string {
  const legacyLedger = join(homeDirectory, '.autofde', 'runner-data', 'receipts', ledgerName)
  const currentLedger = join(homeDirectory, '.autoteams', 'runner-data', 'receipts', ledgerName)
  const legacyPresent = ledgerPathExists(legacyLedger) || ledgerPathExists(`${legacyLedger}.lock`)
  const currentPresent = ledgerPathExists(currentLedger) || ledgerPathExists(`${currentLedger}.lock`)
  if (legacyPresent && currentPresent) {
    throw new Error('新旧物理回执账本同时存在；请先人工核对账本与锁，物理执行已暂停')
  }
  return legacyPresent ? legacyLedger : currentLedger
}

function log(message: string): void {
  // eslint-disable-next-line no-console
  console.log(`[${new Date().toISOString()}] [Local Runner 物理桥接] ${message}`)
}

/** 从环境变量解析物理桥接配置；缺少必要项时返回 null（物理能力保持关闭）。 */
export function readPhysicalBridgeConfig(
  env: NodeJS.ProcessEnv = process.env,
): PhysicalBridgeConfig | null {
  const apiUrl = env.AUTOTEAMS_API_URL
  const deviceId = env.AUTOTEAMS_DEVICE_ID
  const deviceSecret = env.AUTOTEAMS_DEVICE_SECRET
  const runnerId = env.AUTOTEAMS_RUNNER_ID
  if (!apiUrl || !deviceId || !deviceSecret || !runnerId) return null
  const ledgerName = createHash('sha256').update(`${deviceId}\0${runnerId}`).digest('hex') + '.json'
  const receiptLedgerPath = env.AUTOTEAMS_RECEIPT_LEDGER || resolveDefaultReceiptLedger(homedir(), ledgerName)
  return {
    apiUrl: apiUrl.replace(/\/+$/, ''),
    deviceId,
    deviceSecret,
    runnerId,
    scopes: (env.AUTOTEAMS_PHYSICAL_SCOPES || 'read,physical')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean),
    vaultPath: env.AUTOTEAMS_CREDENTIAL_VAULT || '',
    platform: env.AUTOTEAMS_PLATFORM || process.platform,
    version: '2.0.0',
    receiptLedgerPath,
  }
}

/** 设备令牌：到期前自动换新，避免长时间运行后所有调用 401。 */
export class DeviceTokenHolder {
  private token = ''
  private expiresAtMs = 0

  constructor(
    private readonly apiUrl: string,
    private readonly deviceId: string,
    private readonly deviceSecret: string,
  ) {}

  current(): string {
    return this.token
  }

  isFresh(nowSeconds: number): boolean {
    return Boolean(this.token) && nowSeconds * 1000 < this.expiresAtMs - TOKEN_REFRESH_MARGIN_SECONDS * 1000
  }

  adopt(accessToken: string, expiresAtIso: string): void {
    this.token = accessToken
    this.expiresAtMs = Date.parse(expiresAtIso)
  }

  async refresh(): Promise<boolean> {
    const resp = await fetch(`${this.apiUrl}/api/v1/runner/v2/devices/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_id: this.deviceId, device_secret: this.deviceSecret }),
    })
    if (!resp.ok) {
      log(`设备令牌换取失败 (${resp.status})，物理能力保持静默`)
      return false
    }
    const data = (await resp.json()) as {
      data?: { access_token?: string; expires_at?: string }
    }
    const token = data?.data?.access_token
    const expiresAt = data?.data?.expires_at
    if (typeof token !== 'string' || !token || typeof expiresAt !== 'string' ||
      !Number.isFinite(Date.parse(expiresAt)) ||
      Date.parse(expiresAt) <= Date.now() + TOKEN_REFRESH_MARGIN_SECONDS * 1000) {
      log('设备令牌响应无效，物理能力保持静默')
      return false
    }
    this.adopt(token, expiresAt)
    return true
  }

  /** 只返回仍有刷新余量的令牌；失败时绝不回退到空令牌或旧令牌。 */
  async requireFresh(): Promise<string> {
    if (!this.isFresh(Math.floor(Date.now() / 1000))) {
      const refreshed = await this.refresh()
      if (!refreshed || !this.isFresh(Math.floor(Date.now() / 1000))) {
        throw new Error('设备认证暂不可用，物理请求已暂停')
      }
    }
    return this.current()
  }
}

/** 启动物理桥接心跳循环。 */
export function startPhysicalBridge(config: PhysicalBridgeConfig): PhysicalBridge {
  const credentials = config.vaultPath
    ? fileCredentialStore(config.vaultPath)
    : { resolve: () => null }
  const releaseLedger = acquireReceiptLedgerLease(config.receiptLedgerPath)
  let ledger: ReceiptLedger
  try {
    ledger = fileReceiptLedger(config.receiptLedgerPath)
  } catch (err) {
    releaseLedger()
    throw err
  }
  const tokens = new DeviceTokenHolder(config.apiUrl, config.deviceId, config.deviceSecret)
  /** 已完成双因子放行的任务（端侧第二因子已校验通过）。 */
  const approvedTasks = new Set<string>()
  /** 正在执行的任务，避免重复领取。 */
  const running = new Set<string>()
  let stopped = false
  let ticking = false
  let ledgerPaused = false
  let activeTick: Promise<void> = Promise.resolve()

  const post = async (path: string, body: unknown): Promise<Response> => {
    if (stopped) throw new Error('物理桥接已停止')
    const accessToken = await tokens.requireFresh()
    if (stopped) throw new Error('物理桥接已停止')
    return fetch(`${config.apiUrl}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(body),
    })
  }

  /** 上报一条已完成 step 的回执；成功后标记为已确认。 */
  const reportStep = async (record: {
    task_id: string
    step_id: string
    ok: boolean
    data?: Record<string, unknown>
    error?: string
  }): Promise<void> => {
    const resp = await post(`/api/v1/runner/v2/tasks/${record.task_id}/result`, {
      ok: record.ok,
      data: record.data ?? {},
      error: record.error,
      receipt_id: `${record.task_id}::${record.step_id}`,
      step_id: record.step_id,
    })
    if (resp.ok) {
      ledger.markReported(record.task_id, record.step_id)
      return
    }
    if (TERMINAL_RECEIPT_REJECTIONS[resp.status]) {
      // 云端拒绝 = 这条回执**没有**被记账。绝不标记为已确认：写入 rejected
      // 状态与状态码，移出自动重发队列（重发必然再次被拒），并保留可观测记录
      // 供人工处置。
      ledger.markRejected(record.task_id, record.step_id, resp.status, await resp.text())
      log(
        `云端拒绝回执 (${resp.status})：任务 ${record.task_id} step ${record.step_id} ` +
          '的结果尚未入账，需人工核对',
      )
      return
    }
    // 其余错误（网络抖动 / 5xx）：不从账本里删除，下一轮 tick 会重发同一条
    // 回执，而不是重新执行动作。
    log(`执行结果上报失败 (${resp.status})，回执保留待重发`)
  }

  /** 重连后补发已完成但未确认的回执。 */
  const flushPendingReceipts = async (): Promise<void> => {
    for (const record of ledger.pendingReports()) {
      if (stopped) return
      await reportStep(record)
    }
  }

  const pushFrame = async (
    taskId: string,
    frame: { kind: string; width: number; height: number; payload_b64: string },
  ): Promise<void> => {
    const resp = await post(`/api/v1/runner/v2/tasks/${taskId}/frames`, frame)
    if (!resp.ok) log(`视窗帧上报失败 (${resp.status})`)
  }

  /** 在本机控制台采集端侧物理确认码（第二因子）。 */
  const promptDeviceCode = (challengeId: string, code: string): Promise<string> => {
    // Runner 声明 engines >= 18，Promise.withResolvers 需 Node 22+，
    // 因此这里保留 executor 形态（readline.question 是回调式 API）。
    const promise = new Promise<string>((resolve) => {
      const rl = createInterface({ input: process.stdin, output: process.stdout })
      rl.question(
        `\n⚠  高危物理操作已获云端意图确认（挑战 ${challengeId}）。\n` +
          `   请核对本机屏幕上的操作内容，确认无误后输入端侧确认码 ${code}：`,
        (answer) => {
          rl.close()
          resolve(answer.trim())
        },
      )
    })
    return promise
  }

  const handleNotification = async (
    notification: HeartbeatPayload['notifications'][number],
  ): Promise<void> => {
    if (notification.state !== 'pending_device_code' || !notification.device_code) return
    const answer = await promptDeviceCode(notification.challenge_id, notification.device_code)
    const resp = await post(
      `/api/v1/runner/v2/challenges/${notification.challenge_id}/verify`,
      { device_code: answer },
    )
    if (resp.ok) {
      approvedTasks.add(notification.task_id)
      log(`端侧物理确认通过，任务 ${notification.task_id} 放行`)
    } else {
      log(`端侧物理确认被拒绝 (${resp.status})，任务保持挂起`)
    }
  }

  /**
   * 执行一条物理任务。
   *
   * 幂等保证：每个 step 先向账本登记。`ledger.begin` 返回 null 说明这个
   * (task_id, step_id) 之前已经执行过 —— 此时**只补发回执**，绝不重放动作。
   */
  const runTask = async (pending: HeartbeatPayload['pending_tasks'][number]): Promise<void> => {
    if (stopped || running.has(pending.task_id)) return
    running.add(pending.task_id)
    const task: PhysicalTask = {
      taskId: pending.task_id,
      channel: pending.channel as PhysicalChannel,
      steps: pending.steps,
    }
    let pushed = 0
    let alreadyExecuted = true
    try {
      for (const [index, step] of task.steps.entries()) {
        if (stopped) return
        const stepId = step.step_id || String(index)
        const record = ledger.begin(task.taskId, stepId)
        if (record) {
          alreadyExecuted = false
          const single: PhysicalTask = { ...task, steps: [step] }
          const result = await executePhysicalTask(single, {
            scopes: new Set(config.scopes),
            credentials,
            isTwoFactorApproved: (taskId) => approvedTasks.has(taskId),
            emitFrame: (_t, frame) => {
              if (pushed >= MAX_FRAMES_PER_TASK) return
              pushed += 1
              void pushFrame(task.taskId, {
                kind: frame.kind,
                width: frame.width,
                height: frame.height,
                payload_b64: frame.payloadB64,
              }).catch(() => log('视窗帧上报暂不可用，已停止本次上报'))
            },
          })
          ledger.finish({
            ...record,
            status: result.ok ? 'done' : 'failed',
            ok: result.ok,
            data: result.data ?? {},
            error: result.error,
            finished_at: new Date().toISOString(),
          })
          await reportStep({
            task_id: task.taskId,
            step_id: stepId,
            ok: result.ok,
            data: result.data ?? {},
            error: result.error,
          })
          if (!result.ok) {
            log(`物理任务 ${task.taskId} 未执行：${result.error}`)
            return
          }
        }
      }
      if (alreadyExecuted) {
        log(`物理任务 ${task.taskId} 的全部 step 已有回执，仅补发未确认的记录，不重复执行`)
      } else {
        log(`物理任务 ${task.taskId} 执行完成（${task.channel}）`)
      }
    } catch (err) {
      if (err instanceof ReceiptLedgerFullError) {
        ledgerPaused = true
        log('回执账本已满，暂停领取新物理任务；已有回执继续补发')
      } else {
        // 若异常发生在 ledger.begin() 之后，pending 记录将阻止未知结果的动作重放。
        // 不发送不存在的 step_id="task"：云端不会接受这种合成回执。
        log(`物理任务 ${task.taskId} 处理异常，请核对本地回执账本`)
      }
    } finally {
      running.delete(pending.task_id)
      approvedTasks.delete(pending.task_id)
    }
  }

  const tick = async (): Promise<void> => {
    if (stopped || ticking) return
    ticking = true
    try {
      await tokens.requireFresh()
      if (stopped) return
      await flushPendingReceipts()
      if (stopped) return
      if (ledgerPaused) return
      const resp = await post('/api/v1/runner/v2/runners/heartbeat', {
        runner_id: config.runnerId,
        scopes: config.scopes,
        platform: config.platform,
        version: config.version,
        capabilities: {
          browser: true,
          desktop: Boolean(config.vaultPath) || config.platform !== 'win32',
          frames: { width: FRAME_WIDTH, height: FRAME_HEIGHT, format: 'gray8' },
        },
      })
      if (!resp.ok) {
        log(`心跳被拒绝 (${resp.status})，物理能力保持静默`)
        return
      }
      const payload = (await resp.json()) as { data: HeartbeatPayload }
      if (stopped) return
      for (const notification of payload.data.notifications ?? []) {
        if (stopped) return
        await handleNotification(notification)
      }
      for (const pending of payload.data.pending_tasks ?? []) {
        if (stopped) return
        await runTask(pending)
      }
    } catch (err) {
      log(`心跳失败：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      ticking = false
      if (stopped) releaseLedger()
    }
  }

  const pulse = (): void => {
    if (stopped || ticking) return
    activeTick = tick()
  }
  const timer = setInterval(pulse, HEARTBEAT_INTERVAL_MS)
  pulse()
  log(
    `物理桥接已启动：runner=${config.runnerId} device=${config.deviceId} ` +
      `scopes=${config.scopes.join('|')}`,
  )

  return {
    async stop(): Promise<void> {
      stopped = true
      clearInterval(timer)
      await activeTick
      releaseLedger()
    },
  }
}
