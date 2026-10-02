import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowRight } from 'lucide-react'
import { SignalBadge, statusToTone, statusToLabel } from './SignalBadge'
import { formatRelTime, summarizePayload } from '@/utils/format'

/**
 * EventStream — 实时协作事件流（UI v4 §4.2）
 *
 * 修复审计问题：
 *   - 原页面用 JSON.stringify(payload).slice(0,80) 裸露原始 JSON
 *   - CollaborationEvent.status 完全丢弃，看不出失败事件
 *   - target_agent_id 丢弃，看不到「转交给谁」这一协作核心语义
 *
 * 新事件到达时从顶部滑入并闪烁一次，让「正在运转」可感知。
 */

export interface StreamEvent {
  id: string
  event_type: string
  payload?: Record<string, unknown> | null
  source_agent_id?: string | null
  target_agent_id?: string | null
  status?: string | null
  created_at: string
}

interface EventStreamProps {
  events: StreamEvent[]
  /** agent_id → 显示名 */
  agentNames?: Record<string, string>
  /** 事件类型 → 中文标签 */
  typeLabels?: Record<string, string>
  maxHeight?: number
  emptyHint?: string
  className?: string
}

const DEFAULT_TYPE_LABELS: Record<string, string> = {
  inquiry_received: '收到询盘',
  product_query: '产品查询',
  quotation_generated: '生成报价',
  approval_submitted: '提交审批',
  approval_approved: '审批通过',
  order_synced: '订单同步',
  after_sales: '售后跟进',
  handoff: '任务转交',
  escalation: '升级处理',
  error: '异常事件',
}

export function EventStream({
  events,
  agentNames = {},
  typeLabels = {},
  maxHeight = 460,
  emptyHint = '暂无协作事件',
  className = '',
}: EventStreamProps) {
  const labels = { ...DEFAULT_TYPE_LABELS, ...typeLabels }
  // 记录已出现过的事件 id，用于只对「新到达」的事件播放入场高亮
  const seenRef = useRef<Set<string>>(new Set())
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set())

  useEffect(() => {
    const incoming = events.filter((e) => !seenRef.current.has(e.id))
    if (incoming.length === 0) return
    // 首次挂载不算「新事件」，避免整屏闪烁
    const isFirstRender = seenRef.current.size === 0
    events.forEach((e) => seenRef.current.add(e.id))
    if (isFirstRender) return
    const ids = new Set(incoming.map((e) => e.id))
    setFreshIds(ids)
    const timer = setTimeout(() => setFreshIds(new Set()), 1600)
    return () => clearTimeout(timer)
  }, [events])

  if (events.length === 0) {
    return (
      <div className={`flex items-center justify-center py-10 ${className}`}>
        <p className="text-body-sm text-[var(--text-muted)]">{emptyHint}</p>
      </div>
    )
  }

  return (
    <ol
      className={`space-y-2 overflow-y-auto scrollbar-thin pr-1 ${className}`}
      style={{ maxHeight }}
    >
      <AnimatePresence initial={false}>
        {events.map((ev) => {
          const source = ev.source_agent_id ? agentNames[ev.source_agent_id] : null
          const target = ev.target_agent_id ? agentNames[ev.target_agent_id] : null
          const summary = summarizePayload(ev.payload)
          const isFresh = freshIds.has(ev.id)
          return (
            <motion.li
              key={ev.id}
              layout
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              className={`rounded-md border p-3 transition-colors ${
                isFresh
                  ? 'border-[var(--sig-focus,#2563EB)] bg-[rgba(76,141,255,0.08)]'
                  : 'border-[var(--border-subtle)] bg-[var(--bg-elevated)]'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-body-sm font-semibold text-[var(--text-primary)]">
                      {labels[ev.event_type] || ev.event_type}
                    </span>
                    {/* 修复：status 此前完全丢弃，失败事件不可见 */}
                    {ev.status && ev.status !== 'processed' && (
                      <SignalBadge tone={statusToTone(ev.status)}>
                        {statusToLabel(ev.status)}
                      </SignalBadge>
                    )}
                  </div>

                  {/* 协作链路：source → target（此前 target 完全丢弃） */}
                  {(source || target) && (
                    <div className="mt-1 flex items-center gap-1.5 text-caption text-[var(--text-tertiary)]">
                      {source && <span className="truncate">{source}</span>}
                      {source && target && (
                        <ArrowRight className="w-3 h-3 flex-shrink-0 sig-focus" aria-hidden="true" />
                      )}
                      {target && <span className="truncate">{target}</span>}
                    </div>
                  )}

                  {/* 业务摘要：结构化提取而非裸 JSON */}
                  {summary && (
                    <p className="mt-1 text-caption text-[var(--text-secondary)] truncate">
                      {summary}
                    </p>
                  )}
                </div>

                <time
                  className="instrument-readout text-caption text-[var(--text-muted)] flex-shrink-0"
                  dateTime={ev.created_at}
                >
                  {formatRelTime(ev.created_at)}
                </time>
              </div>
            </motion.li>
          )
        })}
      </AnimatePresence>
    </ol>
  )
}
