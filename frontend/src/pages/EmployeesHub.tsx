/**
 * EmployeesHub — AI 员工 hub（员工团队 + 模板库合并）。
 *
 * 将「AI 员工」与「模板库」合并为单页双 tab：
 * 员工团队查看在岗数字员工；模板库浏览可复用的岗位模板。
 */
import { Users, LayoutTemplate, Building2 } from 'lucide-react'
import { HubPage, type HubTab } from '@/components/HubPage'

const tabs: HubTab[] = [
  {
    key: 'workforce',
    label: '员工团队',
    icon: Users,
    to: '/employees?tab=workforce',
    component: () => import('@/pages/WorkforceView'),
  },
  {
    key: 'templates',
    label: '模板库',
    icon: LayoutTemplate,
    to: '/employees?tab=templates',
    component: () => import('@/pages/TemplateGallery'),
  },
  {
    key: 'organization',
    label: '组织架构',
    icon: Building2,
    to: '/employees?tab=organization',
    component: () => import('@/pages/OrganizationChart'),
  },
]

export default function EmployeesHub() {
  return <HubPage defaultTab="workforce" tabs={tabs} />
}
