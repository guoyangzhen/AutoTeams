import { act, StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getEnterpriseVitals, type EnterpriseVitals } from '@/api/cognition'
import { listEvents } from '@/api/collaboration'
import type { CollaborationEvent } from '@/types'
import { useLiveCompany, type LiveCompanyState } from './useLiveCompany'

vi.mock('@/api/cognition', () => ({ getEnterpriseVitals: vi.fn() }))
vi.mock('@/api/collaboration', () => ({
  listEvents: vi.fn(), eventStreamPath: (id: string) => `/stream/${id}`,
}))
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function vitals(count: number): EnterpriseVitals {
  return {
    agents: { total: count, production: count, training: 0, recruit: 0, stage_distribution: {} },
    events: { last_hour: 0, last_24h: 0, total: 0, failed: 0, per_hour: 0 },
    approvals: { pending: 0 },
    shadow: { autonomous: 0, total: 0, evaluated: 0, match: 0, trust_score: null },
    compile: { running: false, job_id: null, stage: null, progress: null,
      last_completed_at: null, last_completeness: null },
    health: { score: 100, tone: 'alive' }, collected_at: '2026-09-30T00:00:00Z',
  }
}
function event(id: string): CollaborationEvent {
  return { event_id: id, enterprise_id: 'a', event_type: 'inquiry_received', payload: {},
    source_agent_id: null, target_agent_id: null, created_at: '2026-09-30T00:00:00Z' }
}

class Stream {
  static instances: Stream[] = []
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  close = vi.fn()
  constructor(readonly url: string) { Stream.instances.push(this) }
  message(frame: unknown) { this.onmessage?.({ data: JSON.stringify(frame) }) }
}

const vitalsMock = vi.mocked(getEnterpriseVitals)
const eventsMock = vi.mocked(listEvents)
let root: Root
let container: HTMLDivElement
let current: LiveCompanyState
let renders: LiveCompanyState[]
function Probe({ id }: { id: string | null }) {
  current = useLiveCompany(id)
  renders.push(current)
  return <output>{current.vitals?.agents.total ?? 'empty'}</output>
}
async function render(id: string | null, strict = false) {
  await act(async () => {
    root.render(strict ? <StrictMode><Probe id={id} /></StrictMode> : <Probe id={id} />)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  vi.stubGlobal('EventSource', Stream)
  Stream.instances = []
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  vitalsMock.mockResolvedValue(vitals(1))
  eventsMock.mockResolvedValue({ items: [event('initial')], total: 1 })
  renders = []
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('enterprise live data lifecycle', () => {
  it('rejects old HTTP responses and never opens their stream after an enterprise switch', async () => {
    const old = deferred<EnterpriseVitals>()
    const next = deferred<EnterpriseVitals>()
    vitalsMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
    await render('a')
    await render('b')
    await act(async () => old.resolve(vitals(99)))
    expect(current.vitals).toBeNull()
    expect(Stream.instances).toHaveLength(0)
    await act(async () => next.resolve(vitals(2)))
    expect(current.vitals?.agents.total).toBe(2)
    expect(Stream.instances.map((stream) => stream.url)).toEqual(['/api/v1/stream/b'])
  })

  it('hides the old snapshot on the very first switch render and clears it without an identity', async () => {
    await render('a')
    const oldRefresh = current.refresh
    const next = deferred<EnterpriseVitals>()
    vitalsMock.mockReturnValueOnce(next.promise)
    renders = []
    await render('b')
    expect(renders.every((snapshot) => snapshot.vitals === null && snapshot.events.length === 0)).toBe(true)
    const calls = vitalsMock.mock.calls.length
    await act(async () => oldRefresh())
    expect(vitalsMock).toHaveBeenCalledTimes(calls)
    await render(null)
    expect(current).toMatchObject({ vitals: null, events: [], loading: false, lastUpdated: null })
    await act(async () => next.resolve(vitals(2)))
    expect(current.vitals).toBeNull()
  })

  it('ignores queued messages, opens and errors from a closed stream', async () => {
    await render('a')
    const old = Stream.instances[0]
    await render('b')
    await act(async () => {
      old.onopen?.()
      old.message({ vitals: vitals(99), events: [event('old')] })
      old.onerror?.()
      old.onerror?.()
    })
    expect(old.close).toHaveBeenCalledTimes(1)
    expect(current.vitals?.agents.total).toBe(1)
    expect(current.events.map((item) => item.event_id)).toEqual(['initial'])
    expect(current.mode).toBe('poll')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not revive the discarded StrictMode lifecycle', async () => {
    const first = deferred<EnterpriseVitals>()
    vitalsMock.mockReturnValueOnce(first.promise)
    await render('a', true)
    expect(Stream.instances).toHaveLength(1)
    await act(async () => first.resolve(vitals(99)))
    expect(current.vitals?.agents.total).toBe(1)
    expect(Stream.instances).toHaveLength(1)
  })

  it('deduplicates a frame and accepts an empty full refresh', async () => {
    await render('a')
    await act(async () => Stream.instances[0].message({ events: [event('new'), event('new')] }))
    expect(current.events.map((item) => item.event_id)).toEqual(['initial', 'new'])
    await act(async () => Stream.instances[0].message({ full_refresh: true, events: [] }))
    expect(current.events).toEqual([])
  })

  it('falls back on the first failure, allows only one poll in flight, and preserves newer SSE data', async () => {
    await render('a')
    const stream = Stream.instances[0]
    await act(async () => stream.onopen?.())
    expect(current.mode).toBe('live')
    await act(async () => stream.onerror?.())
    expect(current.mode).toBe('poll')
    const pending = deferred<EnterpriseVitals>()
    vitalsMock.mockReturnValueOnce(pending.promise)
    await act(async () => vi.advanceTimersByTime(15000))
    await act(async () => {
      current.refresh()
      vi.advanceTimersByTime(45000)
    })
    expect(vitalsMock).toHaveBeenCalledTimes(2)
    await act(async () => stream.message({ vitals: vitals(3), full_refresh: true, events: [] }))
    await act(async () => pending.resolve(vitals(2)))
    expect(current.vitals?.agents.total).toBe(3)
    expect(current.events).toEqual([])
    expect(current.refreshing).toBe(false)
    await act(async () => stream.onopen?.())
    expect(current.mode).toBe('live')
    expect(vi.getTimerCount()).toBe(0)
    await act(async () => { stream.onerror?.(); stream.onerror?.() })
    expect(stream.close).toHaveBeenCalledTimes(1)
    expect(current.mode).toBe('poll')
  })

  it('pauses hidden polling, refreshes on return, and removes timers on unmount', async () => {
    await render('a')
    await act(async () => Stream.instances[0].onerror?.())
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      vi.advanceTimersByTime(30000)
    })
    expect(vitalsMock).toHaveBeenCalledTimes(1)
    hidden.mockReturnValue(false)
    await act(async () => document.dispatchEvent(new Event('visibilitychange')))
    expect(vitalsMock).toHaveBeenCalledTimes(2)
    await render(null)
    expect(vi.getTimerCount()).toBe(0)
    await act(async () => vi.advanceTimersByTime(30000))
    expect(vitalsMock).toHaveBeenCalledTimes(2)
  })

  it('recovers from initial failure and keeps a good snapshot on background failure', async () => {
    vitalsMock.mockRejectedValueOnce(new Error('offline'))
    eventsMock.mockRejectedValueOnce(new Error('offline'))
    await render('a')
    expect(current.error).toBe('数据加载失败')
    expect(current.loading).toBe(false)
    await act(async () => current.refresh())
    expect(current.vitals?.agents.total).toBe(1)
    expect(current.error).toBeNull()
    vitalsMock.mockRejectedValueOnce(new Error('offline'))
    eventsMock.mockRejectedValueOnce(new Error('offline'))
    await act(async () => current.refresh())
    expect(current.vitals?.agents.total).toBe(1)
    expect(current.events).toHaveLength(1)
    expect(current.refreshing).toBe(false)
  })
})
