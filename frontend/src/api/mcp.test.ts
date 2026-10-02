import { describe, expect, it } from 'vitest'
import { assertRunnerToolArguments } from './mcp'

/**
 * 后端收紧端侧工具契约后的前端前置校验（对应
 * backend/app/services/mcp/runner_bridge.py）：
 * 文件读取必须绑定 `grant_id`（用户自己的本地路径授权记录）+ 授权目录内的相对路径；
 * 旧参数 `device_id` / `file_path` 已废弃；未接入的能力不得按成功展示。
 */

describe('assertRunnerToolArguments', () => {
  it('runner_read_file 接受 grant_id + 相对路径', () => {
    expect(() =>
      assertRunnerToolArguments('runner_read_file', {
        grant_id: 'grant-1',
        relative_path: 'reports/2026-09.md',
      }),
    ).not.toThrow()
  })

  it('runner_read_file 拒绝已废弃的 file_path', () => {
    expect(() =>
      assertRunnerToolArguments('runner_read_file', {
        grant_id: 'grant-1',
        file_path: '/etc/passwd',
      }),
    ).toThrow(/file_path/)
  })

  it('runner_read_file 拒绝缺少 grant_id', () => {
    expect(() =>
      assertRunnerToolArguments('runner_read_file', { relative_path: 'a.txt' }),
    ).toThrow(/grant_id/)
  })

  it('runner_read_file 拒绝只有已废弃的 device_id', () => {
    expect(() =>
      assertRunnerToolArguments('runner_read_file', {
        device_id: 'device-1',
        relative_path: 'a.txt',
      }),
    ).toThrow(/grant_id/)
  })

  it('runner_read_file 拒绝绝对路径与任何 .. 段', () => {
    for (const path of [
      '/etc/passwd',
      'C:\\Windows\\win.ini',
      '../outside.txt',
      'a/../../b',
      // 与后端一致：中间出现 .. 也要拒绝，不做"归一化后不越界"的宽松处理
      'a/../b',
      'docs/./../secret.txt',
    ]) {
      expect(() =>
        assertRunnerToolArguments('runner_read_file', { grant_id: 'g1', relative_path: path }),
      ).toThrow(/路径不合法/)
    }
  })

  it('runner_read_file 接受普通的相对路径', () => {
    for (const path of ['a.txt', 'docs/a.txt', './a.txt', 'a b/c.txt']) {
      expect(() =>
        assertRunnerToolArguments('runner_read_file', { grant_id: 'g1', relative_path: path }),
      ).not.toThrow()
    }
  })

  it('runner_system_probe 直接报「未实现」，不发无意义请求', () => {
    expect(() => assertRunnerToolArguments('runner_system_probe', {})).toThrow(/未实现/)
  })

  it('runner_execute_script 直接报「未实现」，不发无意义请求', () => {
    expect(() =>
      assertRunnerToolArguments('runner_execute_script', { command: 'ls' }),
    ).toThrow(/未实现/)
  })

  it('非端侧工具不做额外校验', () => {
    expect(() => assertRunnerToolArguments('codex__write_file', { path: '/tmp/x' })).not.toThrow()
  })
})
