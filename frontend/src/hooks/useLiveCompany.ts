import { useEffect, useState } from 'react'
import * as cognitionApi from '@/api/cognition'
import * as collaborationApi from '@/api/collaboration'
import type { EnterpriseVitals } from '@/api/cognition'
import type { CollaborationEvent } from '@/types'

const POLL_INTERVAL_MS = 15000
const MAX_SSE_FAILURES = 2

export interface LiveCompanyState {
  vitals: EnterpriseVitals | null
  events: CollaborationEvent[]
  loading: boolean
  /** 首次加载失败信息；后台刷新失败保留最后一次成功数据。 */
  error: string | null
  mode: 'live' | 'poll'
  lastUpdated: number | null
  refreshing: boolean
  refresh: () => void
}

type Snapshot = Omit<LiveCompanyState, 'refresh'>
const noop = () => {}

function emptySnapshot(enterpriseId: string | null): LiveCompanyState {
  return {
    vitals: null, events: [], loading: !!enterpriseId, error: null,
    mode: 'poll', lastUpdated: null, refreshing: false, refresh: noop,
  }
}

function uniqueEvents(events: CollaborationEvent[]): CollaborationEvent[] {
  return [...new Map(events.map((event) => [event.event_id, event])).values()].slice(-60)
}

/** 企业实时数据：SSE 优先，断线时以单次在途的轮询兜底。 */
export function useLiveCompany(enterpriseId: string | null): LiveCompanyState {
  const [state, setState] = useState(() => ({ enterpriseId, ...emptySnapshot(enterpriseId) }))

  useEffect(() => {
    // 每次 effect 有独立生命周期，避免新企业或 StrictMode 重启激活旧回调。
    let active = true
    let source: EventSource | null = null
    let timer: ReturnType<typeof setInterval> | null = null
    let inFlight: Promise<void> | null = null
    let streamRevision = 0
    let failures = 0
    let visible = !document.hidden

    const update = (apply: (previous: Snapshot) => Snapshot) => {
      if (!active) return
      setState((previous) => active && previous.enterpriseId === enterpriseId
        ? { ...previous, ...apply(previous) } : previous)
    }

    const fetchSnapshot = (silent: boolean): Promise<void> => {
      if (!active || !enterpriseId) return Promise.resolve()
      if (inFlight) return inFlight
      const revision = streamRevision
      if (silent) update((previous) => ({ ...previous, refreshing: true }))
      inFlight = (async () => {
        try {
          const [vitals, events] = await Promise.allSettled([
            cognitionApi.getEnterpriseVitals(enterpriseId),
            collaborationApi.listEvents(enterpriseId, { limit: 30 }),
          ])
          // SSE 已给出较新数据时，较早发出的 HTTP 快照不得覆盖它。
          if (!active || revision !== streamRevision) return
          if (vitals.status === 'rejected' && events.status === 'rejected') {
            update((previous) => ({
              ...previous,
              error: previous.lastUpdated === null ? '数据加载失败' : previous.error,
            }))
            return
          }
          update((previous) => ({
            ...previous,
            vitals: vitals.status === 'fulfilled' ? vitals.value : previous.vitals,
            events: events.status === 'fulfilled'
              ? uniqueEvents([...events.value.items].reverse()) : previous.events,
            error: null, lastUpdated: Date.now(),
          }))
        } finally {
          inFlight = null
          update((previous) => ({ ...previous, loading: false, refreshing: false }))
        }
      })()
      return inFlight
    }

    const stopPolling = () => {
      if (timer !== null) clearInterval(timer)
      timer = null
    }
    const startPolling = () => {
      if (!active) return
      update((previous) => ({ ...previous, mode: 'poll' }))
      if (timer !== null) return
      timer = setInterval(() => {
        if (visible) void fetchSnapshot(true)
      }, POLL_INTERVAL_MS)
    }
    const connectStream = () => {
      if (!active || !enterpriseId) return
      let es: EventSource
      try {
        es = new EventSource(`/api/v1${collaborationApi.eventStreamPath(enterpriseId)}`, {
          withCredentials: true,
        })
      } catch {
        startPolling()
        return
      }
      source = es
      const isCurrent = () => active && source === es
      es.onopen = () => {
        if (!isCurrent()) return
        failures = 0
        stopPolling()
        update((previous) => ({ ...previous, mode: 'live' }))
      }
      es.onmessage = (event) => {
        if (!isCurrent()) return
        let frame: collaborationApi.EventStreamFrame
        try {
          const parsed: unknown = JSON.parse(event.data)
          if (!parsed || typeof parsed !== 'object') return
          frame = parsed as collaborationApi.EventStreamFrame
        } catch {
          return
        }
        if (frame.error || frame.done) {
          es.close()
          source = null
          startPolling()
          void fetchSnapshot(true)
          return
        }
        streamRevision += 1
        update((previous) => ({
          ...previous,
          vitals: frame.vitals ? frame.vitals as EnterpriseVitals : previous.vitals,
          events: Array.isArray(frame.events)
            ? uniqueEvents(frame.full_refresh ? frame.events : [...previous.events, ...frame.events])
            : previous.events,
          loading: false, error: null, lastUpdated: Date.now(),
        }))
      }
      es.onerror = () => {
        if (!isCurrent()) return
        // 首次断线即显示 POLL 并兜底；浏览器仍可重连，连续失败后关闭。
        startPolling()
        failures += 1
        if (failures >= MAX_SSE_FAILURES) {
          es.close()
          source = null
        }
      }
    }
    const handleVisibility = () => {
      const nowVisible = !document.hidden
      if (nowVisible && !visible && timer !== null) void fetchSnapshot(true)
      visible = nowVisible
    }
    // 此函数归属当前生命周期；旧企业的审批完成后调用它也不会刷新新企业。
    const refresh = () => { void fetchSnapshot(true) }
    setState({ enterpriseId, ...emptySnapshot(enterpriseId), refresh })
    if (enterpriseId) {
      document.addEventListener('visibilitychange', handleVisibility)
      void fetchSnapshot(false).then(connectStream)
    }
    return () => {
      active = false
      document.removeEventListener('visibilitychange', handleVisibility)
      source?.close()
      source = null
      stopPolling()
    }
  }, [enterpriseId])

  // 在 effect 执行前的首个 render 也不能显示上一个企业的数据。
  return state.enterpriseId === enterpriseId ? state : emptySnapshot(enterpriseId)
}
