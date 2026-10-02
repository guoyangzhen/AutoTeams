/**
 * 具身物理执行面（Local Runner 2.0）
 *
 * 在用户本机执行两条通道的物理操作：
 * - `browser_action`：无头 Chromium 导航 / 元素点击 / 表格录入（Playwright）
 * - `desktop_accessibility`：系统辅助功能树读取与受控输入模拟（UIAutomation 适配器）
 *
 * 安全边界（与云端 `backend/app/services/runner_v2_protocol.py` 的 Tri-Rule 同构）：
 * - 端侧在下发链路上再执行一次护栏，云端被绕过或协议降级时本机仍然阻断；
 * - 严禁明文凭据：凭据字段只能由本机凭据机按 `credential_ref` / `credential_refs` 代填；
 * - 高危操作必须先拿到云端下发的双因子放行标记，端侧才会继续；
 * - 视窗只以「降采样无损 8bit 灰度帧」形式外传。
 *
 * 驱动缺失时返回明确的 `driver_unavailable` 错误码，绝不以模拟数据冒充真实操作结果。
 */
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

/** 视窗流降采样尺寸契约（与云端 FRAME_WIDTH/FRAME_HEIGHT 一致）。 */
export const FRAME_WIDTH = 160
export const FRAME_HEIGHT = 100

/** 允许的浏览器协议（与云端规则三一致）。 */
const ALLOWED_URL_SCHEMES: Record<string, true> = { 'http:': true, 'https:': true }

/** 凭据类字段标签（命中即要求凭据机代填）。 */
const CREDENTIAL_FIELD_PATTERN =
  /(password|passwd|pwd|pass_?code|密码|口令|验证码|校验码|动态码|短信码|otp|mfa|2fa|token|secret|api[_\-\s]?key|access[_\-\s]?key|private[_\-\s]?key|私钥|credential|凭据)/i

/** 正文中的凭据赋值片段。 */
const SECRET_ASSIGNMENT_PATTERN =
  /(password|passwd|pwd|口令|密码|token|secret|api[_\-\s]?key|私钥|credential)\s*[:=]\s*["']?[^\s"',;]{4,}/i

/** 高熵裸串。 */
const HIGH_ENTROPY_PATTERN = /^[A-Za-z0-9+/=_-]{24,}$/

/** 高危操作词表（与云端规则二一致）。 */
const HIGH_RISK_PATTERN =
  /(转账|汇款|打款|付款|支付|确认支付|立即支付|结算|清分|提现|批量删除|全部删除|清空|销毁|不可撤销|下单|提交订单|签约|签署|授权书|绑定卡|transfer|wire\s*transfer|payment|pay\b|remittance|settle|withdraw|bulk[_\-\s]?delete|delete[_\-\s]?all|purge|irreversible)/i

/** 只读操作：不改变任何物理状态。 */
const READ_ONLY_OPS: Record<string, true> = {
  navigate: true,
  read_tree: true,
  screenshot: true,
  focus: true,
}

/** 变更型操作：需要 physical 作用域 + 双因子放行标记。 */
const MUTATING_OPS: Record<string, true> = {
  click: true,
  fill_table: true,
  input_text: true,
  press_keys: true,
  invoke: true,
}

/** 两条通道的操作白名单（静态字面量表，非动态集合）。 */
const CHANNEL_OPS: Record<PhysicalChannel, Record<string, true>> = {
  browser_action: {
    navigate: true,
    click: true,
    fill_table: true,
    screenshot: true,
    press_keys: true,
  },
  desktop_accessibility: {
    read_tree: true,
    click: true,
    input_text: true,
    invoke: true,
    focus: true,
  },
}

export type PhysicalChannel = 'browser_action' | 'desktop_accessibility'

export type GuardAction = 'allow' | 'block' | 'require_2fa'

export interface PhysicalStep {
  op: string
  target?: string
  text?: string
  /** 整条 step 级别的凭据机引用（命中单个凭据字段时使用）。 */
  credential_ref?: string
  /** 表格列级凭据机引用：列名 → 凭据机路径（表格批量录入使用）。 */
  credential_refs?: Record<string, string>
  rows?: Array<Record<string, string>>
  keys?: string[]
  url?: string
  label?: string
  risk?: 'normal' | 'high'
  /** 云端生成的稳定 step 标识：端侧回执账本按 (task_id, step_id) 去重（AUD-18）。 */
  step_id?: string
}

export interface PhysicalTask {
  taskId: string
  channel: PhysicalChannel
  steps: PhysicalStep[]
}

export interface GuardrailVerdict {
  action: GuardAction
  rule: string
  reason: string
  stepIndex?: number
}

/** 视窗灰度帧（与云端 ViewportFrame 契约一致）。 */
export interface ViewportFrame {
  kind: 'keyframe' | 'delta'
  width: number
  height: number
  payloadB64: string
  // 线上字段名是 `payload_b64`（snake_case，与后端 Pydantic 模型一致）；
  // 这里的 `payloadB64` 只是端侧内部类型名，physical-bridge 负责转换。
  digest: string
}

export interface PhysicalResult {
  ok: boolean
  data?: Record<string, unknown>
  error?: string
  guardrail?: GuardrailVerdict
  frames?: ViewportFrame[]
}

/** 凭据机：把 `vault://a/b` 引用解析为本机明文，仅存在于内存中。 */
export interface CredentialStore {
  resolve(ref: string): string | null
}

/** 端侧执行上下文。 */
export interface PhysicalContext {
  /** 已授予的作用域（read / read_write / physical）。 */
  scopes: Set<string>
  /** 凭据机（缺省时 credential_ref 解析失败即阻断）。 */
  credentials: CredentialStore
  /** 高危任务的双因子放行判定；缺省视为未放行。 */
  isTwoFactorApproved(taskId: string): boolean
  /** 视窗帧回调（每步执行后推送）。 */
  emitFrame?(task: PhysicalTask, frame: ViewportFrame): void
}

// ============================================================
// 护栏（端侧镜像实现，纯函数）
// ============================================================

/** 该 step 命中的凭据类字段名（表格列名 + 选择器/标签）。 */
function credentialFields(step: PhysicalStep): string[] {
  const candidates: string[] = []
  for (const value of [step.target, step.label]) {
    if (typeof value === 'string') candidates.push(value)
  }
  for (const row of step.rows ?? []) {
    if (row && typeof row === 'object') candidates.push(...Object.keys(row))
  }
  return candidates.filter((candidate) => CREDENTIAL_FIELD_PATTERN.test(candidate))
}

/** 摊平 step 中的可录入正文（含表格「列名=取值」形态）。 */
function entryText(step: PhysicalStep): string {
  const chunks: string[] = []
  if (typeof step.text === 'string') chunks.push(step.text)
  for (const row of step.rows ?? []) {
    if (!row || typeof row !== 'object') continue
    for (const [column, value] of Object.entries(row)) chunks.push(`${column}=${String(value)}`)
  }
  return chunks.join(' ').trim()
}

/** 明文凭据证据；命中即必须阻断。 */
function literalSecretEvidence(step: PhysicalStep): string | null {
  const text = entryText(step)
  if (!text) return null
  const assignment = SECRET_ASSIGNMENT_PATTERN.exec(text)
  if (assignment) return `正文出现凭据赋值片段（${assignment[1]}）`
  if (HIGH_ENTROPY_PATTERN.test(text)) return '正文出现高熵疑似密钥串'
  return null
}

/** 缺少凭据机引用的凭据字段（空数组表示代填链路完整）。 */
function unresolvedCredentialFields(step: PhysicalStep): string[] {
  const fields = credentialFields(step)
  if (fields.length === 0) return []
  const tableColumns = new Set(
    (step.rows ?? []).flatMap((row) => (row && typeof row === 'object' ? Object.keys(row) : [])),
  )
  // 命中的是选择器/标签而非表格列时，step 级 credential_ref 即为代填声明。
  if (fields.every((field) => !tableColumns.has(field))) {
    return String(step.credential_ref ?? '').trim() ? [] : fields
  }
  return fields.filter((field) => {
    if (!tableColumns.has(field)) return String(step.credential_ref ?? '').trim() === ''
    return !String(step.credential_refs?.[field] ?? '').trim() && String(step.credential_ref ?? '').trim() === ''
  })
}

/**
 * 端侧 Tri-Rule 护栏：与云端同构的第二道物理沙箱。
 * 高危任务即使已被云端放行，本机仍需双因子放行标记才会真正执行。
 */
export function evaluatePhysicalTask(
  task: PhysicalTask,
  ctx: Pick<PhysicalContext, 'scopes'>,
): GuardrailVerdict {
  const allowed = CHANNEL_OPS[task.channel]
  if (!allowed) {
    return { action: 'block', rule: 'tri_rule_channel_surface', reason: `未知物理通道: ${String(task.channel)}` }
  }
  if (!Array.isArray(task.steps) || task.steps.length === 0) {
    return { action: 'block', rule: 'tri_rule_invalid', reason: '物理任务必须包含至少一个 step' }
  }

  const highRisk: number[] = []

  for (let index = 0; index < task.steps.length; index += 1) {
    const step = task.steps[index]
    const op = String(step.op ?? '')
    if (!op) {
      return { action: 'block', rule: 'tri_rule_invalid', reason: `step[${index}] 缺少 op`, stepIndex: index }
    }
    if (!allowed[op]) {
      return {
        action: 'block',
        rule: 'tri_rule_channel_surface',
        reason: `通道 ${task.channel} 不支持操作 ${op}`,
        stepIndex: index,
      }
    }
    if (MUTATING_OPS[op] && !ctx.scopes.has('physical')) {
      return {
        action: 'block',
        rule: 'tri_rule_scope',
        reason: `本机未授予 physical 作用域，禁止执行变更型操作 ${op}`,
        stepIndex: index,
      }
    }

    const rawUrl = String(step.url ?? '').trim()
    if (op === 'navigate' && !rawUrl) {
      return { action: 'block', rule: 'tri_rule_navigation', reason: 'navigate 必须提供 url', stepIndex: index }
    }
    if (rawUrl) {
      let scheme: string
      try {
        scheme = new URL(rawUrl).protocol
      } catch {
        return { action: 'block', rule: 'tri_rule_navigation', reason: 'url 非法，禁止跳转', stepIndex: index }
      }
      if (!ALLOWED_URL_SCHEMES[scheme]) {
        return {
          action: 'block',
          rule: 'tri_rule_navigation',
          reason: `禁止导航到非 http(s) 目标: ${rawUrl}`,
          stepIndex: index,
        }
      }
    }

    const literal = literalSecretEvidence(step)
    if (literal) {
      return {
        action: 'block',
        rule: 'tri_rule_credential_autofill',
        reason: `${literal}；凭据必须由本机凭据机按 credential_ref 代填`,
        stepIndex: index,
      }
    }
    const unresolved = unresolvedCredentialFields(step)
    if (unresolved.length > 0) {
      return {
        action: 'block',
        rule: 'tri_rule_credential_autofill',
        reason: `凭据字段 ${unresolved.join(', ')} 缺少凭据机代填引用`,
        stepIndex: index,
      }
    }

    if (step.risk === 'high') {
      highRisk.push(index)
    } else if (!READ_ONLY_OPS[op]) {
      const haystack = [step.target, step.label, step.text, step.url].join(' ')
      if (HIGH_RISK_PATTERN.test(haystack)) highRisk.push(index)
    }
    if (highRisk.length > 0) {
      return {
        action: 'require_2fa',
        rule: 'tri_rule_high_risk_two_factor',
        reason: `命中高危操作（step 索引 ${highRisk.join(',')}），需云端双因子确认放行`,
        stepIndex: highRisk[0],
      }
    }
  }

  return { action: 'allow', rule: 'tri_rule_pass', reason: '端侧三重护栏通过' }
}

// ============================================================
// 凭据机
// ============================================================

/**
 * 文件型凭据机：读取本机 JSON 凭据库（`vault://erp/prod/password` 对应
 * `{"erp":{"prod":{"password":"…"}}}`）。该文件只应存在于用户本机并受 OS 权限保护，
 * Runner 永远不会把解析出的明文写回云端。
 */
export function fileCredentialStore(path: string): CredentialStore {
  return {
    resolve(ref: string): string | null {
      if (!ref.startsWith('vault://')) return null
      const segments = ref.slice('vault://'.length).split('/').filter(Boolean)
      if (segments.length === 0) return null
      let cursor: unknown
      try {
        cursor = JSON.parse(readFileSync(path, 'utf8'))
      } catch {
        return null
      }
      for (const segment of segments) {
        if (cursor === null || typeof cursor !== 'object') return null
        cursor = (cursor as Record<string, unknown>)[segment]
      }
      return typeof cursor === 'string' ? cursor : null
    },
  }
}

/** 解析单个字段的凭据；解析失败即抛错，绝不回退到明文或占位值。 */
function resolveFieldCredential(
  step: PhysicalStep,
  field: string | undefined,
  ctx: PhysicalContext,
  literal = '',
): string {
  if (field === undefined || !CREDENTIAL_FIELD_PATTERN.test(field)) {
    return literal || String(step.text ?? '')
  }
  const ref = String(step.credential_refs?.[field] ?? step.credential_ref ?? '').trim()
  if (!ref) {
    throw new PhysicalDriverError('credential_unavailable', `字段 ${field} 缺少凭据机代填引用`)
  }
  const secret = ctx.credentials.resolve(ref)
  if (secret === null) {
    throw new PhysicalDriverError('credential_unavailable', `本机凭据机无法解析引用: ${ref}`)
  }
  return secret
}

// ============================================================
// 驱动错误
// ============================================================

export class PhysicalDriverError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'PhysicalDriverError'
    this.code = code
  }
}

// ============================================================
// 视窗帧
// ============================================================

/** 编码为降采样无损 8bit 灰度帧（面积平均，无色彩量化损失）。 */
export function encodeGrayscaleFrame(
  rgba: Uint8ClampedArray,
  sourceWidth: number,
  sourceHeight: number,
  lastDigest?: string,
): ViewportFrame {
  const payload = Buffer.alloc(FRAME_WIDTH * FRAME_HEIGHT)
  for (let y = 0; y < FRAME_HEIGHT; y += 1) {
    const y0 = Math.floor((y * sourceHeight) / FRAME_HEIGHT)
    const y1 = Math.max(y0 + 1, Math.floor(((y + 1) * sourceHeight) / FRAME_HEIGHT))
    for (let x = 0; x < FRAME_WIDTH; x += 1) {
      const x0 = Math.floor((x * sourceWidth) / FRAME_WIDTH)
      const x1 = Math.max(x0 + 1, Math.floor(((x + 1) * sourceWidth) / FRAME_WIDTH))
      let sum = 0
      let count = 0
      for (let sy = y0; sy < y1 && sy < sourceHeight; sy += 1) {
        for (let sx = x0; sx < x1 && sx < sourceWidth; sx += 1) {
          const offset = (sy * sourceWidth + sx) * 4
          const alpha = rgba[offset + 3] / 255
          const luminance = 0.299 * rgba[offset] + 0.587 * rgba[offset + 1] + 0.114 * rgba[offset + 2]
          sum += luminance * alpha + 255 * (1 - alpha)
          count += 1
        }
      }
      payload[y * FRAME_WIDTH + x] = count > 0 ? Math.round(sum / count) : 0
    }
  }
  const digest = createHash('sha256').update(payload).digest('hex').slice(0, 32)
  return {
    kind: lastDigest && lastDigest === digest ? 'delta' : 'keyframe',
    width: FRAME_WIDTH,
    height: FRAME_HEIGHT,
    payloadB64: payload.toString('base64'),
    digest,
  }
}

// ============================================================
// 驱动（端侧按需安装，缺失即报 driver_unavailable）
// ============================================================

/** 无头浏览器会话所需的最小 Playwright 表面。 */
interface BrowserSession {
  goto(url: string): Promise<void>
  click(selector: string): Promise<void>
  fill(selector: string, value: string): Promise<void>
  press(selector: string | null, keys: string): Promise<void>
  screenshotRgba(): Promise<{ data: Uint8ClampedArray; width: number; height: number }>
  close(): Promise<void>
}

/** PNG 解码所需的最小 sharp 表面。 */
interface SharpPipeline {
  ensureAlpha(): {
    raw(): {
      toBuffer(options: { resolveWithObject: true }): Promise<{
        data: Buffer
        info: { width: number; height: number }
      }>
    }
  }
}
type SharpFactory = (input: Buffer) => SharpPipeline

// playwright / sharp 是端侧按需安装的可选驱动（不写入 package.json 依赖），
// 静态 import 会在未安装时直接让守护进程无法启动；因此这里用运行期惰性加载，
// 加载失败立即以 driver_unavailable 报错，绝不静默降级。
async function loadOptionalDriver<T>(specifier: string): Promise<T | null> {
  try {
    return (await import(specifier)) as T
  } catch {
    return null
  }
}

let browserSession: BrowserSession | null = null
let sharpFactory: SharpFactory | null = null

/** 惰性启动无头 Chromium。 */
async function ensureBrowserSession(): Promise<BrowserSession> {
  if (browserSession) return browserSession

  const playwright = await loadOptionalDriver<{ chromium: { launch(options: { headless: boolean }): Promise<Browser> } }>(
    'playwright',
  )
  if (!playwright) {
    throw new PhysicalDriverError(
      'driver_unavailable',
      '未安装 Playwright：请在 local-runner 目录执行 npm i playwright && npx playwright install chromium',
    )
  }

  const browser = await playwright.chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const page = await context.newPage()

  browserSession = {
    async goto(url: string) {
      await page.goto(url, { waitUntil: 'domcontentloaded' })
    },
    async click(selector: string) {
      await page.click(selector)
    },
    async fill(selector: string, value: string) {
      await page.fill(selector, value)
    },
    async press(selector: string | null, keys: string) {
      if (selector) await page.press(selector, keys)
      else await page.keyboard.press(keys)
    },
    async screenshotRgba() {
      const shot: Buffer = await page.screenshot({ type: 'png' })
      if (!sharpFactory) {
        const sharp = await loadOptionalDriver<{ default: SharpFactory }>('sharp')
        if (!sharp) {
          throw new PhysicalDriverError(
            'driver_unavailable',
            '未安装 sharp：视窗灰度帧编码需要 PNG 解码能力，请执行 npm i sharp',
          )
        }
        sharpFactory = sharp.default
      }
      const { data, info } = await sharpFactory(shot).ensureAlpha().raw().toBuffer({ resolveWithObject: true })
      return { data: new Uint8ClampedArray(data), width: info.width, height: info.height }
    },
    async close() {
      await browser.close()
      browserSession = null
    },
  }
  return browserSession
}

/** Playwright Browser/Page 的最小结构（仅本地驱动使用）。 */
interface Browser {
  newContext(options: { viewport: { width: number; height: number } }): Promise<{
    newPage(): Promise<Page>
  }>
  close(): Promise<void>
}
interface Page {
  goto(url: string, options: { waitUntil: 'domcontentloaded' }): Promise<unknown>
  click(selector: string): Promise<void>
  fill(selector: string, value: string): Promise<void>
  press(selector: string, keys: string): Promise<void>
  keyboard: { press(keys: string): Promise<void> }
  screenshot(options: { type: 'png' }): Promise<Buffer>
}

/** 关闭无头浏览器会话（进程退出 / 重连时调用）。 */
export async function disposePhysicalSession(): Promise<void> {
  if (browserSession) await browserSession.close()
}

// ============================================================
// 桌面辅助功能适配器
// ============================================================

/** 桌面辅助功能适配器：读取 UIAutomation / nut.js 节点树并受控输入。 */
export interface DesktopAdapter {
  readTree(target?: string): Promise<Array<Record<string, unknown>>>
  focus(target: string): Promise<void>
  click(target: string): Promise<void>
  inputText(target: string, value: string): Promise<void>
  invoke(target: string): Promise<void>
  captureFrame(): Promise<{ data: Uint8ClampedArray; width: number; height: number } | null>
}

let desktopAdapter: DesktopAdapter | null = null

/** 注入桌面适配器；未注入时桌面物理操作明确报 driver_unavailable。 */
export function configureDesktopAdapter(adapter: DesktopAdapter | null): void {
  desktopAdapter = adapter
}

// ============================================================
// 执行面
// ============================================================

/**
 * 执行一条具身物理任务。
 *
 * 流程：端侧护栏复核 → 双因子放行判定 → 逐步骤执行 → 视窗灰度帧回传。
 * 任一环节失败都返回 `ok: false` 与稳定的机器可读错误码，绝不返回伪造结果。
 */
export async function executePhysicalTask(
  task: PhysicalTask,
  ctx: PhysicalContext,
): Promise<PhysicalResult> {
  const verdict = evaluatePhysicalTask(task, ctx)
  if (verdict.action === 'block') {
    return { ok: false, error: `${verdict.rule}: ${verdict.reason}`, guardrail: verdict }
  }
  if (verdict.action === 'require_2fa' && !ctx.isTwoFactorApproved(task.taskId)) {
    return {
      ok: false,
      error: `${verdict.rule}: ${verdict.reason}（端侧尚未收到双因子放行标记）`,
      guardrail: verdict,
    }
  }

  const frames: ViewportFrame[] = []
  const trace: Array<{ op: string; target?: string; status: string }> = []
  let lastDigest: string | undefined

  const capture = async (): Promise<void> => {
    let raw: { data: Uint8ClampedArray; width: number; height: number } | null = null
    if (task.channel === 'browser_action') {
      raw = await (await ensureBrowserSession()).screenshotRgba()
    } else if (desktopAdapter) {
      raw = await desktopAdapter.captureFrame()
    }
    if (!raw) return
    const frame = encodeGrayscaleFrame(raw.data, raw.width, raw.height, lastDigest)
    lastDigest = frame.digest
    frames.push(frame)
    ctx.emitFrame?.(task, frame)
  }

  try {
    if (task.channel === 'browser_action') {
      const session = await ensureBrowserSession()
      for (const step of task.steps) {
        switch (step.op) {
          case 'navigate':
            await session.goto(String(step.url))
            break
          case 'click':
            await session.click(requireStepTarget(step))
            break
          case 'fill_table':
            await fillTable(session, step, ctx)
            break
          case 'press_keys':
            for (const key of step.keys ?? []) await session.press(step.target ?? null, key)
            break
          case 'screenshot':
            break
          default:
            throw new PhysicalDriverError('unsupported_op', `不支持的浏览器操作: ${step.op}`)
        }
        trace.push({ op: step.op, target: step.target, status: 'ok' })
        await capture()
      }
    } else {
      if (!desktopAdapter) {
        throw new PhysicalDriverError(
          'driver_unavailable',
          '未注册桌面辅助功能适配器（UIAutomation / nut.js），无法执行 desktop_accessibility 任务',
        )
      }
      const adapter = desktopAdapter
      for (const step of task.steps) {
        switch (step.op) {
          case 'read_tree': {
            const nodes = await adapter.readTree(step.target)
            trace.push({ op: step.op, target: step.target, status: 'ok' })
            return { ok: true, data: { type: 'read_tree', nodes }, frames }
          }
          case 'focus':
            await adapter.focus(requireStepTarget(step))
            break
          case 'click':
            await adapter.click(requireStepTarget(step))
            break
          case 'input_text':
            await adapter.inputText(requireStepTarget(step), resolveFieldCredential(step, step.target, ctx))
            break
          case 'invoke':
            await adapter.invoke(requireStepTarget(step))
            break
          default:
            throw new PhysicalDriverError('unsupported_op', `不支持的桌面操作: ${step.op}`)
        }
        trace.push({ op: step.op, target: step.target, status: 'ok' })
        await capture()
      }
    }
  } catch (err) {
    const code = err instanceof PhysicalDriverError ? err.code : 'physical_execution_failed'
    const message = err instanceof Error ? err.message : String(err)
    return { ok: false, error: `${code}: ${message}`, guardrail: verdict, frames }
  }

  return {
    ok: true,
    data: { type: 'physical_execution', trace, stepCount: task.steps.length },
    frames,
  }
}

/** 定位目标缺失即报错（避免把空选择器交给驱动）。 */
function requireStepTarget(step: PhysicalStep): string {
  const target = String(step.target ?? '').trim()
  if (!target) {
    throw new PhysicalDriverError('invalid_step', `操作 ${step.op} 缺少 target`)
  }
  return target
}

/** 表格批量录入：逐行逐列写入，命中凭据列时改由本机凭据机代填。 */
async function fillTable(
  session: BrowserSession,
  step: PhysicalStep,
  ctx: PhysicalContext,
): Promise<void> {
  const rows = step.rows ?? []
  if (rows.length === 0) {
    throw new PhysicalDriverError('invalid_step', 'fill_table 需要至少一行 rows')
  }
  const rowTemplate = step.target ?? 'tbody tr:nth-of-type(%d)'
  for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
    const columns = Object.keys(rows[rowIndex])
    for (let columnPosition = 0; columnPosition < columns.length; columnPosition += 1) {
      const column = columns[columnPosition]
      const cellSelector = `${rowTemplate.replace('%d', String(rowIndex + 1))} td:nth-of-type(${
        columnPosition + 1
      })`
      const value = resolveFieldCredential(step, column, ctx, String(rows[rowIndex][column]))
      await session.fill(cellSelector, value)
    }
  }
}
