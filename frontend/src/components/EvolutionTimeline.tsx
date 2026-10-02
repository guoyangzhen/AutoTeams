/**
 * EvolutionTimeline — 进化历程单列时间线（AutoTeams 编辑式）。
 *
 * 对应 autoteams_ui/DESIGN.md §5「状态机日志」：单列、6px 圆点、
 * 12px 灰时间、14px 内容、节点名等宽，条目之间仅用 1px 发丝线分隔，
 * 不用描边卡片包裹、不用彩色填充胶囊。
 *
 * 数据来源：GET /evolution/{enterprise_id}/timeline
 * 事件类型取值域对齐后端 backend/app/api/evolution.py 的聚合逻辑：
 *   suggestion_generated / suggestion_applied / suggestion_rejected
 *   metric_recorded / optimization_generated / optimization_applied
 * 后端为 str 类型，可能新增值，未知类型归入「系统事件」而非报错。
 */
import { Clock, ArrowUpRight } from 'lucide-react'
import type { EvolutionTimelineItem } from '@/types'

interface EvolutionTimelineProps {
  items: EvolutionTimelineItem[]
  /** 空状态提示文案 */
  emptyText?: string
  /** 真实 Agent ID → 名称映射（用于将 details 中的 agent_id 解析为可读的员工姓名） */
  agentNameMap?: Record<string, string>
}

type EventTone = 'positive' | 'blocked' | 'caution' | 'idle'

/** 事件类型 → 标签 + 状态点色（对齐后端实际 event_type 值域） */
const EVENT_LABEL: Record<string, string> = {
  suggestion_generated: '优化建议',
  suggestion_applied: '建议已采纳',
  suggestion_rejected: '建议已驳回',
  metric_recorded: '指标记录',
  optimization_generated: '能力优化',
  optimization_applied: '优化生效',
  compile: '编译',
  data_change: '数据变更',
  agent_sync: '员工同步',
  rollback: '回滚',
}

const EVENT_TONE: Record<string, EventTone> = {
  suggestion_generated: 'caution',
  suggestion_applied: 'positive',
  optimization_applied: 'positive',
  suggestion_rejected: 'blocked',
  rollback: 'blocked',
  metric_recorded: 'idle',
  optimization_generated: 'idle',
  compile: 'idle',
  data_change: 'idle',
  agent_sync: 'idle',
}

const DOT_CLASS: Record<EventTone, string> = {
  positive: 'bg-at-positive',
  blocked: 'bg-at-blocked',
  caution: 'bg-at-caution',
  idle: 'bg-at-hairline',
}

const DEFAULT_LABEL = '系统事件'

function formatRelative(ts: string): string {
  try {
    const then = new Date(ts).getTime()
    if (!Number.isFinite(then)) return ts
    const diffHours = (Date.now() - then) / (1000 * 60 * 60)
    if (diffHours < 1) return '刚刚'
    if (diffHours < 24) return `${Math.floor(diffHours)} 小时前`
    if (diffHours < 48) return '昨天'
    if (diffHours < 24 * 7) return `${Math.floor(diffHours / 24)} 天前`
    return new Date(then).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  } catch {
    return ts
  }
}

function formatAbsolute(ts: string): string {
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ts
  }
}

/** details 键 → 中文标签 */
const DETAIL_LABEL: Record<string, string> = {
  stage: '编译阶段',
  affected_stages: '影响阶段',
  agent_count: '员工数',
  process_count: '流程数',
  node_count: '节点数',
  edge_count: '关系数',
  skill_count: '技能数',
  confidence: '置信度',
  completeness: '完成度',
  trigger: '触发原因',
  category: '分类',
  priority: '优先级',
  impact: '影响范围',
  improvement: '改进项',
  before: '变更前',
  after: '变更后',
  delta: '变化量',
  reason: '原因',
  level: '成熟度',
  name: '等级',
  dimensions: '维度',
  agent_id: '员工',
  type: '类型',
  suggestion_id: '建议',
}

/** 冗余 / 低信息量字段：跳过，避免污染时间线可读性 */
const DETAIL_SKIP: Record<string, true> = { description: true, achieved: true }

/** 从 details 中提取变更详情标签 */
function extractChangeTags(
  details: Record<string, unknown> | undefined,
  agentNameMap?: Record<string, string>,
): { label: string; value: string }[] {
  if (!details) return []
  const tags: { label: string; value: string }[] = []

  for (const [key, value] of Object.entries(details)) {
    if (DETAIL_SKIP[key]) continue
    const label = DETAIL_LABEL[key] || key
    let val: string
    if (typeof value === 'number') {
      val =
        key.includes('confidence') || key.includes('completeness')
          ? `${Math.round(value * 100)}%`
          : String(value)
    } else if (typeof value === 'string') {
      // agent_id 优先解析为员工姓名，其次显示短 ID（避免暴露冗长 UUID）
      val =
        key === 'agent_id'
          ? agentNameMap?.[value] || value.slice(0, 13)
          : value.length > 50
            ? `${value.substring(0, 50)}…`
            : value
    } else if (Array.isArray(value)) {
      val = `${value.length} 项`
    } else if (value && typeof value === 'object') {
      val = `${Object.keys(value).length} 项`
    } else {
      val = String(value ?? '')
    }
    if (val) tags.push({ label, value: val })
  }

  return tags.slice(0, 4)
}

/** 优化生效类事件附加「能力提升」提示（唯一使用上箭头的场景） */
const TREND_EVENTS: Record<string, true> = {
  suggestion_applied: true,
  optimization_applied: true,
}

export function EvolutionTimeline({
  items,
  emptyText = '暂无进化记录',
  agentNameMap,
}: EvolutionTimelineProps) {
  if (items.length === 0) {
    return (
      <div className="border-t border-at-hairline py-10 text-center">
        <Clock className="mx-auto mb-2 h-4 w-4 text-at-subtle" aria-hidden="true" />
        <p className="text-sm text-at-muted">{emptyText}</p>
      </div>
    )
  }

  return (
    <ol className="border-t border-at-hairline">
      {items.map((item, index) => {
        const tone = EVENT_TONE[item.event_type] ?? 'idle'
        const title = item.title || item.summary || item.event_type
        const description = item.description || item.summary || ''
        const tags = extractChangeTags(item.details, agentNameMap)
        const showTrend = TREND_EVENTS[item.event_type] === true

        return (
          <li
            key={item.id || `${item.timestamp}-${index}`}
            className="flex gap-4 border-b border-at-hairline py-3"
          >
            {/* 6px 状态点 + 左侧竖直发丝线，构成单列时间轴 */}
            <div className="flex w-1.5 shrink-0 justify-center pt-1.5" aria-hidden="true">
              <span className={`w-1.5 h-1.5 rounded-full ${DOT_CLASS[tone]}`} />
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-3">
                <span className="font-mono text-xs text-at-subtle">
                  {EVENT_LABEL[item.event_type] ?? DEFAULT_LABEL}
                </span>
                <time
                  className="text-xs text-at-subtle"
                  title={formatAbsolute(item.timestamp)}
                  dateTime={item.timestamp}
                >
                  {formatRelative(item.timestamp)}
                </time>
                {showTrend && (
                  <span className="inline-flex items-center gap-1 text-xs text-at-positive">
                    <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
                    能力提升
                  </span>
                )}
              </div>

              <p className="mt-0.5 text-sm leading-5 text-at-ink">{title}</p>
              {description && description !== title && (
                <p className="mt-0.5 text-[13px] leading-5 text-at-muted">{description}</p>
              )}

              {tags.length > 0 && (
                <p className="mt-1 text-xs text-at-subtle">
                  {tags.map((t) => `${t.label} ${t.value}`).join(' · ')}
                </p>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export default EvolutionTimeline
