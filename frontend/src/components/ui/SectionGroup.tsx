/**
 * SectionGroup — 折叠分组组件。
 *
 * 对标 prototype 的 details > summary 折叠模式（如编译缺失项建议）。
 * 用于将同类重复元素分组折叠，解决"endless scrolling"痛点：
 * - KnowledgePage 文件按类型分组
 * - WorkforceView 员工按部门分组
 * - EvolutionPage 建议按类型分组
 *
 * 使用原生 <details>/<summary> 保证可访问性 + chev 旋转动画。
 */
import { type ReactNode, useState, useEffect } from 'react'
import { ChevronRight } from 'lucide-react'

interface SectionGroupProps {
  /** 分组标题 */
  title: string
  /** 标题左侧图标（可选） */
  icon?: ReactNode
  /** 右侧徽标（如计数 badge） */
  badge?: ReactNode
  /** 右侧操作区（如筛选、排序按钮） */
  action?: ReactNode
  /** 描述说明（标题下方） */
  description?: string
  /** 是否默认展开（默认 true） */
  defaultOpen?: boolean
  /** 受控展开状态（可选） */
  open?: boolean
  /** 展开状态变更回调 */
  onOpenChange?: (open: boolean) => void
  children: ReactNode
  className?: string
}

export function SectionGroup({
  title,
  icon,
  badge,
  action,
  description,
  defaultOpen = true,
  open: controlledOpen,
  onOpenChange,
  children,
  className = '',
}: SectionGroupProps) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen)
  const isOpen = controlledOpen !== undefined ? controlledOpen : internalOpen

  useEffect(() => {
    if (controlledOpen !== undefined) {
      setInternalOpen(controlledOpen)
    }
  }, [controlledOpen])

  const handleToggle = () => {
    const next = !isOpen
    if (controlledOpen === undefined) {
      setInternalOpen(next)
    }
    onOpenChange?.(next)
  }

  return (
    <section
      className={`bg-surface border border-border-default rounded-xl overflow-hidden ${className}`}
    >
      <header
        className="flex items-center gap-2.5 px-5 py-4 hover:bg-elevated/40 transition-colors cursor-pointer select-none"
        onClick={handleToggle}
        role="button"
        tabIndex={0}
        aria-expanded={isOpen}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            handleToggle()
          }
        }}
      >
        <ChevronRight
          className={`w-4 h-4 text-text-muted flex-shrink-0 transition-transform duration-200 ${
            isOpen ? 'rotate-90' : ''
          }`}
          aria-hidden="true"
        />
        {icon && <span className="flex-shrink-0 text-brand-500">{icon}</span>}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-text-primary truncate">{title}</h3>
            {badge}
          </div>
          {description && (
            <p className="text-xs text-text-tertiary mt-0.5 truncate">{description}</p>
          )}
        </div>
        {action && (
          <div
            className="flex-shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            {action}
          </div>
        )}
      </header>
      {isOpen && (
        <div className="border-t border-border-subtle">{children}</div>
      )}
    </section>
  )
}

export default SectionGroup
