/**
 * AUD-25：协作会话状态存储的首次启动与损坏处理。
 *
 * 复现路径与 docs/TECHNICAL_AUDIT_VALIDATION_2026-09-28.md §6.5 一致：
 * COLLAB_STATE_FILE 指向尚不存在的 <tmp>/new-data/sessions.json。
 */
import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

const tmpRoot = await mkdtemp(join(tmpdir(), 'collab-store-'))
const stateFile = join(tmpRoot, 'new-data', 'sessions.json')
process.env.COLLAB_STATE_FILE = stateFile

const store = await import('../src/session-store.js')

const SAMPLE_META = {
  sessionId: 'session-a',
  agentId: 'agent-a',
  agentName: '工程师',
  positionLabel: '后端',
  createdAt: '2026-09-28T00:00:00.000Z',
  lastActiveAt: '2026-09-28T00:00:00.000Z',
  messageCount: 1,
  lastMessagePreview: 'hello',
  mode: 'sandbox' as const,
}

test('父目录不存在时首次 markActiveSessionsInterrupted 不再 ENOENT', async () => {
  await assert.doesNotReject(() => store.markActiveSessionsInterrupted())

  const report = await store.inspectStoreState()
  assert.equal(report.status, 'ok')
  assert.equal(report.stateFile, stateFile)
  assert.deepEqual(await readdir(join(tmpRoot, 'new-data')), ['sessions.json'])
})

test('正常读写仍然工作（未被修复破坏）', async () => {
  await store.persistSession(SAMPLE_META, 'user-1', 'enterprise-1', tmpRoot, [
    { role: 'user', content: 'hello', timestamp: '2026-09-28T00:00:00.000Z' },
  ])
  const sessions = await store.listPersistedSessions('user-1')
  assert.equal(sessions.length, 1)
  assert.equal(sessions[0].sessionId, 'session-a')
})

test('损坏的 JSON 既不被静默覆盖，读路径也返回可区分的 corrupt 状态', async () => {
  const corrupted = '{"version":1,"sessions":{"session-a":'
  await writeFile(stateFile, corrupted, 'utf8')

  // 1) 只读探测明确区分「损坏」，而不是 missing/空状态
  const report = await store.inspectStoreState()
  assert.equal(report.status, 'corrupt')
  assert.ok(report.reason && report.reason.length > 0)

  // 2) 读路径抛出可区分的 typed error，而不是返回 {} 或空数组
  await assert.rejects(
    () => store.listPersistedSessions('user-1'),
    (error: unknown) => {
      assert.ok(error instanceof store.SessionStoreCorruptError)
      assert.equal(error.state, 'corrupt')
      assert.match(error.message, /已损坏/)
      return true
    },
  )
  await assert.rejects(() => store.getPersistedSession('session-a', 'user-1'), store.SessionStoreCorruptError)

  // 3) 写路径被阻断，且损坏文件原样保留
  await assert.rejects(
    () => store.persistSession(SAMPLE_META, 'user-1', 'enterprise-1', tmpRoot, []),
    store.SessionStoreCorruptError,
  )
  assert.equal(await readFile(stateFile, 'utf8'), corrupted)

  // 4) 覆盖之前保留了一份损坏副本
  const files = await readdir(join(tmpRoot, 'new-data'))
  const backup = files.find((name) => name.startsWith('sessions.json.corrupt-'))
  assert.ok(backup, `期望存在损坏副本，实际文件: ${files.join(', ')}`)
  assert.equal(await readFile(join(tmpRoot, 'new-data', backup!), 'utf8'), corrupted)
})

test('结构不合法（JSON 可解析但不是状态文档）同样按损坏处理', async () => {
  const structurallyWrong = JSON.stringify({ version: 2, sessions: [] })
  await writeFile(stateFile, structurallyWrong, 'utf8')
  const report = await store.inspectStoreState()
  assert.equal(report.status, 'corrupt')
  await assert.rejects(() => store.markActiveSessionsInterrupted(), store.SessionStoreCorruptError)
  assert.equal(await readFile(stateFile, 'utf8'), structurallyWrong)
})
