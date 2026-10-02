import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdirSync, readFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'

import { executeTask, initLocal } from '../src/executor.js'

function createRoot(): string {
  // 使用仓库已忽略的测试目录，避免将系统临时目录误授权给 Runner。
  const root = join(process.cwd(), '_e2e_local', `runner-${randomUUID()}`)
  mkdirSync(join(root, 'nested'), { recursive: true })
  return root
}

async function task(tool: string, path = '', content: string | null = null) {
  return executeTask({
    taskId: `task-${Date.now()}-${Math.random()}`,
    tool,
    path,
    content,
  })
}

test('Runner manifest excludes cli and agentic even for read_write grants', () => {
  const root = createRoot()
  try {
    const ready = initLocal(root, 'read_write')
    assert.deepEqual(ready.toolManifest.tools, ['list', 'read', 'write', 'delete'])
    assert.equal(ready.toolManifest.executionPolicy, 'file_operations_only')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('Runner rejects cli and agentic execution requests before any process can start', async () => {
  const root = createRoot()
  try {
    initLocal(root, 'read_write')

    const cliResult = await task('cli', '', null)
    assert.equal(cliResult.ok, false)
    assert.match(cliResult.error || '', /禁止通用命令/)

    const agenticResult = await task('agentic', '', null)
    assert.equal(agenticResult.ok, false)
    assert.match(agenticResult.error || '', /禁止通用命令/)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('Runner keeps write operations inside the authorized root', async () => {
  const root = createRoot()
  try {
    initLocal(root, 'read_write')
    const result = await task('write', 'nested/report.txt', '安全文件内容')
    assert.equal(result.ok, true)
    assert.equal(readFileSync(join(root, 'nested', 'report.txt'), 'utf8'), '安全文件内容')

    const escaped = await task('write', '../escape.txt', 'must fail')
    assert.equal(escaped.ok, false)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('Runner denies write operations for read-only grants', async () => {
  const root = createRoot()
  try {
    initLocal(root, 'read')
    const result = await task('write', 'report.txt', 'must fail')
    assert.equal(result.ok, false)
    assert.match(result.error || '', /只读/)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
