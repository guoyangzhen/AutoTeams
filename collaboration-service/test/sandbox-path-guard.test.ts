/**
 * AUD-02：会话路径守卫。
 *
 * 复现路径与 docs/TECHNICAL_AUDIT_VALIDATION_2026-09-28.md §6.1 一致：
 * 会话目录 session-A 与同级 marker 文件都在临时目录下，模型给出的 `../` 路径
 * 在 SDK 内置工具下可以读到 marker；这里要求守卫把它挡在会话根之外。
 */
import assert from 'node:assert/strict'
import { SessionManager, createAgentSession, createReadTool } from '@earendil-works/pi-coding-agent'
import { mkdir, mkdtemp, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { SandboxPathError, isPathInside, resolveWithinRoot, resolveWithinRootSync } from '../src/path-guard.js'
import { SANDBOX_TOOL_NAMES, createSandboxTools, loadSandboxBashPolicy } from '../src/sandbox-tools.js'

const tmpRoot = await mkdtemp(join(tmpdir(), 'collab-sandbox-'))
const sessionRoot = join(tmpRoot, 'session-A')
const outsideDir = join(tmpRoot, 'other-session')
const markerFile = join(tmpRoot, 'other-session-marker.txt')

await mkdir(join(sessionRoot, 'sub'), { recursive: true })
await mkdir(outsideDir, { recursive: true })
await writeFile(markerFile, 'AUDIT_OTHER_SESSION_SYNTHETIC_MARKER', 'utf8')
await writeFile(join(outsideDir, 'secret.txt'), 'OTHER_SESSION_SECRET', 'utf8')
await writeFile(join(sessionRoot, 'notes.md'), 'IN_ROOT_CONTENT', 'utf8')

// Windows 上普通符号链接需要特权，目录 junction 不需要，因此用 junction 制造逃逸链接。
const escapeLink = join(sessionRoot, 'escape')
const insideLink = join(sessionRoot, 'inside-link')
let junctionSupported = true
try {
  await symlink(outsideDir, escapeLink, process.platform === 'win32' ? 'junction' : 'dir')
  await symlink(join(sessionRoot, 'sub'), insideLink, process.platform === 'win32' ? 'junction' : 'dir')
} catch {
  junctionSupported = false
}

test('会话目录内的相对/绝对路径被接受', async () => {
  const relative = await resolveWithinRoot(sessionRoot, 'notes.md')
  assert.ok(isPathInside(await resolveWithinRoot(sessionRoot, '.'), relative))
  assert.equal(await resolveWithinRootSync(sessionRoot, 'notes.md'), relative)

  const absolute = await resolveWithinRoot(sessionRoot, join(sessionRoot, 'sub', 'new-file.txt'))
  assert.ok(isPathInside(await resolveWithinRoot(sessionRoot, '.'), absolute))
})

test('`../` 逃逸被拒绝', async () => {
  await assert.rejects(() => resolveWithinRoot(sessionRoot, '../other-session-marker.txt'), SandboxPathError)
  await assert.rejects(() => resolveWithinRoot(sessionRoot, 'sub/../../other-session-marker.txt'), SandboxPathError)
  await assert.rejects(async () => resolveWithinRootSync(sessionRoot, '../other-session-marker.txt'), SandboxPathError)
})

test('会话目录之外的绝对路径被拒绝', async () => {
  await assert.rejects(() => resolveWithinRoot(sessionRoot, markerFile), SandboxPathError)
  await assert.rejects(() => resolveWithinRoot(sessionRoot, join(outsideDir, 'secret.txt')), SandboxPathError)
})

test('指向会话目录之外的符号链接被拒绝，指向内部的符号链接仍可用', { skip: !junctionSupported }, async () => {
  await assert.rejects(() => resolveWithinRoot(sessionRoot, 'escape/secret.txt'), SandboxPathError)
  await assert.doesNotReject(() => resolveWithinRoot(sessionRoot, 'inside-link/../notes.md'))
  const insideTarget = await resolveWithinRoot(sessionRoot, 'inside-link')
  assert.match(insideTarget, /sub$/)
})

test('空路径与不存在的读目标被拒绝', async () => {
  await assert.rejects(() => resolveWithinRoot(sessionRoot, '   '), SandboxPathError)
  await assert.rejects(() => resolveWithinRoot(sessionRoot, 'missing.md', { mustExist: true }), SandboxPathError)
})

test('SDK 内置工具同名覆盖后，read/write 无法越出会话目录', async () => {
  const tools = createSandboxTools(sessionRoot, loadSandboxBashPolicy({}))
  const read = tools.find((tool) => tool.name === 'read')!
  const write = tools.find((tool) => tool.name === 'write')!
  assert.ok(read && write, '期望装配出 read/write 工具')

  // 对照组：未经守卫的 SDK 内置工具确实能读出会话目录之外的文件（AUD-02 / 验证记录 §6.1）。
  const bareRead = createReadTool(sessionRoot)
  const bareResult = await bareRead.execute('bare-call', { path: '../other-session-marker.txt' }, undefined, undefined, undefined as never)
  assert.match(JSON.stringify(bareResult.content), /AUDIT_OTHER_SESSION_SYNTHETIC_MARKER/)

  const call = (tool: typeof read, params: unknown) =>
    tool.execute('test-call', params as never, undefined, undefined, undefined as never)

  // 允许：会话目录内的文件
  const inside = await call(read, { path: 'notes.md' })
  assert.match(JSON.stringify(inside.content), /IN_ROOT_CONTENT/)

  // 拒绝：跨会话读取
  await assert.rejects(() => call(read, { path: '../other-session-marker.txt' }), SandboxPathError)

  // 拒绝：写入会话目录之外
  await assert.rejects(() => call(write, { path: '../escaped.txt', content: 'nope' }), SandboxPathError)
  await assert.rejects(() => call(write, { path: 'sub/../../escaped.txt', content: 'nope' }), SandboxPathError)

  // 允许：写入会话目录内的新文件
  await call(write, { path: 'sub/report.md', content: 'ok' })
  await assert.doesNotReject(() => resolveWithinRoot(sessionRoot, 'sub/report.md', { mustExist: true }))
})

test('真实 AgentSession 中生效的是受控工具（覆盖 SDK 内置实现）', async () => {
  const agentDir = join(tmpRoot, 'agent-dir')
  await mkdir(agentDir, { recursive: true })
  const { session } = await createAgentSession({
    tools: [...SANDBOX_TOOL_NAMES],
    customTools: createSandboxTools(sessionRoot, loadSandboxBashPolicy({})),
    sessionManager: SessionManager.inMemory(),
    cwd: sessionRoot,
    agentDir,
  })
  assert.deepEqual(session.getActiveToolNames().sort(), ['bash', 'edit', 'read', 'write'])

  const read = session.getToolDefinition('read')!
  const call = (path: string) =>
    read.execute('session-call', { path }, undefined, undefined, undefined as never)
  await assert.rejects(() => call('../other-session-marker.txt'), SandboxPathError)
  const inside = await call('notes.md')
  assert.match(JSON.stringify(inside.content), /IN_ROOT_CONTENT/)
})
