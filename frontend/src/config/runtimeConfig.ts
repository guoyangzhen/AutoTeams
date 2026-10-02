/**
 * AUD-27：运行时配置。
 *
 * 问题：`import.meta.env.VITE_*` 在 `vite build` 时被字面量内联进产物。Docker
 * Compose 的 `environment:` 只作用于**容器进程**，无法改写已经编译好的 JS，
 * 因此「换域名 / 换 API 主机」必须重新构建镜像，运维上不可行。
 *
 * 方案：应用启动时拉取同源 `config.json`。容器入口脚本（docker-entrypoint.d）
 * 用容器环境变量生成该文件；非容器部署可由运维直接放置/替换该文件。
 *
 * 优先级：运行时 config.json → 构建时 VITE_* → 同源默认 `/api/v1`。
 *
 * 注意：加载失败（文件缺失、非 JSON、超时）一律回退到下一优先级，绝不阻塞启动。
 */

/** 运行时可覆盖的字段。全部可选，缺省即表示「不覆盖」。 */
export interface RuntimeConfig {
  /** API 基础地址，例如 `https://api.example.com/api/v1` 或 `/api/v1`。 */
  apiBaseUrl?: string
  /** 协作服务 WebSocket 地址；缺省时按当前页面 origin 推导 `/collab-ws`。 */
  collabWsUrl?: string
  /** 事件上报地址；缺省时复用 apiBaseUrl。 */
  analyticsUrl?: string
}

const CONFIG_PATH = '/config.json'
/** 配置文件必须「取不到就立刻降级」，超时后走构建时值，不让首屏卡住。 */
const CONFIG_TIMEOUT_MS = 3000
export const DEFAULT_API_BASE_URL = '/api/v1'

let resolved: RuntimeConfig = {}
let loaded = false
let inflight: Promise<RuntimeConfig> | null = null

function readString(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const trimmed = value.trim()
  if (!trimmed) return undefined
  return trimmed
}

/** 去掉结尾斜杠，避免与以 `/` 开头的相对路径拼接出双斜杠。 */
function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, '')
}

async function fetchRuntimeConfig(signal: AbortSignal): Promise<RuntimeConfig> {
  const response = await fetch(CONFIG_PATH, {
    cache: 'no-store',
    credentials: 'same-origin',
    signal,
  })
  if (!response.ok) {
    throw new Error(`config.json HTTP ${response.status}`)
  }
  const raw: unknown = await response.json()
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('config.json 不是对象')
  }
  const source = raw as Record<string, unknown>
  const config: RuntimeConfig = {}
  const apiBaseUrl = readString(source.apiBaseUrl)
  if (apiBaseUrl) config.apiBaseUrl = normalizeBaseUrl(apiBaseUrl)
  const collabWsUrl = readString(source.collabWsUrl)
  if (collabWsUrl) config.collabWsUrl = collabWsUrl
  const analyticsUrl = readString(source.analyticsUrl)
  if (analyticsUrl) config.analyticsUrl = normalizeBaseUrl(analyticsUrl)
  return config
}

/**
 * 加载运行时配置。幂等：重复调用共享同一个 in-flight Promise。
 * 永不 reject —— 任何失败都退化为「没有运行时覆盖」。
 */
export function loadRuntimeConfig(): Promise<RuntimeConfig> {
  if (inflight) return inflight
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), CONFIG_TIMEOUT_MS)
  inflight = fetchRuntimeConfig(controller.signal)
    .catch(() => ({}) as RuntimeConfig)
    .then((config) => {
      resolved = config
      loaded = true
      return resolved
    })
    .finally(() => {
      clearTimeout(timer)
    })
  return inflight
}

/** 供测试使用：清空已加载状态，避免用例之间互相污染。 */
export function resetRuntimeConfig(): void {
  resolved = {}
  loaded = false
  inflight = null
}

export function isRuntimeConfigLoaded(): boolean {
  return loaded
}

/** 同步读取当前生效的 API 基础地址（配置未加载完成时返回构建时值/默认值）。 */
export function getApiBaseUrl(): string {
  return resolved.apiBaseUrl || import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL
}

/** 同步读取事件上报地址；未单独配置时复用 API 基础地址。 */
export function getAnalyticsUrl(): string {
  return resolved.analyticsUrl || getApiBaseUrl()
}

/**
 * 协作服务 WebSocket 地址。
 * 运行时显式配置优先；否则沿用同源推导（`wss://<当前域名>/collab-ws`），
 * 这与 nginx.conf 的 `/collab-ws` 反向代理约定一致。
 */
export function getCollabWsUrl(): string {
  if (resolved.collabWsUrl) return resolved.collabWsUrl
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/collab-ws`
}
