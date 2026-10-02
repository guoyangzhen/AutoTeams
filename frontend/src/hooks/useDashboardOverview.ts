import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { listWorkforceProfiles } from '@/api/workforceProfiles'
import * as collaborationApi from '@/api/collaboration'
import * as evolutionApi from '@/api/evolution'
import * as runtimeApi from '@/api/runtime'
import { getBusinessMetrics, type DailyCountPoint } from '@/api/metrics'
import type { ApprovalRequest, OrgMetrics, RuntimeCompileResult } from '@/types'

const TREND_DAYS = 14

type Scope = { enterpriseId: string; active: boolean; generation: number; request: number; resolved: Set<string> }
type Overview = {
  scope: Scope
  orgMetrics: OrgMetrics | null
  runtime: RuntimeCompileResult | null
  approvals: ApprovalRequest[] | null
  trend: DailyCountPoint[]
  trendAvailable: boolean
  rosterCount: number | null
  deciding: string | null
}

function empty(scope: Scope): Overview {
  return { scope, orgMetrics: null, runtime: null, approvals: null, trend: [], trendAvailable: true, rosterCount: null, deciding: null }
}

export function useDashboardOverview(enterpriseId: string, refreshLive: () => void) {
  const scopeRef = useRef<Scope | null>(null)
  if (scopeRef.current?.enterpriseId !== enterpriseId) {
    if (scopeRef.current) scopeRef.current.active = false
    scopeRef.current = { enterpriseId, active: true, generation: 0, request: 0, resolved: new Set() }
  }
  const scope = scopeRef.current
  const refreshLiveRef = useRef(refreshLive)
  refreshLiveRef.current = refreshLive
  const [stored, setStored] = useState<Overview>(() => empty(scope))
  // A render with a new enterprise must never expose the previous enterprise's state.
  const overview = stored.scope === scope ? stored : empty(scope)

  const load = useCallback(async (target: Scope) => {
    if (!target.enterpriseId || !target.active || scopeRef.current !== target) return
    const request = ++target.request
    const [orgRes, rtRes, apprRes, trendRes, rosterRes] = await Promise.allSettled([
      evolutionApi.getOrgMetrics(target.enterpriseId),
      runtimeApi.getRuntime(target.enterpriseId),
      collaborationApi.getPendingApprovals(target.enterpriseId),
      getBusinessMetrics(TREND_DAYS),
      listWorkforceProfiles(),
    ])
    if (!target.active || scopeRef.current !== target || target.request !== request) return
    setStored((previous) => ({
      ...empty(target),
      deciding: previous.scope === target ? previous.deciding : null,
      orgMetrics: orgRes.status === 'fulfilled' ? orgRes.value : null,
      runtime: rtRes.status === 'fulfilled' ? rtRes.value : null,
      approvals: apprRes.status === 'fulfilled'
        ? apprRes.value.filter((item) => !target.resolved.has(item.id)) : null,
      trend: trendRes.status === 'fulfilled' && Array.isArray(trendRes.value.trends?.daily_counts)
        ? trendRes.value.trends.daily_counts.filter((p) => p && typeof p.date === 'string').slice(-TREND_DAYS) : [],
      trendAvailable: trendRes.status === 'fulfilled',
      // Unknown roster count stays null; zero would wrongly trigger onboarding.
      rosterCount: rosterRes.status === 'fulfilled' ? rosterRes.value.length : null,
    }))
  }, [])

  useEffect(() => {
    scope.active = true
    scope.generation++
    void load(scope)
    return () => { scope.active = false; scope.generation++; scope.request++ }
  }, [scope, load])

  const refreshSecondary = useCallback(() => { void load(scope) }, [load, scope])
  const decide = useCallback(async (item: ApprovalRequest, action: 'approve' | 'reject') => {
    if (!scope.active || scopeRef.current !== scope || item.enterprise_id !== scope.enterpriseId) return
    const generation = scope.generation
    const isCurrent = () => scope.active && scopeRef.current === scope && scope.generation === generation
    setStored((previous) => previous.scope === scope ? { ...previous, deciding: item.id } : previous)
    try {
      if (action === 'approve') {
        await collaborationApi.approveRequest(item.id, '驾驶舱快速批准')
      } else {
        await collaborationApi.rejectRequest(item.id, { reason: '驾驶舱快速驳回' })
      }
      if (!isCurrent()) return
      scope.resolved.add(item.id)
      setStored((previous) => previous.scope === scope
        ? { ...previous, approvals: previous.approvals?.filter((p) => p.id !== item.id) ?? null } : previous)
      toast.success(`${action === 'approve' ? '已批准' : '已驳回'}：${item.title}`)
      refreshLiveRef.current()
    } catch (err) {
      if (!isCurrent()) return
      toast.error(`${action === 'approve' ? '批准' : '驳回'}失败：${
        err instanceof Error && err.message ? err.message : '裁决服务暂不可用，请稍后重试'
      }`)
    } finally {
      if (isCurrent()) {
        setStored((previous) => previous.scope === scope ? { ...previous, deciding: null } : previous)
      }
    }
  }, [scope])

  return { ...overview, refreshSecondary, decide }
}
