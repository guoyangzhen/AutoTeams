import { useEffect, useState } from 'react'
import { getPendingApprovals } from '@/api/collaboration'
import { listWorkforce } from '@/api/workforce'
import { listFiles } from '@/api/files'
import type { AgentRunMetrics } from '@/types'

type NavigationData = {
  agents: AgentRunMetrics[]
  files: { id: string; original_name: string; file_type: string }[]
  approvalIds: string[]
  approvalsLoading: boolean
  approvalsError: boolean
}
const empty = (loading: boolean): NavigationData => ({
  agents: [], files: [], approvalIds: [], approvalsLoading: loading, approvalsError: false,
})

/** 导航的员工、文件和通知也属于当前登录人和企业，不能沿用上个工作空间的快照。 */
export function useNavigationData(enterpriseId: string, userId: string | null) {
  const key = JSON.stringify([userId, enterpriseId])
  const enabled = !!(userId && enterpriseId)
  const [state, setState] = useState(() => ({ key, ...empty(enabled) }))
  useEffect(() => {
    let active = true
    setState({ key, ...empty(enabled) })
    if (enabled) {
      const update = (patch: Partial<NavigationData>) => {
        if (!active) return
        setState((previous) => active && previous.key === key ? { ...previous, ...patch } : previous)
      }
      void getPendingApprovals(enterpriseId).then(
        (approvals) => update({ approvalIds: approvals.map((item) => item.id), approvalsLoading: false }),
        () => update({ approvalsError: true, approvalsLoading: false }),
      )
      void listWorkforce(enterpriseId, 50, 0).then(
        (response) => update({ agents: response.items }), () => undefined,
      )
      void listFiles({ limit: 50, offset: 0 }).then(
        (response) => update({ files: response.files }), () => undefined,
      )
    }
    return () => { active = false }
  }, [key, enterpriseId, enabled])
  return { ...(state.key === key ? state : empty(enabled)), key }
}
