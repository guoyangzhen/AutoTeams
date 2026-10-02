/**
 * EmptyState — 优雅空态组件。
 *
 * 用于 API 无数据场景，绝不造假数据。
 * 结构：圆形衬底大图标 + serif 标题 + 说明 + CTA 按钮（可选）。
 * 引导用户触发后端流程（去编译/去上传/去生成员工）。
 *
 * 对标 prototype 的"缺数据引导"设计语言，达到苹果级精致度。
 */
import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'

type Variant = 'default' | 'brand' | 'success' | 'warning' | 'info'

interface EmptyStateAction {
  /** CTA 按钮文案 */
  label: string
  /** 跳转路由（与 onClick 二选一） */
  to?: string
  /** 点击回调（与 to 二选一） */
  onClick?: () => void
}

interface EmptyStateProps {
  /** Lucide 图标组件 */
  icon: React.ElementType
  /** 标题（serif 字体） */
  title: string
  /** 描述说明 */
  description?: string
  /** 主 CTA 动作 */
  action?: EmptyStateAction
  /** 次要 CTA 动作（可选） */
  secondaryAction?: EmptyStateAction
  /** 视觉变体（默认 brand） */
  variant?: Variant
  /** 额外内容（如示意图、提示卡） */
  children?: ReactNode
  className?: string
}


export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  secondaryAction,
  variant: _variant = 'brand',
  children,
  className = '',
}: EmptyStateProps) {
  const renderAction = (act: EmptyStateAction, primary: boolean) => {
    const baseClass = primary
      ? 'inline-flex items-center gap-1.5 px-4 py-2 text-[13px] font-medium rounded-[6px] bg-[#1F4FD8] text-white hover:bg-[#1a44be] transition-colors shadow-xs'
      : 'inline-flex items-center gap-1.5 px-4 py-2 text-[13px] font-medium rounded-[6px] border border-[#E4E4E1] bg-white text-[#0B0B0B] hover:bg-[#FAFAF9] transition-colors'
    const content = (
      <>
        {act.label}
        <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" />
      </>
    )
    if (act.to) {
      return (
        <Link key={act.label} to={act.to} className={baseClass}>
          {content}
        </Link>
      )
    }
    return (
      <button key={act.label} type="button" onClick={act.onClick} className={baseClass}>
        {content}
      </button>
    )
  }

  return (
    <div
      className={`flex flex-col items-center justify-center text-center py-16 px-6 ${className}`}
    >
      <div className="w-12 h-12 rounded-[10px] bg-[#F4F4F3] border border-[#E4E4E1] flex items-center justify-center text-[#6B6B66] mb-4">
        <Icon className="w-5 h-5 text-[#6B6B66]" aria-hidden="true" />
      </div>
      <h3 className="text-[17px] font-semibold text-[#0B0B0B] mb-2 tracking-tight">
        {title}
      </h3>
      {description && (
        <p className="text-[13px] text-[#6B6B66] max-w-md leading-relaxed mb-6">
          {description}
        </p>
      )}
      {children}
      {(action || secondaryAction) && (
        <div className="flex items-center gap-3 flex-wrap justify-center mt-2">
          {action && renderAction(action, true)}
          {secondaryAction && renderAction(secondaryAction, false)}
        </div>
      )}
    </div>
  )
}

export default EmptyState
