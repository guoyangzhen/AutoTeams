import { motion } from 'framer-motion'
import { Check, AlertTriangle, Loader2, Circle } from 'lucide-react'
import type { ReactNode } from 'react'

/**
 * StateMachineTrack — 通用状态机可视化轨道（UI v4 §4.2）
 *
 * 后端有多套状态机，此前前端全部渲染为静态 Tab/卡片，
 * 状态「跃迁」这一核心语义完全丢失。本组件统一表达：
 *   - 影子模式：shadowing → evaluating → qualified → autonomous（+2 条降级边）
 *   - 生命周期：recruit → training → production
 *   - 五级编译：information → knowledge → process → capability → runtime
 *   - 审批门：  pending → approved / rejected
 *
 * L3 高光时刻：状态跃迁时滑块沿轨道物理位移 + 目标节点辉光爆闪。
 * 降级（回退）用反向红色动画，视觉上有「跌落感」。
 */

export type TrackNodeStatus = 'done' | 'active' | 'pending' | 'failed'

export interface TrackNode {
  key: string
  label: string
  /** 副标题：阶段说明或实时读数 */
  caption?: string
  status: TrackNodeStatus
  /** 该节点的量化指标（如置信度 0-1），驱动进度环 */
  score?: number | null
  /** 节点计数（如该状态下的任务数） */
  count?: number
  /** 失败原因（failed 时展示，修复审计发现的「失败态无信息」问题） */
  error?: string | null
}

interface StateMachineTrackProps {
  nodes: TrackNode[]
  /** 轨道方向 */
  orientation?: 'horizontal' | 'vertical'
  /** 点击节点 */
  onSelect?: (key: string) => void
  /** 当前选中节点 */
  selectedKey?: string
  /** 是否发生了降级（反向跃迁），触发红色回退动画 */
  demoted?: boolean
  /** 节点右侧的自定义渲染 */
  renderExtra?: (node: TrackNode) => ReactNode
  className?: string
}

const STATUS_STYLE: Record<TrackNodeStatus, { ring: string; dot: string; text: string }> = {
  done: {
    ring: 'border-[var(--sig-alive,#16A34A)]',
    dot: 'sig-bg-alive sig-glow-alive',
    text: 'sig-alive',
  },
  active: {
    ring: 'border-[var(--sig-focus,#2563EB)]',
    dot: 'sig-bg-focus sig-glow-focus',
    text: 'sig-focus',
  },
  pending: {
    ring: 'border-[var(--border-default)]',
    dot: 'sig-bg-idle',
    text: 'text-[var(--text-muted)]',
  },
  failed: {
    ring: 'border-[var(--sig-fault,#DC2626)]',
    dot: 'sig-bg-fault sig-glow-fault',
    text: 'sig-fault',
  },
}

function StatusIcon({ status }: { status: TrackNodeStatus }) {
  const cls = 'w-3.5 h-3.5 text-white'
  if (status === 'done') return <Check className={cls} strokeWidth={3} aria-hidden="true" />
  if (status === 'active') return <Loader2 className={`${cls} animate-spin`} aria-hidden="true" />
  if (status === 'failed') return <AlertTriangle className={cls} strokeWidth={2.5} aria-hidden="true" />
  return <Circle className="w-2 h-2 text-white/60" aria-hidden="true" />
}

export function StateMachineTrack({
  nodes,
  orientation = 'horizontal',
  onSelect,
  selectedKey,
  demoted = false,
  renderExtra,
  className = '',
}: StateMachineTrackProps) {
  if (nodes.length === 0) return null

  const activeIdx = Math.max(
    0,
    nodes.findIndex((n) => n.status === 'active'),
  )
  const doneCount = nodes.filter((n) => n.status === 'done').length
  // 进度：已完成节点 + 当前活跃节点的半程
  const progress =
    nodes.length <= 1
      ? 0
      : Math.min(100, ((doneCount + (nodes[activeIdx]?.status === 'active' ? 0.5 : 0)) / nodes.length) * 100)

  if (orientation === 'vertical') {
    return (
      <ol className={`relative ${className}`}>
        {/* 竖向轨道底线 */}
        <div
          className="absolute left-[15px] top-3 bottom-3 w-px bg-[var(--border-default)]"
          aria-hidden="true"
        />
        <motion.div
          className="absolute left-[15px] top-3 w-px sig-bg-focus"
          initial={{ height: 0 }}
          animate={{ height: `${progress}%` }}
          transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
          aria-hidden="true"
        />
        {nodes.map((node, i) => {
          const s = STATUS_STYLE[node.status]
          const selected = selectedKey === node.key
          return (
            <li key={node.key} className="relative pl-11 pb-5 last:pb-0">
              <motion.span
                className={`absolute left-[7px] top-0.5 w-4 h-4 rounded-full flex items-center justify-center ${s.dot}`}
                initial={{ scale: 0.6, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ delay: i * 0.06, duration: 0.3 }}
              >
                <StatusIcon status={node.status} />
              </motion.span>
              <button
                type="button"
                onClick={onSelect ? () => onSelect(node.key) : undefined}
                disabled={!onSelect}
                className={`w-full text-left rounded-md transition-colors ${
                  onSelect ? 'hover:bg-[var(--bg-elevated)] px-2 py-1 -mx-2' : ''
                } ${selected ? 'bg-[var(--bg-elevated)]' : ''}`}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`text-body-sm font-semibold ${s.text}`}>{node.label}</span>
                  {typeof node.count === 'number' && (
                    <span className="instrument-readout text-caption text-[var(--text-muted)]">
                      {node.count}
                    </span>
                  )}
                  {typeof node.score === 'number' && (
                    <span className="instrument-readout text-caption text-[var(--text-tertiary)]">
                      {(node.score * 100).toFixed(0)}%
                    </span>
                  )}
                </div>
                {node.caption && (
                  <p className="mt-0.5 text-caption text-[var(--text-tertiary)] leading-relaxed">
                    {node.caption}
                  </p>
                )}
                {node.status === 'failed' && node.error && (
                  <p className="mt-1 text-caption sig-fault leading-relaxed">{node.error}</p>
                )}
              </button>
              {renderExtra?.(node)}
            </li>
          )
        })}
      </ol>
    )
  }

  // 横向轨道
  return (
    <div className={className}>
      <div className="relative">
        {/* 轨道底线 */}
        <div
          className="absolute left-0 right-0 top-[15px] h-px bg-[var(--border-default)]"
          aria-hidden="true"
        />
        {/* 进度滑块：状态跃迁时物理位移（L3 高光） */}
        <motion.div
          className={`absolute left-0 top-[15px] h-px ${demoted ? 'sig-bg-fault' : 'sig-bg-focus'}`}
          initial={{ width: 0 }}
          animate={{ width: `${progress}%` }}
          transition={{
            duration: demoted ? 0.5 : 0.8,
            ease: demoted ? [0.4, 0, 1, 1] : [0.22, 1, 0.36, 1],
          }}
          aria-hidden="true"
        />
        <ol className="relative flex justify-between gap-2">
          {nodes.map((node, i) => {
            const s = STATUS_STYLE[node.status]
            const selected = selectedKey === node.key
            return (
              <li key={node.key} className="flex-1 min-w-0 flex flex-col items-center text-center">
                <motion.button
                  type="button"
                  onClick={onSelect ? () => onSelect(node.key) : undefined}
                  disabled={!onSelect}
                  className={`w-8 h-8 rounded-full flex items-center justify-center border-2 bg-[var(--bg-canvas)] ${s.ring} ${
                    onSelect ? 'cursor-pointer' : 'cursor-default'
                  }`}
                  initial={{ scale: 0.5, opacity: 0 }}
                  animate={{
                    scale: 1,
                    opacity: 1,
                    // 活跃节点持续轻微呼吸，强化「正在此处」
                    ...(node.status === 'active' ? { boxShadow: '0 0 0 6px rgba(76,141,255,0.10)' } : {}),
                  }}
                  transition={{ delay: i * 0.07, duration: 0.34, ease: [0.22, 1, 0.36, 1] }}
                  aria-current={node.status === 'active' ? 'step' : undefined}
                >
                  <span
                    className={`w-5 h-5 rounded-full flex items-center justify-center ${s.dot}`}
                  >
                    <StatusIcon status={node.status} />
                  </span>
                </motion.button>
                <p className={`mt-2 text-caption font-semibold truncate max-w-full ${s.text}`}>
                  {node.label}
                </p>
                {typeof node.count === 'number' && (
                  <p className="instrument-readout text-body-sm font-semibold text-[var(--text-primary)]">
                    {node.count}
                  </p>
                )}
                {node.caption && (
                  <p className="text-caption text-[var(--text-muted)] leading-snug line-clamp-2">
                    {node.caption}
                  </p>
                )}
                {node.status === 'failed' && node.error && (
                  <p className="mt-1 text-caption sig-fault leading-snug">{node.error}</p>
                )}
                {selected && renderExtra?.(node)}
              </li>
            )
          })}
        </ol>
      </div>
    </div>
  )
}
