/** @vitest-environment jsdom */
import { StrictMode, act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { useDashboardOverview } from './useDashboardOverview'
import * as collaboration from '@/api/collaboration'
import * as evolution from '@/api/evolution'
import * as runtime from '@/api/runtime'
import * as metrics from '@/api/metrics'
import * as workforce from '@/api/workforceProfiles'
import type { ApprovalRequest, OrgMetrics, RuntimeCompileResult } from '@/types'
import type { BusinessMetrics } from '@/api/metrics'

vi.mock('@/api/collaboration', () => ({ getPendingApprovals: vi.fn(), approveRequest: vi.fn(), rejectRequest: vi.fn() }))
vi.mock('@/api/evolution', () => ({ getOrgMetrics: vi.fn() }))
vi.mock('@/api/runtime', () => ({ getRuntime: vi.fn() }))
vi.mock('@/api/metrics', () => ({ getBusinessMetrics: vi.fn() }))
vi.mock('@/api/workforceProfiles', () => ({ listWorkforceProfiles: vi.fn() }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
type Batch = ReturnType<typeof batch>
function batch() {
  return {
    org: deferred<OrgMetrics>(), rt: deferred<RuntimeCompileResult>(),
    approvals: deferred<ApprovalRequest[]>(), trend: deferred<BusinessMetrics>(),
    roster: deferred<Awaited<ReturnType<typeof workforce.listWorkforceProfiles>>>(),
  }
}
const batches: Batch[] = []
function queue() { const item = batch(); batches.push(item); return item }
function approval(id: string, company: string) {
  return { id, enterprise_id: company, title: id } as ApprovalRequest
}
function finish(item: Batch, company: string, approvals: ApprovalRequest[] = []) {
  item.org.resolve({ maturity_level: company } as OrgMetrics)
  item.rt.resolve({ version: company } as RuntimeCompileResult)
  item.approvals.resolve(approvals)
  item.trend.resolve({ trends: { daily_counts: [{ date: company, count: 7 }] } } as BusinessMetrics)
  item.roster.resolve([{}] as Awaited<ReturnType<typeof workforce.listWorkforceProfiles>>)
}

let root: Root
let container: HTMLDivElement
let current: ReturnType<typeof useDashboardOverview>
const refreshLive = vi.fn()
function Harness({ id }: { id: string }) {
  current = useDashboardOverview(id, refreshLive)
  return <div>{JSON.stringify({ org: current.orgMetrics?.maturity_level, rt: current.runtime?.version,
    approvals: current.approvals?.map((p) => p.id), trend: current.trend.map((p) => p.date),
    roster: current.rosterCount, deciding: current.deciding })}</div>
}
async function render(id: string) { await act(async () => { root.render(<Harness id={id} />) }) }
function shown() { return JSON.parse(container.textContent || '{}') as Record<string, unknown> }

beforeEach(() => {
  batches.length = 0
  vi.clearAllMocks()
  vi.mocked(evolution.getOrgMetrics).mockImplementation(() => batches[vi.mocked(evolution.getOrgMetrics).mock.calls.length - 1].org.promise)
  vi.mocked(runtime.getRuntime).mockImplementation(() => batches[vi.mocked(runtime.getRuntime).mock.calls.length - 1].rt.promise)
  vi.mocked(collaboration.getPendingApprovals).mockImplementation(() => batches[vi.mocked(collaboration.getPendingApprovals).mock.calls.length - 1].approvals.promise)
  vi.mocked(metrics.getBusinessMetrics).mockImplementation(() => batches[vi.mocked(metrics.getBusinessMetrics).mock.calls.length - 1].trend.promise)
  vi.mocked(workforce.listWorkforceProfiles).mockImplementation(() => batches[vi.mocked(workforce.listWorkforceProfiles).mock.calls.length - 1].roster.promise)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(async () => { await act(async () => { root.unmount() }); container.remove() })

describe('dashboard overview lifecycle', () => {
  it('hides old data in the switch render and rejects A after B has loaded', async () => {
    const first = queue()
    await render('A')
    await act(async () => { finish(first, 'A', [approval('old', 'A')]) })
    expect(shown().approvals).toEqual(['old'])
    const pendingA = queue()
    await act(async () => { current.refreshSecondary() })
    const second = queue()
    await render('B')
    expect(shown()).toEqual({ trend: [], roster: null, deciding: null })
    await act(async () => { finish(second, 'B', [approval('new', 'B')]) })
    await act(async () => { finish(pendingA, 'old', [approval('old', 'A')]) })
    expect(shown()).toEqual({ org: 'B', rt: 'B', approvals: ['new'], trend: ['B'], roster: 1, deciding: null })
  })

  it('rejects the first A instance after A to B to A', async () => {
    const first = queue(); await render('A')
    const second = queue(); await render('B')
    const third = queue(); await render('A')
    await act(async () => { finish(third, 'latest') })
    await act(async () => { finish(first, 'stale'); finish(second, 'B') })
    expect(shown().org).toBe('latest')
  })

  it('rejects the first StrictMode effect request after effect cleanup and restart', async () => {
    const first = queue(); const second = queue()
    await act(async () => { root.render(<StrictMode><Harness id="A" /></StrictMode>) })
    await act(async () => { finish(second, 'current') })
    await act(async () => { finish(first, 'stale') })
    expect(shown().org).toBe('current')
  })

  it('treats a malformed daily count payload as an empty series', async () => {
    const item = queue(); await render('A')
    item.org.resolve({ maturity_level: 'A' } as unknown as OrgMetrics)
    item.rt.resolve({ version: 'A' } as RuntimeCompileResult)
    item.approvals.resolve([])
    item.roster.resolve([])
    item.trend.resolve({ trends: { daily_counts: {} } } as BusinessMetrics)
    await act(async () => { await item.trend.promise })
    expect(shown().trend).toEqual([])
  })

  it('keeps latest refresh and never restores a resolved approval from an in-flight list', async () => {
    const first = queue(); await render('A')
    await act(async () => { finish(first, 'A', [approval('pending', 'A')]) })
    const stale = queue(); await act(async () => { current.refreshSecondary() })
    const latest = queue(); await act(async () => { current.refreshSecondary() })
    await act(async () => { finish(latest, 'latest', [approval('pending', 'A')]) })
    await act(async () => { finish(stale, 'stale') })
    expect(shown().org).toBe('latest')
    const inflight = queue(); await act(async () => { current.refreshSecondary() })
    vi.mocked(collaboration.approveRequest).mockResolvedValue({} as Awaited<ReturnType<typeof collaboration.approveRequest>>)
    await act(async () => { await current.decide(approval('pending', 'A'), 'approve') })
    await act(async () => { finish(inflight, 'after', [approval('pending', 'A')]) })
    expect(shown().approvals).toEqual([])
    expect(refreshLive).toHaveBeenCalledTimes(1)
  })

  it('does not apply an old approval completion to the new enterprise', async () => {
    const first = queue(); await render('A')
    await act(async () => { finish(first, 'A', [approval('old', 'A')]) })
    const decision = deferred<Awaited<ReturnType<typeof collaboration.approveRequest>>>()
    vi.mocked(collaboration.approveRequest).mockReturnValue(decision.promise)
    let completion!: Promise<void>
    await act(async () => { completion = current.decide(approval('old', 'A'), 'approve') })
    expect(shown().deciding).toBe('old')
    const second = queue(); await render('B')
    expect(shown().deciding).toBeNull()
    await act(async () => { finish(second, 'B', [approval('new', 'B')]) })
    await act(async () => { decision.resolve({} as Awaited<ReturnType<typeof collaboration.approveRequest>>); await completion })
    expect(shown().approvals).toEqual(['new'])
    expect(refreshLive).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })
})
