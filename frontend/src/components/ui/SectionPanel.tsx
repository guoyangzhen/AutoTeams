/**
 * SectionPanel — V3.1 统一区块容器
 *
 * 设计意图：替代各页面中重复的 Card+CardHeader+CardBody 模式，
 * 提供一致的区块视觉节奏（标题 + 描述 + 操作 + 内容）。
 */
import { type ReactNode } from 'react'

interface SectionPanelProps {
  title: string
  description?: string
  icon?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}

export function SectionPanel({
  title,
  description,
  icon,
  action,
  children,
  className = '',
  bodyClassName = '',
}: SectionPanelProps) {
  return (
    <section className={`bg-surface rounded-md border border-border-default overflow-hidden ${className}`}>
      <header className="flex items-center justify-between px-5 py-4 border-b border-border-subtle">
        <div className="flex items-center gap-2.5 min-w-0">
          {icon && <span className="flex-shrink-0 text-brand-500">{icon}</span>}
          <div className="min-w-0">
            <h3 className="text-h4 text-text-primary truncate">{title}</h3>
            {description && (
              <p className="text-body-sm text-text-tertiary mt-0.5 truncate">{description}</p>
            )}
          </div>
        </div>
        {action && <div className="flex-shrink-0">{action}</div>}
      </header>
      <div className={`px-5 py-4 ${bodyClassName}`}>
        {children}
      </div>
    </section>
  )
}
