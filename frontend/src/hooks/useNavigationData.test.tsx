import { act, StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { getPendingApprovals } from '@/api/collaboration'
import { listFiles } from '@/api/files'
import { listWorkforce } from '@/api/workforce'
import { useNavigationData } from './useNavigationData'

vi.mock('@/api/collaboration', () => ({ getPendingApprovals: vi.fn() }))
vi.mock('@/api/files', () => ({ listFiles: vi.fn() }))
vi.mock('@/api/workforce', () => ({ listWorkforce: vi.fn() }))
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
function batch() {
  const approvals = deferred<Awaited<ReturnType<typeof getPendingApprovals>>>()
  const files = deferred<Awaited<ReturnType<typeof listFiles>>>()
  const agents = deferred<Awaited<ReturnType<typeof listWorkforce>>>()
  vi.mocked(getPendingApprovals).mockReturnValueOnce(approvals.promise)
  vi.mocked(listFiles).mockReturnValueOnce(files.promise)
  vi.mocked(listWorkforce).mockReturnValueOnce(agents.promise)
  return {
    approvals, files, agents,
    finish: (id: string) => {
      approvals.resolve([{ id }] as Awaited<ReturnType<typeof getPendingApprovals>>)
      files.resolve({ files: [{ id, original_name: id, file_type: 'txt' }], total: 1 } as Awaited<ReturnType<typeof listFiles>>)
      agents.resolve({ items: [{ agent_id: id }], total: 1 } as Awaited<ReturnType<typeof listWorkforce>>)
    },
  }
}
let root: Root
let host: HTMLDivElement
let current: ReturnType<typeof useNavigationData>
let snapshots: ReturnType<typeof useNavigationData>[]
function Probe({ enterprise, user }: { enterprise: string; user: string | null }) {
  current = useNavigationData(enterprise, user)
  snapshots.push(current)
  return null
}
async function render(enterprise: string, user: string | null = 'user', strict = false) {
  await act(async () => root.render(strict
    ? <StrictMode><Probe enterprise={enterprise} user={user} /></StrictMode>
    : <Probe enterprise={enterprise} user={user} />))
}
beforeEach(() => {
  vi.resetAllMocks()
  snapshots = []
  host = document.createElement('div'); document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove() })

it('hides old employee/file/notification data on the first enterprise switch render', async () => {
  const first = batch(); await render('A')
  await act(async () => first.finish('old'))
  expect(current.approvalIds).toEqual(['old'])
  const second = batch(); snapshots = []; await render('B')
  expect(snapshots.every((item) => !item.agents.length && !item.files.length && !item.approvalIds.length)).toBe(true)
  await act(async () => second.finish('new'))
  expect(current.approvalIds).toEqual(['new'])
})

it('rejects late responses across A to B to A and a new login in the same enterprise', async () => {
  const first = batch(); await render('A')
  const second = batch(); await render('B')
  const third = batch(); await render('A')
  await act(async () => { third.finish('latest'); first.finish('stale'); second.finish('other') })
  expect(current.approvalIds).toEqual(['latest'])
  const fourth = batch(); await render('A', 'new-user')
  expect(current.agents).toEqual([])
  await act(async () => fourth.finish('new-login'))
  expect(current.files[0].original_name).toBe('new-login')
})

it('keeps successful search sources available when the independent notification request fails', async () => {
  const item = batch(); await render('A')
  await act(async () => {
    item.files.resolve({ files: [], total: 0 })
    item.agents.resolve({ items: [], total: 0 })
    item.approvals.reject(new Error('offline'))
  })
  expect(current).toMatchObject({ approvalsLoading: false, approvalsError: true, approvalIds: [] })
})

it('clears everything on logout and does not fetch without a user', async () => {
  const item = batch(); await render('A')
  await render('A', null)
  await act(async () => item.finish('old'))
  expect(current).toMatchObject({ agents: [], files: [], approvalIds: [], approvalsLoading: false })
  expect(getPendingApprovals).toHaveBeenCalledTimes(1)
})

it('rejects the first StrictMode effect lifecycle even when its identity matches', async () => {
  const old = batch(); const active = batch()
  await render('A', 'user', true)
  await act(async () => { active.finish('current'); old.finish('stale') })
  expect(current.approvalIds).toEqual(['current'])
})
