/**
 * InlineTabs — 页内 Tab 切换组件。
 *
 * 区别于 SubTabBar（页面级路由 Tab，用 Link 跳转），
 * InlineTabs 用于同一页面内同级模块切换，纯状态驱动。
 *
 * 对标 prototype 的 inline-flex bg-elevated 圆角分段控件 +
 * 底部指示条两种风格，默认用底部指示条（与 SubTabBar 视觉一致）。
 *
 * 用于：
 * - AICompanyView 任务流/协作流程图/协作关系图切换
 * - CompilePage 编译阶段时间线/横向流程图切换
 * - RuntimeView 协作图/组织架构/数字员工/业务流程切换
 * - KnowledgePage 类型分布/知识图谱切换
 * - WorkforceView 员工详情抽屉内概况/配置/历史切换
 */
import { type ReactNode } from 'react'
import { type LucideIcon } from 'lucide-react'

export interface InlineTabItem {
  key: string
  label: string
  icon?: LucideIcon
  /** 右上角徽标（如计数、红点） */
  badge?: ReactNode
  /** 禁用 */
  disabled?: boolean
}

interface InlineTabsProps {
  tabs: InlineTabItem[]
  activeKey: string
  onChange: (key: string) => void
  /** 变体：underline 底部指示条（默认）| segment 分段控件 */
  variant?: 'underline' | 'segment'
  className?: string
}

export function InlineTabs({
  tabs,
  activeKey,
  onChange,
  variant = 'underline',
  className = '',
}: InlineTabsProps) {
  if (variant === 'segment') {
    // 分段控件风格（对标 prototype 老板模式切换、近7天/30天切换）
    return (
      <div
        className={`inline-flex bg-elevated rounded-md p-0.5 ${className}`}
        role="tablist"
      >
        {tabs.map((tab) => {
          const Icon = tab.icon
          const active = tab.key === activeKey
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={active}
              disabled={tab.disabled}
              onClick={() => !tab.disabled && onChange(tab.key)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded transition-colors ${
                active
                  ? 'bg-surface text-brand-500 shadow-soft'
                  : 'text-text-tertiary hover:text-text-secondary'
              } disabled:opacity-40 disabled:cursor-not-allowed`}
            >
              {Icon && <Icon className="w-3.5 h-3.5" aria-hidden="true" />}
              {tab.label}
              {tab.badge}
            </button>
          )
        })}
      </div>
    )
  }

  // 底部指示条风格（默认，与 SubTabBar 视觉一致）
  return (
    <div
      className={`flex items-center gap-1 border-b border-border-default overflow-x-auto scrollbar-thin ${className}`}
      role="tablist"
    >
      {tabs.map((tab) => {
        const Icon = tab.icon
        const active = tab.key === activeKey
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={tab.disabled}
            onClick={() => !tab.disabled && onChange(tab.key)}
            className={`relative inline-flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${
              active
                ? 'text-brand-500'
                : 'text-text-tertiary hover:text-text-secondary'
            } disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            {Icon && <Icon className="w-4 h-4 flex-shrink-0" aria-hidden="true" />}
            {tab.label}
            {tab.badge}
            {active && (
              <span className="absolute left-2 right-2 bottom-0 h-0.5 bg-brand-500 rounded-full" />
            )}
          </button>
        )
      })}
    </div>
  )
}

export default InlineTabs
