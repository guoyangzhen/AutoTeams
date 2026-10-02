/**
 * editorial — AutoTeams UI v2「克制 · 编辑式」设计令牌与最小原子组件。
 *
 * 依据：docs/AutoTeams-UI重设计蓝图_2026-09-26.md §2 与 autoteams_ui/DESIGN.md §2/§5。
 * 规则要点：
 * - 发丝线 #E4E4E1 是唯一分隔手段；主色 #1F4FD8 全屏 ≤3 处。
 * - 状态 = 6px 圆点 + 13px 文字，禁止彩色填充胶囊。
 * - 正文字号不得小于 12px；圆角 6px（控件）/ 10px（大容器）。
 *
 * 该模块刻意只承载「色值 + 状态点」，排版与栅格仍走 Tailwind 类，
 * 避免与既有 index.css / tailwind.config.js 形成第二套主题体系。
 */
import type { CSSProperties } from 'react'

/** 浅色纸面作用域（08 渠道中枢 / 09 工具生态等）。 */
export const PAPER = {
  /** 纸面底色 */
  canvas: '#FAFAF9',
  /** 卡片面 */
  card: '#FFFFFF',
  /** 分区底 */
  alt: '#F4F4F3',
  /** 发丝线 */
  hair: '#E4E4E1',
  /** 墨色文字 */
  ink: '#0B0B0B',
  /** 次级文字 */
  muted: '#6B6B66',
  /** 弱化文字 */
  subtle: '#A3A29C',
  /** 主色（仅主行动 / 选中 / 关键数字） */
  primary: '#1F4FD8',
  /** 正向（仅状态点与状态文字） */
  success: '#1F7A4D',
  /** 警示 */
  warning: '#9A6212',
  /** 阻断 */
  danger: '#B23A2F',
} as const

/** 深色观测作用域（10 Trace 观测专用）。 */
export const NIGHT = {
  /** 底座 */
  canvas: '#0A0A0A',
  /** 面板 */
  panel: '#0D0D0E',
  /** 内嵌块（引文 / payload） */
  inset: '#141416',
  /** 发丝线 */
  hair: '#232326',
  /** 文字 */
  text: '#EDECEA',
  /** 次级文字 */
  muted: '#8A8A86',
  /** 弱化文字 */
  subtle: '#5C5C58',
  /** 选中描边主色 */
  accent: '#4C7DF0',
  /** 正向 */
  success: '#3E9E6E',
  /** 警示 */
  warning: '#C08A3E',
  /** 阻断 */
  danger: '#C4615A',
} as const

/** 状态语义 → 颜色 + 文案（文案由调用方给出，此处只给点色）。 */
export type StatusTone = 'success' | 'warning' | 'danger' | 'muted' | 'subtle'

/** 状态点颜色（浅色作用域）。 */
export function toneColor(tone: StatusTone, scope: 'paper' | 'night' = 'paper'): string {
  if (scope === 'night') {
    if (tone === 'success') return NIGHT.success
    if (tone === 'warning') return NIGHT.warning
    if (tone === 'danger') return NIGHT.danger
    if (tone === 'muted') return NIGHT.muted
    return NIGHT.subtle
  }
  if (tone === 'success') return PAPER.success
  if (tone === 'warning') return PAPER.warning
  if (tone === 'danger') return PAPER.danger
  if (tone === 'muted') return PAPER.muted
  return PAPER.subtle
}

/** 6px 实心状态圆点。 */
export function Dot({ tone, scope = 'paper' }: { tone: StatusTone; scope?: 'paper' | 'night' }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
      style={{ background: toneColor(tone, scope) }}
    />
  )
}

/** 状态点 + 13px 文字（v2 状态表达的唯一形态）。 */
export function StatusLabel({
  tone,
  children,
  scope = 'paper',
}: {
  tone: StatusTone
  children: React.ReactNode
  scope?: 'paper' | 'night'
}) {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[13px] leading-5"
      style={{ color: scope === 'night' ? NIGHT.text : PAPER.ink }}
    >
      <Dot tone={tone} scope={scope} />
      {children}
    </span>
  )
}

/** 区块标题：17px/600 + 右侧等宽计数说明。 */
export function SectionTitle({
  title,
  meta,
  scope = 'paper',
}: {
  title: string
  meta?: string
  scope?: 'paper' | 'night'
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <h2
        className="text-[17px] font-semibold leading-6"
        style={{ color: scope === 'night' ? NIGHT.text : PAPER.ink }}
      >
        {title}
      </h2>
      {meta && (
        <span
          className="font-mono text-[12px] uppercase tracking-wider"
          style={{ color: scope === 'night' ? NIGHT.subtle : PAPER.subtle }}
        >
          {meta}
        </span>
      )}
    </div>
  )
}

/** 唯一主行动按钮（每屏 ≤1 枚，6px 圆角）。 */
export function PrimaryButton({
  children,
  onClick,
  disabled,
  type = 'button',
  title,
}: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  type?: 'button' | 'submit'
  title?: string
}) {
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 rounded-[6px] px-4 py-2 text-[13px] font-medium text-white transition-colors disabled:cursor-not-allowed disabled:opacity-40"
      style={{ background: PAPER.primary }}
    >
      {children}
    </button>
  )
}

/** 次按钮：白底 + 1px 发丝线 + 墨色字。 */
export function SecondaryButton({
  children,
  onClick,
  disabled,
  type = 'button',
  title,
}: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  type?: 'button' | 'submit'
  title?: string
}) {
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 rounded-[6px] px-3.5 py-2 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40"
      style={{ background: PAPER.card, color: PAPER.ink, border: `1px solid ${PAPER.hair}` }}
    >
      {children}
    </button>
  )
}

/** 文字链（次级操作，不占用按钮预算）。 */
export function TextLink({
  children,
  onClick,
  disabled,
  title,
}: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  title?: string
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="text-[13px] underline-offset-4 transition-colors hover:underline disabled:cursor-not-allowed disabled:opacity-40"
      style={{ color: disabled ? PAPER.subtle : PAPER.muted }}
    >
      {children}
    </button>
  )
}

/** 发丝线容器：白底、1px 发丝线、10px 圆角，无阴影。 */
export function HairlineBox({
  children,
  className = '',
  style,
  scope = 'paper',
}: {
  children: React.ReactNode
  className?: string
  style?: CSSProperties
  scope?: 'paper' | 'night'
}) {
  return (
    <section
      className={`overflow-hidden rounded-[10px] ${className}`}
      style={{
        background: scope === 'night' ? NIGHT.panel : PAPER.card,
        border: `1px solid ${scope === 'night' ? NIGHT.hair : PAPER.hair}`,
        ...style,
      }}
    >
      {children}
    </section>
  )
}

/** 表单字段：12px 灰标签 + 白底发丝线输入（6px 圆角，正文 14px）。 */
export function Field({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
  mono,
  hint,
}: {
  label: string
  value: string
  onChange: (next: string) => void
  placeholder?: string
  type?: 'text' | 'password'
  mono?: boolean
  hint?: string
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12px]" style={{ color: PAPER.muted }}>
        {label}
      </span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full rounded-[6px] px-3 py-2 text-[14px] leading-[22px] outline-none transition-colors focus:border-[#1F4FD8] ${
          mono ? 'font-mono text-[13px]' : ''
        }`}
        style={{ background: PAPER.card, border: `1px solid ${PAPER.hair}`, color: PAPER.ink }}
      />
      {hint && (
        <span className="mt-1 block text-[12px]" style={{ color: PAPER.subtle }}>
          {hint}
        </span>
      )}
    </label>
  )
}

/** 空态：一句说明 + 一个行动，无插画。 */
export function EmptyState({ text }: { text: string }) {
  return (
    <div className="px-6 py-10 text-center text-[13px]" style={{ color: PAPER.muted }}>
      {text}
    </div>
  )
}
