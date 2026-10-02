/**
 * 具身物理执行面端侧护栏测试（Local Runner 2.0）。
 *
 * 覆盖端侧第二道物理沙箱：凭据代填过滤器、作用域与跳转面收敛、高危双因子判定，
 * 以及视窗灰度帧编码的确定性。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import {
  configureDesktopAdapter,
  encodeGrayscaleFrame,
  evaluatePhysicalTask,
  executePhysicalTask,
  fileCredentialStore,
  FRAME_HEIGHT,
  FRAME_WIDTH,
  type PhysicalContext,
  type PhysicalTask,
} from '../src/physical.js'

const VAULT = {
  'vault://erp/prod/password': 'S3cret-From-Local-Vault',
  'vault://erp/prod/otp': '482913',
}

function context(scopes: string[], approved = false): PhysicalContext {
  return {
    scopes: new Set(scopes),
    credentials: { resolve: (ref: string) => VAULT[ref] ?? null },
    isTwoFactorApproved: () => approved,
  }
}

test('端侧护栏阻断正文中的明文凭据', () => {
  const task: PhysicalTask = {
    taskId: 'ptask-1',
    channel: 'browser_action',
    steps: [
      { op: 'navigate', url: 'https://erp.example.com/login' },
      { op: 'fill_table', rows: [{ username: 'svc-erp', password: 'P@ssw0rd-2026' }] },
    ],
  }
  const verdict = evaluatePhysicalTask(task, context(['physical']))
  assert.equal(verdict.action, 'block')
  assert.equal(verdict.rule, 'tri_rule_credential_autofill')
})

test('端侧护栏允许凭据机代填并拒绝明文回退', async () => {
  const task: PhysicalTask = {
    taskId: 'ptask-2',
    channel: 'browser_action',
    steps: [
      {
        op: 'fill_table',
        rows: [{ username: 'svc-erp', password: '' }],
        credential_refs: { password: 'vault://erp/prod/password' },
      },
    ],
  }
  assert.equal(evaluatePhysicalTask(task, context(['physical'])).action, 'allow')

  const missingRef: PhysicalTask = {
    taskId: 'ptask-3',
    channel: 'browser_action',
    steps: [{ op: 'fill_table', rows: [{ password: '' }] }],
  }
  assert.equal(evaluatePhysicalTask(missingRef, context(['physical'])).rule, 'tri_rule_credential_autofill')
})

test('未解析的凭据引用在执行期失败，不回退到明文', async () => {
  configureDesktopAdapter({
    readTree: async () => [],
    focus: async () => undefined,
    click: async () => undefined,
    inputText: async () => undefined,
    invoke: async () => undefined,
    captureFrame: async () => null,
  })
  try {
    const result = await executePhysicalTask(
      {
        taskId: 'ptask-4',
        channel: 'desktop_accessibility',
        steps: [
          {
            op: 'input_text',
            target: 'Window/PasswordField',
            credential_ref: 'vault://erp/prod/unknown-key',
          },
        ],
      },
      context(['physical']),
    )
    assert.equal(result.ok, false)
    assert.match(result.error || '', /credential_unavailable/)
  } finally {
    configureDesktopAdapter(null)
  }
})

test('端侧护栏收敛作用域与跳转面', () => {
  const noScope: PhysicalTask = {
    taskId: 'ptask-5',
    channel: 'browser_action',
    steps: [{ op: 'click', target: '#submit' }],
  }
  assert.equal(evaluatePhysicalTask(noScope, context(['read'])).rule, 'tri_rule_scope')

  const fileScheme: PhysicalTask = {
    taskId: 'ptask-6',
    channel: 'browser_action',
    steps: [{ op: 'navigate', url: 'file:///C:/Windows/System32/config' }],
  }
  assert.equal(evaluatePhysicalTask(fileScheme, context(['physical'])).rule, 'tri_rule_navigation')

  const crossChannel: PhysicalTask = {
    taskId: 'ptask-7',
    channel: 'browser_action',
    steps: [{ op: 'read_tree' }],
  }
  assert.equal(evaluatePhysicalTask(crossChannel, context(['physical'])).rule, 'tri_rule_channel_surface')
})

test('高危操作在未收到双因子放行标记时拒绝执行', async () => {
  const task: PhysicalTask = {
    taskId: 'ptask-8',
    channel: 'browser_action',
    steps: [
      { op: 'navigate', url: 'https://erp.example.com/transfer' },
      { op: 'click', target: '#submit', risk: 'high' },
    ],
  }
  const verdict = evaluatePhysicalTask(task, context(['physical']))
  assert.equal(verdict.action, 'require_2fa')

  const denied = await executePhysicalTask(task, context(['physical'], false))
  assert.equal(denied.ok, false)
  assert.match(denied.error || '', /双因子放行标记/)
})

test('驱动缺失时明确报错，不伪造执行结果', async () => {
  configureDesktopAdapter(null)
  const result = await executePhysicalTask(
    {
      taskId: 'ptask-9',
      channel: 'desktop_accessibility',
      steps: [{ op: 'read_tree' }],
    },
    context(['physical']),
  )
  assert.equal(result.ok, false)
  assert.match(result.error || '', /driver_unavailable/)
})

test('辅助功能树读取只回传结构化节点', async () => {
  configureDesktopAdapter({
    readTree: async () => [{ role: 'Window', name: 'SAP Logon', path: 'Root/Window[0]' }],
    focus: async () => undefined,
    click: async () => undefined,
    inputText: async () => undefined,
    invoke: async () => undefined,
    captureFrame: async () => null,
  })
  try {
    const result = await executePhysicalTask(
      { taskId: 'ptask-10', channel: 'desktop_accessibility', steps: [{ op: 'read_tree' }] },
      context(['read']),
    )
    assert.equal(result.ok, true)
    assert.equal(result.data?.type, 'read_tree')
    assert.deepEqual(result.data?.nodes, [
      { role: 'Window', name: 'SAP Logon', path: 'Root/Window[0]' },
    ])
  } finally {
    configureDesktopAdapter(null)
  }
})
test('灰度帧编码尺寸固定、均匀画面无损且渐变单调', () => {
  const width = 64
  const height = 32
  const uniform = new Uint8ClampedArray(width * height * 4)
  for (let i = 0; i < width * height; i += 1) {
    uniform[i * 4] = 128
    uniform[i * 4 + 1] = 128
    uniform[i * 4 + 2] = 128
    uniform[i * 4 + 3] = 255
  }
  const flat = encodeGrayscaleFrame(uniform, width, height)
  assert.equal(flat.width, FRAME_WIDTH)
  assert.equal(flat.height, FRAME_HEIGHT)
  assert.equal(flat.kind, 'keyframe')

  const flatPixels = Buffer.from(flat.payloadB64, 'base64')
  assert.equal(flatPixels.length, FRAME_WIDTH * FRAME_HEIGHT)
  // 均匀画面降采样后必须逐像素保持原值（面积平均无损）
  assert.equal(flatPixels.every((value) => value === 128), true)

  // 水平渐变：输出首行必须单调不减，且落在源图灰度范围内
  const gradient = new Uint8ClampedArray(width * height * 4)
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const value = Math.round((x / (width - 1)) * 255)
      const offset = (y * width + x) * 4
      gradient[offset] = value
      gradient[offset + 1] = value
      gradient[offset + 2] = value
      gradient[offset + 3] = 255
    }
  }
  const ramp = encodeGrayscaleFrame(gradient, width, height)
  const rampPixels = Buffer.from(ramp.payloadB64, 'base64')
  const firstRow = Array.from(rampPixels.subarray(0, FRAME_WIDTH))
  assert.equal(firstRow[0], 0)
  assert.equal(firstRow[FRAME_WIDTH - 1], 255)
  for (let x = 1; x < FRAME_WIDTH; x += 1) {
    assert.equal(firstRow[x] >= firstRow[x - 1], true)
  }

  const repeat = encodeGrayscaleFrame(uniform, width, height, flat.digest)
  assert.equal(repeat.kind, 'delta')
  assert.equal(repeat.digest, flat.digest)
})

test('本机凭据机按 vault 引用解析，缺失路径返回 null', () => {
  const dir = join(process.cwd(), '_e2e_local', `vault-${randomUUID()}`)
  mkdirSync(dir, { recursive: true })
  const vaultPath = join(dir, 'vault.json')
  writeFileSync(vaultPath, JSON.stringify({ erp: { prod: { password: 'S3cret-From-Local-Vault' } } }))
  try {
    const store = fileCredentialStore(vaultPath)
    assert.equal(store.resolve('vault://erp/prod/password'), 'S3cret-From-Local-Vault')
    assert.equal(store.resolve('vault://erp/prod/missing'), null)
    assert.equal(store.resolve('not-a-vault-ref'), null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

