import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { once } from 'node:events'
import { createServer } from 'node:http'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import type { AddressInfo } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { WebSocketServer } from 'ws'

const cliPath = fileURLToPath(new URL('../src/index.ts', import.meta.url))
const cliCwd = fileURLToPath(new URL('..', import.meta.url))

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((fulfill) => { resolve = fulfill })
  return { promise, resolve }
}

function cleanEnv(): NodeJS.ProcessEnv {
  const env = { ...process.env }
  for (const key of Object.keys(env)) {
    if (key.startsWith('AUTOTEAMS_')) delete env[key]
  }
  return env
}

const requiredArgs = [
  'connect', '--server', 'ws://127.0.0.1:1/bridge', '--grant', 'test-grant',
  '--token', 'test-token', '--path', 'C:/test',
]

test('CLI rejects legacy shared secrets and incomplete physical credentials before connecting', () => {
  const cases: Array<{ args: string[]; env?: NodeJS.ProcessEnv; message: string }> = [
    { args: ['--api', 'http://127.0.0.1:1', '--bridge-secret', 'legacy-secret'], message: '已停用' },
    { args: [], env: { AUTOTEAMS_BRIDGE_SECRET: 'legacy-secret' }, message: '已停用' },
    { args: ['--api', 'http://127.0.0.1:1', '--device-id', 'device-1'], message: '需要完整配置' },
    { args: ['--api', 'http://127.0.0.1:1', '--device-id', 'device-1'], env: { AUTOTEAMS_DEVICE_SECRET: 'private-device-value' }, message: '需要完整配置' },
    { args: ['--api', 'http://127.0.0.1:1', '--device-id', 'device-1', '--runner-id', 'runner-1'], env: { AUTOTEAMS_DEVICE_SECRET: '   ' }, message: '需要完整配置' },
    { args: [], env: { AUTOTEAMS_API_URL: 'http://127.0.0.1:1' }, message: '需要完整配置' },
    { args: ['--vault', 'vault.json'], message: '需要完整配置' },
    { args: ['--api', 'file:///invalid', '--device-id', 'device-1', '--runner-id', 'runner-1'], env: { AUTOTEAMS_DEVICE_SECRET: 'private-device-value' }, message: 'REST 地址无效' },
  ]
  for (const { args, env, message } of cases) {
    const result = spawnSync(process.execPath, ['--import', 'tsx', cliPath, ...requiredArgs, ...args], {
      cwd: cliCwd, env: { ...cleanEnv(), ...env }, encoding: 'utf8', timeout: 10_000, windowsHide: true,
    })
    assert.equal(result.status, 1, result.stderr)
    assert.match(result.stderr, new RegExp(message))
    assert.equal(result.stderr.includes('legacy-secret'), false)
    assert.equal(result.stderr.includes('private-device-value'), false)
  }
})

test('损坏物理账本只关闭物理执行，目录 WebSocket 仍能连接', { timeout: 15_000 }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'autoteams-corrupt-ledger-'))
  const ledgerPath = join(directory, 'ledger.json')
  await writeFile(ledgerPath, '{broken', 'utf8')
  const ws = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await once(ws, 'listening')
  const port = (ws.address() as AddressInfo).port
  const env = cleanEnv()
  env.AUTOTEAMS_API_URL = 'http://127.0.0.1:1'
  env.AUTOTEAMS_DEVICE_ID = 'device-1'
  env.AUTOTEAMS_RUNNER_ID = 'runner-1'
  env.AUTOTEAMS_DEVICE_SECRET = 'synthetic-device-secret'
  env.AUTOTEAMS_RECEIPT_LEDGER = ledgerPath
  const child = spawn(process.execPath, [
    '--import', 'tsx', cliPath, 'connect', '--server', `ws://127.0.0.1:${port}/bridge`,
    '--grant', 'grant-1', '--token', 'synthetic-setup-token', '--path', cliCwd,
  ], { cwd: cliCwd, env, windowsHide: true })
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit')
      child.kill()
      await exited
    }
    for (const peer of ws.clients) peer.terminate()
    await new Promise<void>((resolveClose) => ws.close(() => resolveClose()))
    await rm(directory, { recursive: true, force: true })
  })
  await Promise.race([
    once(ws, 'connection'),
    once(child, 'exit').then(() => { throw new Error('目录连接在物理账本损坏后提前退出') }),
  ])
})

test('CLI exchanges environment device credentials and heartbeats with the registered runner ID', { timeout: 15_000 }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'autoteams-physical-cli-'))
  const ws = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  const tokenRequest = deferred<{ body: Record<string, string>; bridgeSecret: string | string[] | undefined }>()
  const heartbeatRequest = deferred<{ body: Record<string, string>; authorization: string | undefined }>()
  const api = createServer((req, res) => {
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer) => chunks.push(chunk))
    req.on('end', () => {
      if (req.url === '/api/v1/runner/v2/devices/token') {
        tokenRequest.resolve({
          body: JSON.parse(Buffer.concat(chunks).toString('utf8')),
          bridgeSecret: req.headers['x-bridge-secret'],
        })
        res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({
          data: { access_token: 'test-token', expires_at: new Date(Date.now() + 600_000).toISOString() },
        }))
      } else if (req.url === '/api/v1/runner/v2/runners/heartbeat') {
        heartbeatRequest.resolve({
          body: JSON.parse(Buffer.concat(chunks).toString('utf8')),
          authorization: req.headers.authorization,
        })
        res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({
          data: { server_time: new Date().toISOString(), heartbeat_ttl_seconds: 45,
            runner: { runner_id: 'runner-1', online: true }, pending_tasks: [], notifications: [] },
        }))
      } else {
        res.writeHead(404).end('{}')
      }
    })
  })
  await Promise.all([
    once(ws, 'listening'),
    new Promise<void>((resolve) => api.listen(0, '127.0.0.1', resolve)),
  ])
  const wsPort = (ws.address() as AddressInfo).port
  const apiPort = (api.address() as AddressInfo).port
  const env = cleanEnv()
  env.AUTOTEAMS_API_URL = `http://127.0.0.1:${apiPort}`
  env.AUTOTEAMS_DEVICE_ID = 'device-1'
  env.AUTOTEAMS_RUNNER_ID = 'runner-1'
  env.AUTOTEAMS_DEVICE_SECRET = 'private-device-secret'
  env.AUTOTEAMS_RECEIPT_LEDGER = join(directory, 'ledger.json')
  const child = spawn(process.execPath, [
    '--import', 'tsx', cliPath, 'connect', '--server', `ws://127.0.0.1:${wsPort}/bridge`,
    '--grant', 'test-grant', '--token', 'test-token', '--path', cliCwd,
  ], { cwd: cliCwd, env, windowsHide: true })
  let output = ''
  child.stdout.on('data', (chunk) => { output += String(chunk) })
  child.stderr.on('data', (chunk) => { output += String(chunk) })
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit')
      child.kill()
      await exited
    }
    for (const client of ws.clients) client.terminate()
    await new Promise<void>((resolve) => ws.close(() => resolve()))
    await new Promise<void>((resolve) => api.close(() => resolve()))
    await rm(directory, { recursive: true, force: true })
    assert.equal(output.includes('private-device-secret'), false)
  })
  const token = await Promise.race([
    tokenRequest.promise,
    once(child, 'exit').then(() => { throw new Error(`runner exited before device authentication: ${output}`) }),
  ])
  assert.deepEqual(token.body, { device_id: 'device-1', device_secret: 'private-device-secret' })
  assert.equal(token.bridgeSecret, undefined)
  const heartbeat = await heartbeatRequest.promise
  assert.equal(heartbeat.body.runner_id, 'runner-1')
  assert.equal(heartbeat.authorization, 'Bearer test-token')
})

test('connect CLI sends the supplied one-time grant credentials and redacts its log', { timeout: 15_000 }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'autoteams-cli-'))
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  t.after(async () => {
    for (const client of server.clients) client.terminate()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    await rm(directory, { recursive: true, force: true })
  })
  await once(server, 'listening')
  const port = (server.address() as AddressInfo).port
  const connected = once(server, 'connection')
  const secret = 'cli-test-secret+with&reserved=characters'
  const childEnv = { ...process.env }
  for (const key of Object.keys(childEnv)) {
    if (key.startsWith('AUTOTEAMS_')) delete childEnv[key]
  }
  const child = spawn(process.execPath, [
    '--import', 'tsx', fileURLToPath(new URL('../src/index.ts', import.meta.url)),
    'connect', '--server', `ws://127.0.0.1:${port}/bridge?grant=stale&token=stale`,
    '--grant', 'cli-grant', '--token', secret, '--path', directory,
  ], { cwd: fileURLToPath(new URL('..', import.meta.url)), env: childEnv, windowsHide: true })
  let output = ''
  child.stdout.on('data', (chunk) => { output += String(chunk) })
  child.stderr.on('data', (chunk) => { output += String(chunk) })
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit')
      child.kill()
      await exited
    }
    assert.equal(output.includes(secret), false)
    assert.equal(output.includes(encodeURIComponent(secret)), false)
    assert.equal(output.includes('token='), false)
  })
  const [, request] = await connected
  const received = new URL(request.url, 'http://localhost')
  assert.equal(received.pathname, '/bridge')
  assert.deepEqual(received.searchParams.getAll('grant'), ['cli-grant'])
  assert.deepEqual(received.searchParams.getAll('token'), [secret])
})
