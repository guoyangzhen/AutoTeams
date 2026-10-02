import type { ReactNode } from 'react'

/**
 * SignalBadge — 信号色状态徽章（UI v4 §4.2）
 *
 * 全站状态语义的统一出口。此前各页面对同一状态各写各的颜色，
 * 导致 processed/completed/approved 在不同页面呈现不同色彩。
 * 本组件把后端所有状态枚举收敛到 5 个信号语义。
 */

export type SignalTone = 'alive' | 'focus' | 'alert' | 'fault' | 'idle'

interface SignalBadgeProps {
  tone: SignalTone
  children: ReactNode
  /** 呼吸动画：用于「进行中」的活体感 */
  pulse?: boolean
  /** 尺寸 */
  size?: 'sm' | 'md'
  /** 前置图标 */
  icon?: ReactNode
  className?: string
}

const TONE_STYLE: Record<SignalTone, { text: string; bg: string; dot: string }> = {
  alive: { text: 'sig-alive', bg: 'bg-[rgba(77,216,192,0.12)]', dot: 'sig-bg-alive' },
  focus: { text: 'sig-focus', bg: 'bg-[rgba(76,141,255,0.12)]', dot: 'sig-bg-focus' },
  alert: { text: 'sig-alert', bg: 'bg-[rgba(245,165,36,0.12)]', dot: 'sig-bg-alert' },
  fault: { text: 'sig-fault', bg: 'bg-[rgba(255,93,93,0.12)]', dot: 'sig-bg-fault' },
  idle: { text: 'sig-idle', bg: 'bg-[var(--bg-elevated)]', dot: 'sig-bg-idle' },
}

export function SignalBadge({
  tone,
  children,
  pulse = false,
  size = 'sm',
  icon,
  className = '',
}: SignalBadgeProps) {
  const s = TONE_STYLE[tone]
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full font-semibold ${s.bg} ${s.text} ${
        size === 'sm' ? 'px-2 py-0.5 text-caption' : 'px-2.5 py-1 text-body-sm'
      } ${className}`}
    >
      {icon ?? (
        <span
          className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${s.dot} ${pulse ? 'sig-breathe' : ''}`}
          aria-hidden="true"
        />
      )}
      {children}
    </span>
  )
}

/**
 * 后端状态枚举 → 信号语义的单一映射源。
 * 覆盖 compiler / shadow / collaboration / workforce / evolution 全部状态机。
 */
export function statusToTone(status?: string | null): SignalTone {
  switch (status) {
    // 完成/健康/自主
    case 'completed':
    case 'processed':
    case 'approved':
    case 'applied':
    case 'production':
    case 'autonomous':
    case 'ready':
    case 'active':
    case 'match':
      return 'alive'
    // 进行中
    case 'running':
    case 'processing':
    case 'training':
    case 'evaluating':
    case 'qualified':
      return 'focus'
    // 待办/需注意
    case 'pending':
    case 'paused':
    case 'recruit':
    case 'shadowing':
    case 'draft':
      return 'alert'
    // 失败/拒绝
    case 'failed':
    case 'rejected':
    case 'error':
    case 'mismatch':
    case 'cancelled':
      return 'fault'
    default:
      return 'idle'
  }
}

/** 后端状态枚举 → 中文标签的单一映射源 */
export function statusToLabel(status?: string | null): string {
  const map: Record<string, string> = {
    completed: '已完成',
    processed: '已处理',
    approved: '已批准',
    applied: '已应用',
    running: '进行中',
    processing: '处理中',
    pending: '待处理',
    paused: '已暂停',
    failed: '失败',
    rejected: '已拒绝',
    cancelled: '已取消',
    error: '异常',
    ready: '就绪',
    active: '运行中',
    // 生命周期
    recruit: '招聘中',
    training: '培训中',
    production: '已上岗',
    // 影子模式
    shadowing: '影子观察',
    evaluating: '评估中',
    qualified: '待授权',
    autonomous: '自主运行',
    match: '一致',
    mismatch: '分歧',
    draft: '草稿',
  }
  return map[status || ''] || status || '未知'
}
