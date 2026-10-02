/**
 * AgentsHub — 知识中枢（V3.1 对齐）。
 *
 * 管理企业的知识资产和能力资源：
 * 「文件管理」「技能管理」两个 tab。
 *
 * 已移除「知识图谱」tab（#11：当前无可视化图谱数据，空画布无法呈现），
 * 避免用户进入后看到空白区域。
 */
import { Compass, Database, Cpu } from 'lucide-react'
import { HubPage, type HubTab } from '@/components/HubPage'

const tabs: HubTab[] = [
  {
    key: 'build',
    label: '选择构建方式',
    icon: Compass,
    to: '/agents?tab=build',
    component: () => import('@/pages/KnowledgeBuildPage'),
  },
  {
    key: 'knowledge',
    label: '文件管理',
    icon: Database,
    to: '/agents?tab=knowledge',
    component: () => import('@/pages/KnowledgePage'),
  },
  {
    key: 'skills',
    label: '技能管理',
    icon: Cpu,
    to: '/agents?tab=skills',
    component: () => import('@/pages/SkillManagement'),
  },
]

export default function AgentsHub() {
  return <HubPage defaultTab="build" tabs={tabs} />
}
