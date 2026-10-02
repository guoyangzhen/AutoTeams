/**
 * 统一数据展示契约（UI v4 §4.3）
 *
 * 背景：审计发现同一后端字段在不同页面有三种解读 ——
 *   avg_satisfaction 在 WorkforceView 当百分比（×100）、
 *   在 WorkExecutionPage / ShadowModeDemo 当 0-1 评分（toFixed(2)）；
 *   completeness 在 CompilePage 与 AICompanyView 间 0-1 / 0-100 混用。
 *
 * 本模块是所有数值/时间展示的单一真源，页面禁止各自解读。
 */

/** 后端可能返回 null/undefined/NaN，统一兜底 */
function safeNum(value: unknown): number | null {
  if (value === null || value === undefined) return null
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : null
}

/**
 * 比率 → 百分比文本。
 * 自动兼容后端两种尺度：0-1 归一化值与 0-100 百分数。
 * 判定规则：> 1 视为已是百分数（完成度 0.69 与 69 都能正确显示为 69%）。
 */
export function formatRatio(value: unknown, digits = 0): string {
  const n = safeNum(value)
  if (n === null) return '—'
  const pct = n > 1 ? n : n * 100
  return `${pct.toFixed(digits)}%`
}

/** 比率 → 0-100 数值（用于进度条 width、雷达图等需要数字的场景） */
export function toPercentValue(value: unknown): number {
  const n = safeNum(value)
  if (n === null) return 0
  const pct = n > 1 ? n : n * 100
  return Math.max(0, Math.min(100, pct))
}

/**
 * 评分 → x.xx 文本（满意度/置信度等 0-1 区间的质量分）。
 * 与 formatRatio 的区别：评分保留原始尺度，不转百分比。
 */
export function formatScore(value: unknown, digits = 2): string {
  const n = safeNum(value)
  if (n === null) return '—'
  return n.toFixed(digits)
}

/** 整数计数（带千分位） */
export function formatCount(value: unknown): string {
  const n = safeNum(value)
  if (n === null) return '—'
  return Math.round(n).toLocaleString('zh-CN')
}

/**
 * 满意度 → 「x.x 分」文本。
 *
 * 后端 AgentRunMetrics.avg_satisfaction 为 **1-5 分制**（见 backend
 * models/workforce.py），0 表示尚无评分样本。
 *
 * 审计发现多处页面把它当 0-1 比例乘 100 显示，导致 4.6 分被渲染成
 * 「460%」。此函数是该字段的唯一展示入口，不要再自行换算。
 */
export function formatSatisfaction(value: unknown): string {
  const n = safeNum(value)
  // 0 或 null 均视为「无样本」，诚实显示占位符而非 0 分
  if (n === null || n <= 0) return '—'
  return `${n.toFixed(1)} 分`
}

/** 毫秒 → 人类可读耗时 */
export function formatDuration(ms: unknown): string {
  const n = safeNum(ms)
  if (n === null || n < 0) return '—'
  if (n < 1000) return `${Math.round(n)} ms`
  const sec = n / 1000
  if (sec < 60) return `${sec.toFixed(1)} 秒`
  const min = sec / 60
  if (min < 60) return `${min.toFixed(1)} 分钟`
  const hr = min / 60
  if (hr < 24) return `${hr.toFixed(1)} 小时`
  return `${(hr / 24).toFixed(1)} 天`
}

/** 两个 ISO 时间戳之间的耗时（编译任务 started_at → completed_at） */
export function formatElapsed(start?: string | null, end?: string | null): string {
  if (!start) return '—'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  if (!Number.isFinite(s) || !Number.isFinite(e)) return '—'
  return formatDuration(e - s)
}

/** ISO 时间戳 → 相对时间（「3 分钟前」） */
export function formatRelTime(iso?: string | null): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return '—'
  const diff = Date.now() - t
  if (diff < 0) return '刚刚'
  const sec = diff / 1000
  if (sec < 45) return '刚刚'
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`
  const hr = sec / 3600
  if (hr < 24) return `${Math.floor(hr)} 小时前`
  const day = hr / 24
  if (day < 30) return `${Math.floor(day)} 天前`
  return new Date(iso).toLocaleDateString('zh-CN')
}

/** ISO 时间戳 → 本地时间（HH:mm） */
export function formatClock(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

/** ISO 时间戳 → 本地日期时间 */
export function formatDateTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/** 金额 → 人民币文本（万元自动折算） */
export function formatAmount(value: unknown): string {
  const n = safeNum(value)
  if (n === null) return '—'
  if (Math.abs(n) >= 10000) return `¥${(n / 10000).toFixed(1)} 万`
  return `¥${n.toLocaleString('zh-CN')}`
}

/**
 * 向量检索距离 → 相关度百分比。
 * 修复审计问题：原 Chat 页用 `1 - distance` 线性映射，对余弦距离不严谨
 * （余弦距离范围 0-2，L2 距离无上界）。改用有界的倒数衰减映射。
 */
export function formatRelevance(distance: unknown): string {
  const d = safeNum(distance)
  if (d === null || d < 0) return '—'
  return `${Math.round(toRelevanceValue(d))}%`
}

/**
 * 向量检索距离 → 0-100 相关度数值（进度条宽度等需要数字的场景）。
 * 与 formatRelevance 共用同一映射，避免条形长度与文本读数对不上。
 * 距离缺失时返回 0（不画条），而不是假装 50%。
 */
export function toRelevanceValue(distance: unknown): number {
  const d = safeNum(distance)
  if (d === null || d < 0) return 0
  return Math.max(0, Math.min(100, (1 / (1 + d)) * 100))
}

/** 安全渲染任意 payload：优先取业务可读字段，避免裸露 JSON */
export function summarizePayload(
  payload: Record<string, unknown> | null | undefined,
  preferredKeys: string[] = ['action', 'customer', 'product', 'query', 'feedback', 'status'],
): string {
  if (!payload || typeof payload !== 'object') return ''
  const parts: string[] = []
  for (const key of preferredKeys) {
    const v = payload[key]
    if (v !== undefined && v !== null && v !== '') {
      parts.push(String(v))
    }
    if (parts.length >= 3) break
  }
  return parts.join(' · ')
}
