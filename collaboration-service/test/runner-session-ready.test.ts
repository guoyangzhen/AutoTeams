/**
 * 就绪门禁回归：ready 必须等后端 /connected 确认成功后才会置为就绪。
 *
 * 覆盖四种真实 socket 场景（真的 ws 服务端 + 真的客户端连接）：
 * - 成功：确认之后才有 ready_ack，才计入在线数，才允许下发任务
 * - 失败：后端拒绝 → 回 error 并断开，既不在线也不下发任务
 * - 在途：等待应答期间重复 ready 只上报一次
 * - 迟到：socket 断开 / 同 grant 被新会话替换后，迟到的成功不得复活旧会话
 *
 * 同步方式全部基于真实信号（协议消息、socket close、后端上报被调用），
 * 不用固定时长 sleep 猜时序。
 *
 * node --test 每个测试文件独立进程，模块级会话池在本文件内自洽；
 * 各用例使用互不相同的 grantId，并在线程结束时关闭全部连接。
 */
import assert from 'node:assert/strict'
import { once } from 'node:events'
import type { AddressInfo } from 'node:net'
import { test } from 'node:test'
import { WebSocket, WebSocketServer } from 'ws'

import {
  configureRunnerSession,
  dispatchTask,
  getRunnerSession,
  handleRunnerConnection,
  isRunnerReady,
  onlineCount,
  runnerCount,
} from '../src/runner-session.js'

/**
 * 起一个真的 /bridge 端点，扮演 server.ts 的角色把连接交给会话管理。
 *
 * close() 会终止并等待所有服务端连接真正触发 close 事件后才返回：会话池的
 * 清理挂在 close 事件上，只有等它落地，下一条用例的在线数基线才是干净的。
 */
async function startBridge(): Promise<{ url: string; close: () => Promise<void> }> {
  const wss = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await once(wss, 'listening')
  const sockets = new Set<WebSocket>()
  wss.on('connection', (ws, req) => {
    sockets.add(ws)
    ws.once('close', () => sockets.delete(ws))
    const url = new URL(req.url ?? '/', 'http://127.0.0.1')
    handleRunnerConnection(
      ws,
      url.searchParams.get('grant_id') ?? '',
      url.searchParams.get('token') ?? '',
    )
  })
  const { port } = wss.address() as AddressInfo
  return {
    url: `ws://127.0.0.1:${port}/bridge`,
    close: async () => {
      const closed = [...sockets].map((ws) => once(ws, 'close'))
      for (const ws of sockets) ws.terminate()
      await Promise.all(closed)
      await new Promise<void>((resolve) => {
        wss.close(() => resolve())
      })
    },
  }
}

/**
 * 手写 deferred：不用 Promise.withResolvers（Node 22+），
 * 项目声明的运行环境更早（测试脚本用的 --import tsx 本身也要求 Node 20.6+）。
 */
function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void
  const promise = new Promise<void>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

interface Client {
  /** 收到的全部服务端消息（按到达顺序） */
  received: Record<string, unknown>[]
  closed: Promise<{ code: number; reason: string }>
  send: (payload: unknown) => void
  /** 等一条指定类型的协议消息；3s 守卫只用于失败时给出可读诊断 */
  waitFor: (type: string) => Promise<Record<string, unknown>>
  has: (type: string) => boolean
  /** 等第 n 条指定类型的协议消息；3s 守卫只用于失败时给出可读诊断 */
  waitForCount: (type: string, n: number) => Promise<void>
  close: () => void
}

async function connect(
  bridge: { url: string },
  grantId: string,
): Promise<Client> {
  const ws = new WebSocket(
    `${bridge.url}?grant_id=${encodeURIComponent(grantId)}&token=setup-token`,
  )
  const received: Record<string, unknown>[] = []
  const waiters: { type: string; resolve: (msg: Record<string, unknown>) => void }[] = []
  const counters: { type: string; n: number; resolve: () => void }[] = []

  ws.on('message', (data: Buffer) => {
    const msg = JSON.parse(data.toString()) as Record<string, unknown>
    received.push(msg)
    for (const counter of [...counters]) {
      if (counter.type !== msg.type) continue
      if (received.filter((m) => m.type === counter.type).length < counter.n) continue
      counters.splice(counters.indexOf(counter), 1)
      counter.resolve()
    }
    const index = waiters.findIndex((w) => w.type === msg.type)
    if (index >= 0) waiters.splice(index, 1)[0].resolve(msg)
  })

  const closed = new Promise<{ code: number; reason: string }>((resolve) => {
    ws.once('close', (code: number, reason: Buffer) =>
      resolve({ code, reason: reason.toString() }),
    )
  })

  await once(ws, 'open')

  return {
    received,
    closed,
    send: (payload) => ws.send(JSON.stringify(payload)),
    has: (type) => received.some((m) => m.type === type),
    waitForCount: async (type, n) => {
      if (received.filter((m) => m.type === type).length >= n) return
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(
          () =>
            reject(
              new Error(`等待第 ${n} 条 ${type} 超时，已收到: ${JSON.stringify(received)}`),
            ),
          3_000,
        )
        counters.push({
          type,
          n,
          resolve: () => {
            clearTimeout(timer)
            resolve()
          },
        })
      })
    },
    waitFor: (type) => {
      const already = received.find((m) => m.type === type)
      if (already) return Promise.resolve(already)
      return new Promise<Record<string, unknown>>((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error(`等待 ${type} 超时，已收到: ${JSON.stringify(received)}`)),
          3_000,
        )
        waiters.push({
          type,
          resolve: (msg) => {
            clearTimeout(timer)
            resolve(msg)
          },
        })
      })
    },
    close: () => ws.close(),
  }
}

/**
 * 让已在途的后端应答及其后续处理全部落地。
 * setImmediate 是事件循环信号（一次微任务排空），不是猜时长的等待。
 */
function flushAsyncWork(): Promise<void> {
  return new Promise((resolve) => {
    setImmediate(resolve)
  })
}

interface ReadyProbe {
  /** 下一次 ready 上报被调用时 resolve（确定性等待「上报已在途」） */
  nextReport: () => Promise<string>
  /** 下一次后端认领被调用时 resolve（确定性等待「认领在途」） */
  nextClaim: () => Promise<void>
  calls: () => number
  claimCalls: () => number
  offlineCalls: () => number
}

function configureHandlers(
  onReady: (runnerId: string) => Promise<void>,
  claimGate?: Promise<void>,
): ReadyProbe {
  const waiters: ((runnerId: string) => void)[] = []
  const claimWaiters: (() => void)[] = []
  let calls = 0
  let claims = 0
  let offs = 0
  configureRunnerSession({
    onClaim: async (grantId) => {
      claims += 1
      claimWaiters.shift()?.()
      if (claimGate) await claimGate
      return {
        grantId,
        scope: 'read',
        localPath: 'D:/Test',
        enterpriseId: 'ent-1',
        userId: 'usr-1',
      }
    },
    onReady: async (_grantId, runnerId) => {
      calls += 1
      waiters.shift()?.(runnerId)
      await onReady(runnerId)
    },
    onOffline: async () => {
      offs += 1
    },
  })
  return {
    nextClaim: () =>
      new Promise<void>((resolve) => {
        claimWaiters.push(resolve)
      }),
    claimCalls: () => claims,
    nextReport: () =>
      new Promise<string>((resolve) => {
        waiters.push(resolve)
      }),
    calls: () => calls,
    offlineCalls: () => offs,
  }
}

test('成功：后端确认之后才回 ready_ack、才计入在线、才允许下发任务', { timeout: 5_000 }, async (t) => {
  const bridge = await startBridge()
  const gate = deferred()
  const probe = configureHandlers(() => gate.promise)
  const grantId = 'grant-ready-ok'
  const baseline = onlineCount()
  const client = await connect(bridge, grantId)
  // 用例之间必须彻底断开：否则上一条连接仍留在会话池里，污染下一条的在线数基线
  t.after(() => bridge.close())

  await client.waitFor('welcome')
  const reported = probe.nextReport()
  client.send({ type: 'ready', runnerId: 'runner-ok', resolvedPath: 'D:/Test' })
  assert.equal(await reported, 'runner-ok')

  // 后端还没确认：不得就绪、不得计入在线、不得下发任务
  assert.equal(isRunnerReady(grantId), false)
  assert.equal(onlineCount(), baseline)
  assert.equal(client.has('ready_ack'), false)
  await assert.rejects(dispatchTask(grantId, { tool: 'list', path: '.' }), /本地守护进程不在线/)

  gate.resolve()
  await client.waitFor('ready_ack')

  assert.equal(isRunnerReady(grantId), true)
  assert.equal(onlineCount(), baseline + 1)
  assert.equal(probe.calls(), 1)
  assert.equal(getRunnerSession(grantId)?.runnerId, 'runner-ok')
})

test('失败：后端拒绝时回 error 并断开，既不在线也不下发任务', { timeout: 5_000 }, async (t) => {
  const bridge = await startBridge()
  const probe = configureHandlers(() =>
    Promise.reject(new Error('LOCAL_PATH_ACCESS_DENIED')),
  )
  const grantId = 'grant-ready-denied'
  const baseline = onlineCount()
  const client = await connect(bridge, grantId)
  // 用例之间必须彻底断开：否则上一条连接仍留在会话池里，污染下一条的在线数基线
  t.after(() => bridge.close())

  await client.waitFor('welcome')
  const reported = probe.nextReport()
  client.send({ type: 'ready', runnerId: 'runner-denied' })
  await reported

  const error = await client.waitFor('error')
  assert.match(String(error.message), /被后端拒绝/)
  const close = await client.closed
  assert.equal(close.code, 4003)

  assert.equal(client.has('ready_ack'), false, '被拒绝的连接绝不能收到 ready_ack')
  assert.equal(isRunnerReady(grantId), false)
  assert.equal(onlineCount(), baseline)
  assert.equal(runnerCount(), 0, '失败的会话必须移出会话池，同 grant 才能重新配对')
  await assert.rejects(dispatchTask(grantId, { tool: 'read', path: 'a.txt' }), /本地守护进程不在线/)
  // 后端从未记录过 connected，因此不应触发 /offline 上报
  assert.equal(probe.offlineCalls(), 0)
})

test('在途：等待后端应答期间重复 ready 只上报一次', { timeout: 5_000 }, async (t) => {
  const bridge = await startBridge()
  const gate = deferred()
  const probe = configureHandlers(() => gate.promise)
  const grantId = 'grant-ready-inflight'
  const client = await connect(bridge, grantId)
  // 用例之间必须彻底断开：否则上一条连接仍留在会话池里，污染下一条的在线数基线
  t.after(() => bridge.close())

  await client.waitFor('welcome')
  const reported = probe.nextReport()
  client.send({ type: 'ready', runnerId: 'runner-inflight' })
  assert.equal(await reported, 'runner-inflight')

  client.send({ type: 'ready', runnerId: 'runner-inflight' })
  client.send({ type: 'ready', runnerId: 'runner-inflight' })
  await flushAsyncWork()

  assert.equal(probe.calls(), 1, '重复 ready 不得重复上报后端')
  assert.equal(client.has('ready_ack'), false)

  gate.resolve()
  await client.waitFor('ready_ack')
  await flushAsyncWork()
  assert.equal(probe.calls(), 1)
  assert.equal(isRunnerReady(grantId), true)

  // 已就绪后的重复 ready 只做幂等 ack，不再上报后端
  client.send({ type: 'ready', runnerId: 'runner-inflight' })
  await client.waitForCount('ready_ack', 2)
  assert.equal(probe.calls(), 1)
})

test('迟到：socket 断开后到达的成功应答不得复活会话', { timeout: 5_000 }, async (t) => {
  const bridge = await startBridge()
  const gate = deferred()
  const probe = configureHandlers(() => gate.promise)
  const grantId = 'grant-ready-late-closed'
  const baseline = onlineCount()
  const client = await connect(bridge, grantId)
  // 用例之间必须彻底断开：否则上一条连接仍留在会话池里，污染下一条的在线数基线
  t.after(() => bridge.close())

  await client.waitFor('welcome')
  const reported = probe.nextReport()
  client.send({ type: 'ready', runnerId: 'runner-late' })
  await reported

  client.close()
  await client.closed
  await flushAsyncWork()

  gate.resolve()
  await flushAsyncWork()

  assert.equal(probe.calls(), 1)
  assert.equal(isRunnerReady(grantId), false)
  assert.equal(onlineCount(), baseline)
  assert.equal(runnerCount(), 0)
  assert.equal(client.has('ready_ack'), false, '已断开的连接不能被迟到的成功复活')
})

test('迟到：同 grant 被新会话替换后，旧会话的成功应答不得复活或顶替', { timeout: 5_000 }, async (t) => {
  const bridge = await startBridge()
  const oldGate = deferred()
  const newGate = deferred()
  const grantId = 'grant-ready-late-replaced'
  const baseline = onlineCount()
  const probe = configureHandlers((runnerId) =>
    runnerId === 'runner-old' ? oldGate.promise : newGate.promise,
  )

  const oldClient = await connect(bridge, grantId)
  await oldClient.waitFor('welcome')
  const oldReported = probe.nextReport()
  oldClient.send({ type: 'ready', runnerId: 'runner-old' })
  assert.equal(await oldReported, 'runner-old')

  // 旧连接断开 → 清理 → 同 grant 的新会话接管
  oldClient.close()
  await oldClient.closed
  await flushAsyncWork()

  const newClient = await connect(bridge, grantId)
  t.after(() => bridge.close())
  await newClient.waitFor('welcome')
  const newReported = probe.nextReport()
  newClient.send({ type: 'ready', runnerId: 'runner-new' })
  assert.equal(await newReported, 'runner-new')
  newGate.resolve()
  await newClient.waitFor('ready_ack')
  assert.equal(getRunnerSession(grantId)?.runnerId, 'runner-new')

  // 旧会话的应答此刻才到达
  oldGate.resolve()
  await flushAsyncWork()

  assert.equal(isRunnerReady(grantId), true)
  assert.equal(getRunnerSession(grantId)?.runnerId, 'runner-new', '新会话不得被旧应答顶替')
  assert.equal(onlineCount(), baseline + 1, '旧会话复活会把在线数算成 2')
  assert.equal(runnerCount(), 1)
  assert.equal(oldClient.has('ready_ack'), false)
})

test('认领在途时的提前 ready：不触发后端上报，回 error 并断开', { timeout: 5_000 }, async (t) => {
  const bridge = await startBridge()
  const claimGate = deferred()
  const probe = configureHandlers(() => Promise.resolve(), claimGate.promise)
  const grantId = 'grant-ready-before-claim'
  const baseline = onlineCount()
  // claim 在 server connection 时就已被调用，信号必须在 connect 之前注册
  const claiming = probe.nextClaim()
  const client = await connect(bridge, grantId)
  t.after(() => bridge.close())
  await claiming

  // ws 监听在认领结果返回前就已注册：等认领确实在途，再抢跑发 ready
  client.send({ type: 'ready', runnerId: 'runner-early' })

  const error = await client.waitFor('error')
  assert.match(String(error.message), /尚未完成认证/)
  const close = await client.closed
  assert.equal(close.code, 1008)

  assert.equal(probe.calls(), 0, '未认证连接绝不能触发 /connected 上报')
  assert.equal(client.has('ready_ack'), false)
  assert.equal(client.has('welcome'), false)
  assert.equal(isRunnerReady(grantId), false)
  assert.equal(onlineCount(), baseline)
  assert.equal(runnerCount(), 0, '认领在途的占位会话不算在线')
  await assert.rejects(dispatchTask(grantId, { tool: 'list', path: '.' }), /本地守护进程不在线/)

  // 迟到的认领结果不得把已断开的连接拉回在线
  claimGate.resolve()
  await flushAsyncWork()
  assert.equal(probe.claimCalls(), 1)
  assert.equal(isRunnerReady(grantId), false)
  assert.equal(runnerCount(), 0)
  assert.equal(client.has('welcome'), false)
})

test('关闭中的 socket：close 事件/cleanup 触发前也拒绝下发任务', { timeout: 5_000 }, async (t) => {
  const bridge = await startBridge()
  const gate = deferred()
  const probe = configureHandlers(() => gate.promise)
  const grantId = 'grant-ready-closing'
  const client = await connect(bridge, grantId)
  t.after(() => bridge.close())

  await client.waitFor('welcome')
  const reported = probe.nextReport()
  client.send({ type: 'ready', runnerId: 'runner-closing' })
  await reported
  gate.resolve()
  await client.waitFor('ready_ack')
  assert.equal(isRunnerReady(grantId), true)

  // 触发服务端 close 握手：ready 仍是 true，但 socket 已进入 CLOSING，
  // 而 close 事件与 cleanupSession 尚未触发（那要等一次 I/O 往返）
  const session = getRunnerSession(grantId)
  assert.ok(session, '就绪后应能取到服务端会话')
  session!.ws.close()
  assert.equal(session!.ws.readyState, session!.ws.CLOSING)
  assert.equal(session!.ready, true, 'cleanup 还没跑，ready 仍为 true')

  // 这个窗口里 send() 会被静默丢弃：不加守卫就会先受理任务，随后由 cleanup 以
  // 「已断开连接」报错（对端不完成握手时更会挂到超时），必须当场以「不在线」拒绝
  await assert.rejects(dispatchTask(grantId, { tool: 'read', path: 'a.txt' }), /本地守护进程不在线/)
  assert.equal(isRunnerReady(grantId), false, 'dispatchTask 与 isRunnerReady 同口径')

  // 真正收到 close 后会话照常被回收，且不会误报离线（后端确认过 connected）
  await client.closed
  assert.equal(runnerCount(), 0)
  assert.equal(probe.offlineCalls(), 1)
})
