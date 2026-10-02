/**
 * SubTabBar — hub 页面内的子视图标签栏。
 *
 * 用于页面合并场景（P5.2）：一个 hub 页面聚合多个子页面，
 * 通过顶部标签栏切换，避免侧边栏入口过多。
 *
 * 设计：
 * - 标签使用 URL search params 同步（?tab=xxx），支持深链/刷新保持
 * - 横向滚动适配窄屏
 * - 选中态高亮 + 底部指示条
 */
import { Link } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'

export interface SubTabItem {
  /** tab 标识，对应 ?tab=xxx */
  key: string
  label: string
  icon: LucideIcon
  /** 完整路由路径（含 search params），用于 Link to */
  to: string
}

interface SubTabBarProps {
  tabs: SubTabItem[]
  activeKey: string
}

export function SubTabBar({ tabs, activeKey }: SubTabBarProps) {
  return (
        <div className="sticky top-16 z-20 -mx-4 sm:-mx-6 px-4 sm:px-6 pt-1 pb-4 bg-[var(--shell-header)] backdrop-blur-md">
      <div className="ui-tab-rail">

        {tabs.map((tab) => {
          const Icon = tab.icon
          const active = tab.key === activeKey
          return (
            <Link
              key={tab.key}
              to={tab.to}
                            aria-current={active ? 'page' : undefined}
              className={`ui-tab-item inline-flex items-center gap-2 px-3.5 text-sm font-medium ${
                active
                  ? 'ui-tab-item--active'
                  : 'text-text-secondary hover:text-text-primary hover:bg-white/60'
              }`}

            >
              <Icon className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
                            {tab.label}
            </Link>

          )
        })}
            </div>
    </div>

  )
}

export default SubTabBar
