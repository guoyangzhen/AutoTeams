/**
 * BuildHub — 企业构建 hub（编译 + 运行时合并）。
 *
 * 将「企业编译」与「企业运行时」合并为单页双 tab，对应业务流程：
 * 编译生成 Runtime → 切换到运行时 tab 查看组织/协作/流程可视化。
 */
import { GitBranch, Layers } from 'lucide-react'
import { HubPage, type HubTab } from '@/components/HubPage'

// 模块级定义：保证引用稳定，避免 HubPage 内 useMemo 失效导致子树 remount
const tabs: HubTab[] = [
  {
    key: 'compile',
    label: '企业编译',
    icon: GitBranch,
    to: '/build?tab=compile',
    component: () => import('@/pages/CompilePage'),
  },
  {
    key: 'runtime',
    label: '企业运行时',
    icon: Layers,
    to: '/build?tab=runtime',
    component: () => import('@/pages/RuntimeView'),
  },
]

export default function BuildHub() {
  return <HubPage defaultTab="compile" tabs={tabs} />
}
