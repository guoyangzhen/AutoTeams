import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdir, mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { join } from 'node:path'
import test, { type TestContext } from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'
import { WebSocketServer, type WebSocket } from 'ws'

const secret = 'private-cli-token'
async function directoryFor(t: TestContext): Promise<string> {
  const root = join(process.cwd(), '_e2e_local')
  await mkdir(root, { recursive: true })
  const directory = await mkdtemp(join(root, 'lifecycle-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  return directory
}

function launch(t: TestContext, server: string, directory: string) {
  const env = { ...process.env }
  for (const key of Object.keys(env)) if (key.startsWith('AUTOTEAMS_')) delete env[key]
  const child = spawn(process.execPath, [
    '--import', 'tsx', fileURLToPath(new URL('../src/index.ts', import.meta.url)),
    'connect', '--server', server, '--grant', 'cli-grant', '--token', secret, '--path', directory,
  ], { cwd: fileURLToPath(new URL('..', import.meta.url)), env, windowsHide: true })
  let output = ''
  child.stdout.on('data', b => { output += String(b) })
  child.stderr.on('data', b => { output += String(b) })
  // close waits for captured stdout/stderr to drain, unlike exit.
  const exited = once(child, 'close')
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill()
    await exited
    assert.equal(output.includes(secret), false, 'a connection diagnostic exposed the token')
  })
  return { exited, output: () => output }
}

async function bridge(t: TestContext) {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await once(server, 'listening')
  t.after(async () => {
    for (const socket of server.clients) socket.terminate()
    await new Promise<void>(resolve => server.close(() => resolve()))
  })
  return { server, url: `ws://127.0.0.1:${(server.address() as AddressInfo).port}/bridge` }
}

function welcome(socket: WebSocket) {
  socket.send(JSON.stringify({ type: 'welcome', grantId: 'cli-grant', scope: 'read' }))
}

test('policy rejection stops after one connection and does not echo error or close reason', { timeout: 12_000 }, async t => {
  const directory = await directoryFor(t)
  const { server, url } = await bridge(t)
  let attempts = 0
  server.on('connection', socket => {
    attempts++
    socket.send(JSON.stringify({ type: 'error', message: `Rejected ${secret}` }))
    socket.close(1008, secret)
  })
  const child = launch(t, url, directory)
  const [code] = await child.exited
  assert.equal(code, 1)
  assert.equal(attempts, 1)
  assert.match(child.output(), /重新授权/)
  assert.doesNotMatch(child.output(), /后重连/)
})

test('a consumed setup token is never retried after a successful claim disconnects', { timeout: 12_000 }, async t => {
  const directory = await directoryFor(t)
  const { server, url } = await bridge(t)
  let attempts = 0
  let ready = false
  server.on('connection', socket => {
    attempts++
    socket.on('message', data => {
      if (JSON.parse(String(data)).type === 'ready') { ready = true; socket.terminate() }
    })
    welcome(socket)
  })
  const child = launch(t, url, directory)
  const [code] = await child.exited
  assert.equal(code, 1)
  assert.equal(ready, true, child.output())
  assert.equal(attempts, 1)
  assert.match(child.output(), /令牌已在认领时消费/)
})

test('transient pre-claim disconnect retries and tasks before welcome are ignored', { timeout: 15_000 }, async t => {
  const directory = await directoryFor(t)
  const { server, url } = await bridge(t)
  let attempts = 0
  let prematureResult = false
  let ready = false
  server.on('connection', socket => {
    attempts++
    socket.on('message', data => {
      const msg = JSON.parse(String(data))
      if (msg.type === 'task_result') prematureResult = true
      if (msg.type === 'ready') { ready = true; socket.close(1000) }
    })
    if (attempts === 1) {
      socket.send(JSON.stringify({ type: 'task', taskId: 'before-claim', tool: 'read', path: 'file.txt' }))
      void delay(100).then(() => socket.close(1011, 'temporary'))
    } else welcome(socket)
  })
  const child = launch(t, url, directory)
  const [code] = await child.exited
  assert.equal(code, 1)
  assert.equal(attempts, 2)
  assert.equal(ready, true, child.output())
  assert.equal(prematureResult, false)
  assert.match(child.output(), /后重连/)
})

test('HTTP authorization failure exits without retry or response body leakage', { timeout: 12_000 }, async t => {
  const directory = await directoryFor(t)
  let attempts = 0
  const server = createServer((_request, response) => { attempts++; response.writeHead(401); response.end(secret) })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(() => new Promise<void>(resolve => server.close(() => resolve())))
  const child = launch(t, `ws://127.0.0.1:${(server.address() as AddressInfo).port}/bridge`, directory)
  const [code] = await child.exited
  assert.equal(code, 1)
  assert.equal(attempts, 1)
  assert.match(child.output(), /HTTP 401/)
})

test('invalid CLI URL exits cleanly without printing the credential-bearing input', { timeout: 12_000 }, async t => {
  const directory = await directoryFor(t)
  const child = launch(t, `ws://bad host/bridge?token=${secret}`, directory)
  const [code] = await child.exited
  assert.equal(code, 1)
  assert.match(child.output(), /桥接地址无效/)
  assert.doesNotMatch(child.output(), /ERR_INVALID_URL|at new URL/)
})

test('pre-claim retries are bounded even when the server keeps accepting sockets', { timeout: 50_000 }, async t => {
  const directory = await directoryFor(t)
  const { server, url } = await bridge(t)
  let attempts = 0
  const timestamps: number[] = []
  server.on('connection', socket => { attempts++; timestamps.push(Date.now()); socket.close(1011) })
  const child = launch(t, url, directory)
  const [code] = await child.exited
  assert.equal(code, 1)
  assert.equal(attempts, 6, 'one initial attempt and five retries')
  assert.ok(timestamps[2] - timestamps[1] >= 1_800, 'opening a socket must not reset exponential backoff')
  assert.match(child.output(), /重试次数已达上限/)
})
