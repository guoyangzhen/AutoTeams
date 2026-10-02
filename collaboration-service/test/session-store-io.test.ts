/**
 * AUD-25 续：状态文件不可读写时必须报出可区分的错误，而不是静默产生空状态。
 * 本文件使用独立的状态文件路径（node --test 每个文件独立进程，模块状态互不干扰）。
 */
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

const tmpRoot = await mkdtemp(join(tmpdir(), 'collab-store-io-'))
// 状态文件路径被一个同名目录占用：写入必然失败（EISDIR），跨平台可复现。
const stateFile = join(tmpRoot, 'sessions.json')
await mkdir(stateFile, { recursive: true })
process.env.COLLAB_STATE_FILE = stateFile

const store = await import('../src/session-store.js')

test('状态文件不可写时报出可区分的不可用错误，而不是空状态', async () => {
  await assert.rejects(
    async () => store.markActiveSessionsInterrupted(),
    (error: unknown) => {
      assert.ok(error instanceof store.SessionStoreUnavailableError)
      assert.equal(error.state, 'unreadable')
      assert.equal(error.errnoCode, 'EISDIR')
      assert.match(error.message, /目录而不是文件/)
      return true
    },
  )
  // 没有把失败伪装成「空状态成功」，也没有删掉现场的目录
  assert.deepEqual(await readdir(stateFile), [])
})

test('不可写场景的探测结果同样是可区分的 unreadable', async () => {
  const report = await store.inspectStoreState()
  assert.equal(report.status, 'unreadable')
  assert.equal(report.sessions, 0)
  assert.match(report.reason || '', /目录而不是文件/)
})

test('只读卷 / 权限类 errno 被翻译成明确的运维提示', () => {
  assert.match(store.storeIoErrorMessage('EROFS'), /只读/)
  assert.match(store.storeIoErrorMessage('EACCES'), /无访问权限/)
  assert.match(store.storeIoErrorMessage('EPERM'), /无访问权限/)
  assert.match(store.storeIoErrorMessage('EISDIR'), /目录而不是文件/)
  assert.match(store.storeIoErrorMessage('ENOTDIR'), /父路径不是目录/)
  assert.match(store.storeIoErrorMessage('ENOSPC'), /磁盘空间不足/)
  assert.doesNotMatch(store.storeIoErrorMessage('ENOENT'), /只读/)
})
