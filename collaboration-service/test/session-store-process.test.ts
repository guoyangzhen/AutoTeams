import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, mkdir, readFile, utimes } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

function writer(stateFile: string, prefix: string) {
  const child = spawn(process.execPath, ['--import', 'tsx',
    fileURLToPath(new URL('./fixtures/session-writer.mjs', import.meta.url))], {
    cwd: fileURLToPath(new URL('../', import.meta.url)),
    env: { ...process.env, COLLAB_STATE_FILE: stateFile, TEST_SESSION_PREFIX: prefix },
    stdio: ['ignore', 'ignore', 'pipe', 'ipc'], windowsHide: true,
  })
  const timeout = setTimeout(() => child.kill(), 20000)
  child.once('exit', () => clearTimeout(timeout))
  let stderr = ''
  child.stderr!.on('data', (chunk) => { stderr += chunk })
  const ready = new Promise<void>((resolve, reject) => {
    child.once('message', () => resolve())
    child.once('error', reject)
    child.once('exit', () => reject(new Error(stderr || 'writer exited before ready')))
  })
  const done = new Promise<void>((resolve, reject) => {
    child.once('error', reject)
    child.once('exit', (code) => code === 0 ? resolve() : reject(new Error(stderr || `writer exit ${code}`)))
  })
  // Attach immediately: startup failure can happen before the parent awaits done.
  void done.catch(() => undefined)
  return { child, ready, done }
}

test('two real processes preserve all sessions, then recover an expired lock', { timeout: 30000 }, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'collab-process-'))
  const stateFile = join(directory, 'sessions.json')
  const pair = [writer(stateFile, 'a'), writer(stateFile, 'b')]
  try {
    await Promise.all(pair.map((job) => job.ready))
    pair.forEach((job) => job.child.send({ start: true }))
    await Promise.all(pair.map((job) => job.done))
    const first = JSON.parse(await readFile(stateFile, 'utf8'))
    assert.equal(Object.keys(first.sessions).length, 24)
    for (const prefix of ['a', 'b']) for (let i = 0; i < 12; i++) {
      assert.equal(first.sessions[`${prefix}-${i}`].ownerUserId, 'owner')
    }
    const lock = `${stateFile}.lock`
    await mkdir(lock)
    const expired = new Date(Date.now() - 120000)
    await utimes(lock, expired, expired)
    const recovered = writer(stateFile, 'c')
    pair.push(recovered)
    await recovered.ready
    recovered.child.send({ start: true })
    await recovered.done
    const final = JSON.parse(await readFile(stateFile, 'utf8'))
    assert.equal(Object.keys(final.sessions).length, 36)
  } finally {
    pair.forEach((job) => { if (job.child.exitCode === null) job.child.kill() })
  }
})
