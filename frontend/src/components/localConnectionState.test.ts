import { describe, expect, it } from 'vitest'
import { evaluateSetupCommand, parseSetupExpiry } from './localConnectionState'
import type { LocalPathGrant } from '@/api/localPaths'

/**
 * 一次性配对命令状态机的语义边界。
 *
 * 真实契约（backend/app/services/runner_session.py）：claim_grant 在「认领」时就消费
 * 令牌，mark_connected 才把状态改成 connected —— 所以 pending 无法区分「未使用」与
 * 「已认领未连上」。服务端会在列表里给出 claimed（可选字段，旧版不返回）：
 * claimed=true 是确证，缺失或 false 时 pending 仍只是候选——服务端可在刷新前改写。
 */

const NOW = Date.parse('2026-09-30T10:00:00Z')
const FUTURE = '2026-09-30T10:10:00Z'
const PAST = '2026-09-30T09:50:00Z'

function grant(over: Partial<LocalPathGrant> = {}): LocalPathGrant {
  return {
    id: 'grant-1',
    enterprise_id: 'ent-1',
    user_id: 'user-1',
    label: '销售资料库',
    local_path: 'D:\\项目\\销售资料',
    scope: 'read_write',
    status: 'pending',
    runner_id: null,
    tool_manifest: null,
    resolved_path: null,
    created_at: '2026-09-30T09:59:00Z',
    updated_at: '2026-09-30T09:59:00Z',
    ...over,
  }
}

describe('parseSetupExpiry', () => {
  it('缺失或不可解析返回 null（调用方据此按已过期处理）', () => {
    expect(parseSetupExpiry(null)).toBeNull()
    expect(parseSetupExpiry('')).toBeNull()
    expect(parseSetupExpiry('not-a-date')).toBeNull()
  })

  it('可解析时返回毫秒时间戳', () => {
    expect(parseSetupExpiry(FUTURE)).toBe(Date.parse(FUTURE))
  })
})

describe('evaluateSetupCommand', () => {
  it('pending 且未过期只是「候选可用」，不保证能连上', () => {
    expect(evaluateSetupCommand(grant(), FUTURE, NOW)).toEqual({
      kind: 'candidate',
      expiresAt: Date.parse(FUTURE),
    })
  })

  it.each(['connected', 'offline'] as const)('%s 表示令牌已被认领消费', (status) => {
    expect(evaluateSetupCommand(grant({ status }), FUTURE, NOW).kind).toBe('claimed')
  })

  it('撤销后命令作废', () => {
    expect(evaluateSetupCommand(grant({ status: 'revoked' }), FUTURE, NOW).kind).toBe('revoked')
  })

  it('过期后命令作废（边界：恰好到期即视为失效）', () => {
    expect(evaluateSetupCommand(grant(), PAST, NOW).kind).toBe('expired')
    expect(evaluateSetupCommand(grant(), new Date(NOW).toISOString(), NOW).kind).toBe('expired')
  })

  it('缺失或无法解析的过期时间按已过期处理', () => {
    expect(evaluateSetupCommand(grant(), null, NOW).kind).toBe('expired')
    expect(evaluateSetupCommand(grant(), 'not-a-date', NOW).kind).toBe('expired')
  })

  it('列表里查不到该授权时状态未知，不当作已作废', () => {
    expect(evaluateSetupCommand(undefined, FUTURE, NOW).kind).toBe('unknown')
  })

  it('claimed 为真时立即作废：状态仍是 pending 也不能再展示或复制命令', () => {
    expect(evaluateSetupCommand(grant({ claimed: true }), FUTURE, NOW)).toEqual({ kind: 'claimed' })
    // claimed 优先于过期判定：无论令牌是否仍在有效期内，它都已经不可用。
    expect(evaluateSetupCommand(grant({ claimed: true }), PAST, NOW).kind).toBe('claimed')
  })

  it('claimed 缺失或为 false 时仍是候选：服务端可在下一次刷新前改写', () => {
    expect(evaluateSetupCommand(grant({ claimed: false }), FUTURE, NOW).kind).toBe('candidate')
    expect(evaluateSetupCommand(grant(), FUTURE, NOW).kind).toBe('candidate')
  })
})
