/**
 * OverviewHub — 实时运转总览容器。
 *
 * 进入「实时运转」导航后，仅呈现三个 Tab：实时运转 / 运营概览 / 业务效果。
 * 三个 Tab 均复用 AICompanyView（其内部按 ?tab= 切换对应内容区块），
 * 由 HubPage 顶部的 SubTabBar 统一承载切换，避免重复的内联 Tab 栏。
 */
import { Activity, LayoutDashboard, TrendingUp } from 'lucide-react'
import { HubPage, type HubTab } from '@/components/HubPage'

// 模块级定义：保证引用稳定，避免 HubPage 内 useMemo 失效导致子树 remount
const tabs: HubTab[] = [
  {
    key: 'runtime',
    label: '实时运转',
    icon: Activity,
    to: '/company',
    component: () => import('@/pages/AICompanyView'),
  },
  {
    key: 'overview',
    label: '运营概览',
    icon: LayoutDashboard,
    to: '/company?tab=overview',
    component: () => import('@/pages/AICompanyView'),
  },
  {
    key: 'business',
    label: '业务效果',
    icon: TrendingUp,
    to: '/company?tab=business',
    component: () => import('@/pages/AICompanyView'),
  },
]

export default function OverviewHub() {
  return <HubPage defaultTab="runtime" tabs={tabs} />
}