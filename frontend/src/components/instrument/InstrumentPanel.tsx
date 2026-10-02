import { HTMLAttributes, ReactNode } from 'react'

/**
 * InstrumentPanel — 仪表面板容器（UI v4 §4.2）
 *
 * 深色指挥区的基础容器。与 Card 的区别：
 *   - 深区自动启用内描边高光（模拟金属面板受光）+ 深投影
 *   - 浅区自动退化为普通卡片，视觉与既有 Card 一致
 * 因此同一段页面代码在两个分区都成立，无需分支。
 */
interface InstrumentPanelProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  /** 仪表标签（mono 全大写宽字距，仪表盘美学的正宗表达） */
  label?: string
  /** 标题右侧的操作区 */
  action?: ReactNode
  /** 主标题（衬线，保留品牌权威感） */
  title?: ReactNode
  /** 无内边距（用于图表/画布类内容自行控制留白） */
  flush?: boolean
  /** 入场点亮动画的序号（配合 stagger 使用） */
  igniteIndex?: number
}

export function InstrumentPanel({
  label,
  title,
  action,
  flush = false,
  igniteIndex,
  className = '',
  children,
  style,
  ...props
}: InstrumentPanelProps) {
  const ignite = igniteIndex !== undefined
  return (
    <section
      className={`instrument-panel ${ignite ? 'instrument-ignite' : ''} ${className}`}
      style={ignite ? { animationDelay: `${igniteIndex * 60}ms`, ...style } : style}
      {...props}
    >
      {(label || title || action) && (
        <header
          className={`flex items-start justify-between gap-4 ${flush ? 'px-5 pt-5 pb-3' : 'px-5 pt-5 pb-3'}`}
        >
          <div className="min-w-0">
            {label && <p className="instrument-label mb-1.5">{label}</p>}
            {title && (
              <h3 className="font-serif-display text-h4 text-[var(--text-primary)] truncate">
                {title}
              </h3>
            )}
          </div>
          {action && <div className="flex-shrink-0">{action}</div>}
        </header>
      )}
      <div className={flush ? '' : 'px-5 pb-5'}>{children}</div>
    </section>
  )
}
