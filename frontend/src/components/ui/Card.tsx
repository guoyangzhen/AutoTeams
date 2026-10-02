import { HTMLAttributes, KeyboardEvent, MouseEvent, forwardRef } from 'react'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  hover?: boolean
}

/**
 * 基础卡片容器。
 *
 * 可访问性：传入 onClick 时自动补齐键盘可达性（role/tabIndex/Enter/Space + 焦点环），
 * 使卡片型导航入口（SkillCard、WorkforceView 员工卡）对键盘用户可用。
 *
 * 此处刻意使用 div + role="button" 而非渲染真实 <button>：调用方卡片内部
 * 普遍嵌套了「使用」「发起协作」等 Button，button 嵌套属于无效 HTML，
 * 会被浏览器解析时拆散 DOM 结构。
 */
export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ hover = false, className = '', children, onClick, onKeyDown, ...props }, ref) => {
    const interactive = Boolean(onClick)

    const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
      onKeyDown?.(e)
      if (!onClick || e.defaultPrevented) return
      // 仅响应卡片自身的按键，避免内部输入框/按钮的回车被误当作卡片点击
      if (e.target !== e.currentTarget) return
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onClick(e as unknown as MouseEvent<HTMLDivElement>)
      }
    }

    return (
      <div
        ref={ref}
        className={`ui-card rounded-[10px] border border-[#E4E4E1] bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04)] ${
          hover ? 'transition-all duration-200 hover:border-[#1F4FD8]/40 hover:shadow-[0_2px_4px_rgba(0,0,0,0.06)]' : ''
        } ${
          interactive
            ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-canvas)]'
            : ''
        } ${className}`}

        onClick={onClick}
        onKeyDown={handleKeyDown}
        role={interactive ? 'button' : undefined}
        tabIndex={interactive ? 0 : undefined}
        {...props}
      >
        {children}
      </div>
    )
  }
)

Card.displayName = 'Card'

export function CardHeader({ className = '', children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
        <div className={`px-5 py-4.5 border-b border-border-subtle ${className}`} {...props}>

      {children}
    </div>
  )
}

export function CardBody({ className = '', children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
        <div className={`p-5 ${className}`} {...props}>

      {children}
    </div>
  )
}

export function CardFooter({ className = '', children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
        <div className={`px-5 py-4 border-t border-border-subtle ${className}`} {...props}>

      {children}
    </div>
  )
}
