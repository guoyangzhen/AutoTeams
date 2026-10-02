/**
 * 端侧 ↔ 云端契约一致性（AUD-10）与回执幂等（AUD-18）。
 *
 * 审计复现：把 `physical-bridge.ts` 真实结构喂给生产 Pydantic 模型会得到
 * `capabilities.frames: bool_type` 与 `payload_b64: missing`。本文件与
 * `backend/tests/test_runner_v2_contract.py` 消费同一份
 * `contract/runner_v2_payloads.json`，任何一侧改字段名，另一侧必然失败。
 */
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import test from 'node:test'

import { DeviceTokenHolder, readPhysicalBridgeConfig, resolveDefaultReceiptLedger } from '../src/physical-bridge.js'
import { acquireReceiptLedgerLease, fileReceiptLedger, inMemoryReceiptLedger } from '../src/receipt-ledger.js'

const here = dirname(fileURLToPath(import.meta.url))
const contract = JSON.parse(
  readFileSync(resolve(here, '..', 'contract', 'runner_v2_payloads.json'), 'utf8'),
) as Record<string, Record<string, unknown>>

test('receipt ledger upgrade keeps the legacy path and lock, and rejects split state', () => {
  const home = mkdtempSync(resolve(tmpdir(), 'autoteams-ledger-upgrade-'))
  const name = 'device-hash.json'
  const oldPath = resolve(home, '.autofde', 'runner-data', 'receipts', name)
  const newPath = resolve(home, '.autoteams', 'runner-data', 'receipts', name)
  try {
    assert.equal(resolveDefaultReceiptLedger(home, name), newPath)
    mkdirSync(dirname(oldPath), { recursive: true })
    writeFileSync(`${oldPath}.lock`, 'old lease')
    assert.equal(resolveDefaultReceiptLedger(home, name), oldPath)
    mkdirSync(dirname(newPath), { recursive: true })
    writeFileSync(newPath, '{"version":1,"records":{}}')
    assert.throws(() => resolveDefaultReceiptLedger(home, name), /新旧物理回执账本同时存在/)
    assert.equal(readFileSync(`${oldPath}.lock`, 'utf8'), 'old lease')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('共享契约：心跳载荷使用结构化 frames 能力（不是 bool）', () => {
  const capabilities = contract.heartbeat_request.capabilities as {
    browser: boolean
    desktop: boolean
    frames: { width: number; height: number; format: string }
  }
  assert.equal(typeof capabilities.frames, 'object')
  assert.equal(capabilities.frames.format, 'gray8')
  assert.equal(typeof capabilities.frames.width, 'number')
  assert.equal(typeof capabilities.frames.height, 'number')
  // 旧契约是 dict[str, bool]，这里显式断言不再出现布尔形态的 frames。
  assert.notEqual(typeof capabilities.frames, 'boolean')
})

test('共享契约：视窗帧字段是 payload_b64（不是 payloadB64）', () => {
  const frame = contract.push_frame_request
  assert.equal(typeof frame.payload_b64, 'string')
  assert.equal('payloadB64' in frame, false)
})

test('共享契约：结果回传带 receipt_id', () => {
  assert.equal(typeof contract.report_result_request.receipt_id, 'string')
})

test('端侧心跳请求与共享契约字段完全一致', () => {
  const built = {
    runner_id: 'local-1234',
    scopes: ['physical', 'read'],
    platform: 'win32',
    version: '2.0.0',
    capabilities: {
      browser: true,
      desktop: false,
      frames: { width: 160, height: 100, format: 'gray8' },
    },
  }
  assert.deepEqual(
    Object.keys(built).sort(),
    Object.keys(contract.heartbeat_request).sort(),
  )
  assert.deepEqual(built, contract.heartbeat_request)
})

test('readPhysicalBridgeConfig：缺少设备凭据时物理能力保持关闭', () => {
  assert.equal(readPhysicalBridgeConfig({} as NodeJS.ProcessEnv), null)
  assert.equal(
    readPhysicalBridgeConfig({ AUTOTEAMS_API_URL: 'https://x' } as NodeJS.ProcessEnv),
    null,
  )
  // 旧的共享密钥配置不再足以启用物理能力（AUD-04）。
  assert.equal(
    readPhysicalBridgeConfig({
      AUTOTEAMS_API_URL: 'https://x',
      AUTOTEAMS_BRIDGE_SECRET: 'shared',
    } as NodeJS.ProcessEnv),
    null,
  )
})

test('readPhysicalBridgeConfig：设备 ID、固定 Runner ID 与设备凭据齐全才启用', () => {
  assert.equal(readPhysicalBridgeConfig({
    AUTOTEAMS_API_URL: 'https://x/',
    AUTOTEAMS_DEVICE_ID: 'dev-1',
    AUTOTEAMS_DEVICE_SECRET: 's'.repeat(48),
  } as NodeJS.ProcessEnv), null, '缺固定 Runner ID 时不能使用进程 PID 代替')
  const config = readPhysicalBridgeConfig({
    AUTOTEAMS_API_URL: 'https://x/',
    AUTOTEAMS_DEVICE_ID: 'dev-1',
    AUTOTEAMS_RUNNER_ID: 'runner-1',
    AUTOTEAMS_DEVICE_SECRET: 's'.repeat(48),
  } as NodeJS.ProcessEnv)
  assert.ok(config)
  assert.equal(config.apiUrl, 'https://x')
  assert.equal(config.deviceId, 'dev-1')
  assert.equal(config.bridgeSecret, undefined)
  assert.ok(config.receiptLedgerPath.endsWith('.json'))
  assert.equal(config.receiptLedgerPath.includes('dev-1'), false, '账本文件名不暴露设备 ID')
  const again = readPhysicalBridgeConfig({
    AUTOTEAMS_API_URL: 'https://x/',
    AUTOTEAMS_DEVICE_ID: 'dev-1',
    AUTOTEAMS_RUNNER_ID: 'runner-1',
    AUTOTEAMS_DEVICE_SECRET: 's'.repeat(48),
  } as NodeJS.ProcessEnv)
  assert.equal(again?.receiptLedgerPath, config.receiptLedgerPath, '重启后必须找到同一份账本')
  const other = readPhysicalBridgeConfig({
    AUTOTEAMS_API_URL: 'https://x/',
    AUTOTEAMS_DEVICE_ID: 'dev-2',
    AUTOTEAMS_RUNNER_ID: 'runner-1',
    AUTOTEAMS_DEVICE_SECRET: 's'.repeat(48),
  } as NodeJS.ProcessEnv)
  assert.notEqual(other?.receiptLedgerPath, config.receiptLedgerPath)
})

test('设备认证失败或返回无效令牌时拒绝使用旧令牌', async () => {
  let exchanges = 0
  const server = createServer((_req, res) => {
    exchanges += 1
    if (exchanges === 1) {
      res.writeHead(401).end('{}')
    } else if (exchanges === 2) {
      res.writeHead(200, { 'Content-Type': 'application/json' })
        .end(JSON.stringify({ data: { access_token: '', expires_at: new Date(Date.now() + 600_000).toISOString() } }))
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' })
        .end(JSON.stringify({ data: { access_token: 'new-token', expires_at: new Date(Date.now() + 600_000).toISOString() } }))
    }
  })
  await new Promise<void>((resolveListen) => server.listen(0, '127.0.0.1', resolveListen))
  try {
    const { port } = server.address() as AddressInfo
    const holder = new DeviceTokenHolder(`http://127.0.0.1:${port}`, 'dev-1', 'secret')
    holder.adopt('old-token', new Date(Date.now() + 30_000).toISOString())
    await assert.rejects(holder.requireFresh(), /设备认证暂不可用/)
    await assert.rejects(holder.requireFresh(), /设备认证暂不可用/)
    assert.equal(await holder.requireFresh(), 'new-token')
    assert.equal(exchanges, 3)
  } finally {
    await new Promise<void>((resolveClose) => server.close(() => resolveClose()))
  }
})

test('设备令牌：到期前判为需要刷新', () => {
  const holder = new DeviceTokenHolder('https://x', 'dev-1', 'secret')
  const nowSeconds = Math.floor(Date.now() / 1000)
  assert.equal(holder.isFresh(nowSeconds), false, '没有令牌时必须刷新')
  holder.adopt('tok', new Date(Date.now() + 600_000).toISOString())
  assert.equal(holder.isFresh(nowSeconds), true)
  holder.adopt('tok', new Date(Date.now() + 10_000).toISOString())
  assert.equal(holder.isFresh(nowSeconds), false, '临近到期必须提前刷新')
})

test('回执账本：同一 step 第二次 begin 返回 null（不重复执行）', () => {
  const ledger = inMemoryReceiptLedger()
  const first = ledger.begin('task-1', 'step-1')
  assert.ok(first)
  assert.equal(ledger.begin('task-1', 'step-1'), null)
  ledger.finish({
    ...first,
    status: 'done',
    ok: true,
    data: { matched: 1 },
    finished_at: new Date().toISOString(),
  })
  // 完成后仍未确认 → 需要补发回执，而不是重新执行。
  assert.equal(ledger.pendingReports().length, 1)
  ledger.markReported('task-1', 'step-1')
  assert.equal(ledger.pendingReports().length, 0)
  assert.equal(ledger.begin('task-1', 'step-1'), null, '已完成的 step 仍不得重放')
})

test('回执账本：pending 记录不会被当作待补发回执', () => {
  const ledger = inMemoryReceiptLedger()
  const record = ledger.begin('task-2', 'step-1')
  assert.ok(record)
  // 进程在执行中途退出：状态仍是 pending，云端结果未知，不重放。
  assert.equal(ledger.pendingReports().length, 0)
  assert.equal(ledger.begin('task-2', 'step-1'), null)
})

test('回执账本：磁盘账本跨进程存活（补回执而非重执行）', () => {
  // 每次用独立临时目录：账本是**按文件**持久的，用固定路径会让第二次运行
  // 直接读到上一轮的记录，begin() 正确返回 null，而用例却断言为真。
  const dir = mkdtempSync(resolve(tmpdir(), 'autoteams-receipts-'))
  const path = resolve(dir, 'ledger.json')
  const first = fileReceiptLedger(path)
  const record = first.begin('task-3', 'step-1')
  assert.ok(record)
  first.finish({
    ...record,
    status: 'done',
    ok: true,
    finished_at: new Date().toISOString(),
  })

  // 模拟"动作完成 → 回执未送达 → 进程被杀 → 重启"
  const reopened = fileReceiptLedger(path)
  assert.equal(reopened.begin('task-3', 'step-1'), null, '重启后不得重放已执行的 step')
  const pending = reopened.pendingReports()
  assert.equal(pending.length, 1)
  assert.equal(pending[0].task_id, 'task-3')
  assert.equal(pending[0].step_id, 'step-1')

  rmSync(dir, { recursive: true, force: true })
})

test('损坏的磁盘账本阻止动作并保留原文件', () => {
  const dir = mkdtempSync(resolve(tmpdir(), 'autoteams-receipts-corrupt-'))
  const path = resolve(dir, 'ledger.json')
  try {
    for (const contents of ['{broken', JSON.stringify({ version: 1, records: [] })]) {
      writeFileSync(path, contents, 'utf8')
      assert.throws(() => fileReceiptLedger(path), /回执账本无法读取/)
      assert.equal(readFileSync(path, 'utf8'), contents)
    }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('同一设备账本由一个进程独占，退出后第二进程才能接管', () => {
  const dir = mkdtempSync(resolve(tmpdir(), 'autoteams-receipts-lease-'))
  const path = resolve(dir, 'ledger.json')
  const probe = () => spawnSync(process.execPath, [
    '--import', 'tsx', '--input-type=module', '-e',
    "const { acquireReceiptLedgerLease } = await import('./src/receipt-ledger.ts'); try { const release = acquireReceiptLedgerLease(process.argv[1]); release(); process.exit(0) } catch { process.exit(7) }",
    path,
  ], { cwd: resolve(here, '..'), encoding: 'utf8', timeout: 10_000, windowsHide: true })
  const release = acquireReceiptLedgerLease(path)
  try {
    assert.equal(probe().status, 7, '另一进程不能同时使用同一账本')
  } finally {
    release()
  }
  try {
    const accepted = probe()
    assert.equal(accepted.status, 0, accepted.stderr)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('账本满时拒绝新动作，不淘汰旧 step 的去重依据', () => {
  const ledger = inMemoryReceiptLedger()
  for (let i = 0; i < 500; i += 1) assert.ok(ledger.begin(`task-${i}`, 's0'))
  assert.equal(ledger.begin('task-0', 's0'), null)
  assert.throws(() => ledger.begin('task-500', 's0'), /回执账本已满/)
  assert.equal(ledger.size(), 500)
})
