/**
 * 端侧桥接的**真实 HTTP 行为**测试（AUD-18）。
 *
 * 协调评审要求：回执被云端拒绝时不得把拒绝当成 ack。这里起一个真实的
 * HTTP 桩服务（不是 mock fetch），让 `startPhysicalBridge` 走完整的
 * 换令牌 → 补发回执 → 心跳流程，然后检查落盘账本：
 *
 * * 404/409：记录进入 `rejection`，`reported` 仍为 false，且不再自动重发；
 * * 200：记录标记为已确认，不再出现在待发队列。
 *
 * 同步靠桩服务"收到回执请求"这个真实信号，不用固定 sleep 猜时间。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer, type Server, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'

import { startPhysicalBridge } from '../src/physical-bridge.js'
import { fileReceiptLedger, type ReceiptRecord } from '../src/receipt-ledger.js'

interface StubHandle {
  server: Server
  baseUrl: string
  /** 收到的回执请求体（按到达顺序）。 */
  resultBodies: Array<Record<string, unknown>>
  /** 第一次回执请求到达时兑现。 */
  firstResult: Promise<void>
}

async function startStub(resultStatus: number): Promise<StubHandle> {
  const resultBodies: Array<Record<string, unknown>> = []
  const { promise: firstResult, resolve: signalFirstResult } = Promise.withResolvers<void>()

  const server = createServer((req, res) => {
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer) => chunks.push(chunk))
    req.on('end', () => {
      const body = chunks.length > 0 ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}
      const url = req.url ?? ''
      const send = (status: number, payload: unknown): void => {
        res.writeHead(status, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify(payload))
      }

      if (url === '/api/v1/runner/v2/devices/token') {
        send(200, {
          data: {
            access_token: 'stub-token',
            expires_at: new Date(Date.now() + 600_000).toISOString(),
          },
        })
        return
      }
      if (url === '/api/v1/runner/v2/runners/heartbeat') {
        send(200, {
          data: {
            server_time: new Date().toISOString(),
            heartbeat_ttl_seconds: 45,
            runner: { runner_id: 'stub-runner', online: true },
            pending_tasks: [],
            notifications: [],
          },
        })
        return
      }
      if (url.includes('/result')) {
        resultBodies.push(body)
        signalFirstResult()
        send(resultStatus, { success: false, message: 'stub rejection' })
        return
      }
      send(404, { success: false })
    })
  })

  await new Promise<void>((resolveListen) => server.listen(0, '127.0.0.1', resolveListen))
  const { port } = server.address() as AddressInfo
  return {
    server,
    baseUrl: `http://127.0.0.1:${port}`,
    resultBodies,
    firstResult,
  }
}

function seedLedger(path: string, taskId: string, stepId: string): void {
  const record: ReceiptRecord = {
    task_id: taskId,
    step_id: stepId,
    status: 'done',
    ok: true,
    data: { matched: 1 },
    started_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    reported: false,
  }
  writeFileSync(
    path,
    JSON.stringify({ version: 1, records: { [`${taskId}::${stepId}`]: record } }),
    'utf8',
  )
}

/** 账本被写盘是原子 rename；轮询到目标条件即返回，不依赖固定等待时长。 */
async function waitForLedger(
  path: string,
  key: string,
  predicate: (record: ReceiptRecord) => boolean,
): Promise<ReceiptRecord> {
  const deadline = Date.now() + 10_000
  for (;;) {
    const persisted = JSON.parse(readFileSync(path, 'utf8')) as {
      records: Record<string, ReceiptRecord>
    }
    const record = persisted.records[key]
    if (record && predicate(record)) return record
    if (Date.now() > deadline) {
      throw new Error(`账本未在超时内满足条件: ${key} -> ${JSON.stringify(record)}`)
    }
    await new Promise((resolveTick) => setImmediate(resolveTick))
  }
}

function bridgeConfig(baseUrl: string, ledgerPath: string) {
  return {
    apiUrl: baseUrl,
    deviceId: 'device-stub',
    deviceSecret: 'secret-stub',
    runnerId: 'stub-runner',
    scopes: ['physical'],
    vaultPath: '',
    platform: process.platform,
    version: '2.0.0',
    receiptLedgerPath: ledgerPath,
  }
}

test('云端 409 拒绝回执：账本记为 rejected，不伪造已入账', async () => {
  const stub = await startStub(409)
  const dir = mkdtempSync(resolve(tmpdir(), 'autoteams-bridge-409-'))
  const ledgerPath = resolve(dir, 'ledger.json')
  const taskId = 'ptask-reject'
  const key = `${taskId}::s0`
  seedLedger(ledgerPath, taskId, 's0')

  const bridge = startPhysicalBridge(bridgeConfig(stub.baseUrl, ledgerPath))
  let stored: ReceiptRecord
  try {
    await stub.firstResult
    stored = await waitForLedger(ledgerPath, key, (record) => record.rejection !== undefined)
  } finally {
    await bridge.stop()
    stub.server.close()
  }

  // 请求确实带上了可被云端核对的 step 身份。
  assert.equal(stub.resultBodies[0].step_id, 's0')
  assert.equal(stub.resultBodies[0].receipt_id, key)
  assert.equal(stored.reported, false, '被拒绝的回执绝不能标记为已确认')
  assert.equal(stored.rejection?.status, 409)
  assert.ok(stored.rejection?.at, '必须记录拒绝时间以供人工处置')

  // 落盘后重新打开账本：拒绝记录不再进入自动重发队列，但仍可被观测到。
  const reopened = fileReceiptLedger(ledgerPath)
  assert.equal(reopened.pendingReports().length, 0)
  assert.equal(reopened.rejected().length, 1)
  assert.equal(reopened.begin(taskId, 's0'), null, '已执行的 step 仍不得重放')

  rmSync(dir, { recursive: true, force: true })
})

test('云端 200 接受回执：标记已确认且不再重发', async () => {
  const stub = await startStub(200)
  const dir = mkdtempSync(resolve(tmpdir(), 'autoteams-bridge-200-'))
  const ledgerPath = resolve(dir, 'ledger.json')
  const taskId = 'ptask-ok'
  const key = `${taskId}::s0`
  seedLedger(ledgerPath, taskId, 's0')

  const bridge = startPhysicalBridge(bridgeConfig(stub.baseUrl, ledgerPath))
  try {
    await stub.firstResult
    await waitForLedger(ledgerPath, key, (record) => record.reported === true)
  } finally {
    await bridge.stop()
    stub.server.close()
  }

  assert.equal(stub.resultBodies[0].step_id, 's0')
  const reopened = fileReceiptLedger(ledgerPath)
  assert.equal(reopened.pendingReports().length, 0)
  assert.equal(reopened.rejected().length, 0)

  rmSync(dir, { recursive: true, force: true })
})

test('上一轮心跳仍在等待时不启动重叠的物理心跳', { timeout: 10_000 }, async () => {
  let heartbeatCount = 0
  let signalFirst!: () => void
  const firstHeartbeat = new Promise<void>((resolveFirst) => { signalFirst = resolveFirst })
  let heldResponse: ServerResponse | undefined
  const server = createServer((req, res) => {
    if (req.url === '/api/v1/runner/v2/devices/token') {
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({
        data: { access_token: 'stub-token', expires_at: new Date(Date.now() + 600_000).toISOString() },
      }))
    } else if (req.url === '/api/v1/runner/v2/runners/heartbeat') {
      heartbeatCount += 1
      if (heartbeatCount === 1) {
        heldResponse = res
        signalFirst()
      } else {
        res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({
          data: { pending_tasks: [], notifications: [] },
        }))
      }
    } else {
      res.writeHead(404).end('{}')
    }
  })
  await new Promise<void>((resolveListen) => server.listen(0, '127.0.0.1', resolveListen))
  const dir = mkdtempSync(resolve(tmpdir(), 'autoteams-bridge-overlap-'))
  const originalInterval = globalThis.setInterval
  let pulse: (() => void) | undefined
  globalThis.setInterval = ((callback: () => void) => {
    pulse = callback
    return originalInterval(() => undefined, 60_000)
  }) as typeof setInterval
  const { port } = server.address() as AddressInfo
  let bridge: ReturnType<typeof startPhysicalBridge> | undefined
  try {
    bridge = startPhysicalBridge(bridgeConfig(`http://127.0.0.1:${port}`, resolve(dir, 'ledger.json')))
  } finally {
    globalThis.setInterval = originalInterval
  }
  try {
    await firstHeartbeat
    assert.ok(pulse)
    pulse()
    pulse()
    await new Promise<void>((resolveTick) => setTimeout(resolveTick, 50))
    assert.equal(heartbeatCount, 1, '前一请求未结束，不应并发发出第二次心跳')
  } finally {
    const stopping = bridge?.stop()
    heldResponse?.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({
      data: { pending_tasks: [{ task_id: 'after-stop', channel: 'browser_action',
        steps: [{ step_id: 's0', op: 'click', x: 10, y: 10 }] }], notifications: [] },
    }))
    await stopping
    assert.equal(fileReceiptLedger(resolve(dir, 'ledger.json')).size(), 0,
      '停止后返回的任务不得登记或执行')
    await new Promise<void>((resolveClose) => server.close(() => resolveClose()))
    rmSync(dir, { recursive: true, force: true })
  }
})

test('账本满时不执行新物理 step，也不发送不存在的 task 回执', { timeout: 10_000 }, async () => {
  const dir = mkdtempSync(resolve(tmpdir(), 'autoteams-bridge-capacity-'))
  const ledgerPath = resolve(dir, 'ledger.json')
  const records = Object.fromEntries(Array.from({ length: 500 }, (_, index) => {
    const taskId = `past-${index}`
    return [`${taskId}::s0`, {
      task_id: taskId, step_id: 's0', status: 'done', ok: true, reported: true,
      started_at: new Date().toISOString(), finished_at: new Date().toISOString(),
    }]
  }))
  writeFileSync(ledgerPath, JSON.stringify({ version: 1, records }), 'utf8')
  let resultCount = 0
  let signalHeartbeat!: () => void
  const heartbeatReceived = new Promise<void>((resolveHeartbeat) => { signalHeartbeat = resolveHeartbeat })
  const server = createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json')
    if (req.url === '/api/v1/runner/v2/devices/token') {
      res.end(JSON.stringify({ data: {
        access_token: 'stub-token', expires_at: new Date(Date.now() + 600_000).toISOString(),
      } }))
    } else if (req.url === '/api/v1/runner/v2/runners/heartbeat') {
      res.end(JSON.stringify({ data: {
        pending_tasks: [{ task_id: 'new-task', channel: 'browser_action',
          steps: [{ step_id: 's0', op: 'click', x: 10, y: 10 }] }],
        notifications: [],
      } }))
      signalHeartbeat()
    } else if (req.url?.includes('/result')) {
      resultCount += 1
      res.end('{}')
    } else {
      res.writeHead(404).end('{}')
    }
  })
  await new Promise<void>((resolveListen) => server.listen(0, '127.0.0.1', resolveListen))
  const { port } = server.address() as AddressInfo
  const bridge = startPhysicalBridge(bridgeConfig(`http://127.0.0.1:${port}`, ledgerPath))
  try {
    await heartbeatReceived
    await new Promise<void>((resolveTick) => setTimeout(resolveTick, 50))
    assert.equal(resultCount, 0, '账本已满不应发送后端无法识别的合成 step_id')
    const saved = JSON.parse(readFileSync(ledgerPath, 'utf8')) as { records: Record<string, unknown> }
    assert.equal(Object.keys(saved.records).length, 500)
    assert.equal('new-task::s0' in saved.records, false)
  } finally {
    await bridge.stop()
    await new Promise<void>((resolveClose) => server.close(() => resolveClose()))
    rmSync(dir, { recursive: true, force: true })
  }
})
