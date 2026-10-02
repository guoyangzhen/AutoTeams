/**
 * 本地连接 · setup 命令状态机（纯函数，供 LocalConnectionPanel 与测试消费）。
 *
 * 一次性配对令牌的真实语义（backend/app/services/runner_session.py::claim_grant）：
 * 服务端在 Runner「认领」（claim）时就把 claimed=True 并清空 token 哈希与过期时间；
 * 而授权记录要等到 mark_connected 才从 pending 变为 connected。于是存在一个前端无法
 * 判别的窗口：状态仍是 pending，但令牌可能已经被消费。
 *
 * 后果必须说清楚：
- pending + 未过期 只能说明「令牌尚未被服务端判为过期」，不能保证它还能连上：
   用户可能已经运行过命令，认领成功但连接未就绪（此时重跑旧命令必然失败）。
- 服务端在认领时就把 claimed 置位，但它可选：旧版服务端不返回该字段，缺失时
   只能按「未认领」处理，因此候选语义在下一次刷新前仍然成立（服务端可能随时改写）。
- claimed === true 是确证：令牌已被消费，无论 status 是否还停在 pending。
   connected / offline / revoked 同样是服务端给出的确证。
 *
 * 恢复路径唯一且明确：撤销该授权后重新「添加」生成新命令。
 * 缺失或无法解析的过期时间按「已过期」处理（fail closed）：宁可让用户重新生成，
 * 也不要把一把已失效的一次性凭据继续展示、允许复制。
 */
import type { LocalPathGrant } from '@/api/localPaths'

/** setup 命令当前的可复制状态。 */
export type SetupCommandState =
  /** 候选可用：授权仍为 pending 且令牌未过期，但无法排除「已被认领尚未连上」。 */
  | { kind: 'candidate'; expiresAt: number }
  /** 令牌已被本机认领消费（含连接后断线），无法再次使用。 */
  | { kind: 'claimed' }
  /** 授权已被撤销。 */
  | { kind: 'revoked' }
  /** 一次性令牌已过期。 */
  | { kind: 'expired' }
  /** 当前列表中查不到该授权：状态未知（不代表令牌已作废）。 */
  | { kind: 'unknown' }

/** 解析一次性令牌的过期时间；缺失或不可解析返回 null（按已过期处理）。 */
export function parseSetupExpiry(setupTokenExpiresAt: string | null | undefined): number | null {
  if (!setupTokenExpiresAt) return null
  const parsed = Date.parse(setupTokenExpiresAt)
  return Number.isNaN(parsed) ? null : parsed
}

/**
 * 依据授权记录现状与令牌过期时间，判定 setup 命令是否还应展示/允许复制。
 *
 * @param grant 当前列表中同 id 的授权记录；查不到即视为状态未知。
 * @param setupTokenExpiresAt 注册接口返回的一次性令牌过期时间（ISO 字符串）。
 * @param now 当前时间戳，便于测试注入。
 */
export function evaluateSetupCommand(
  grant: LocalPathGrant | undefined,
  setupTokenExpiresAt: string | null,
  now: number = Date.now(),
): SetupCommandState {
  if (!grant) return { kind: 'unknown' }
  if (grant.status === 'revoked') return { kind: 'revoked' }
  // claimed 为真即表示令牌已被消费，即使 status 还停在 pending（认领早于 mark_connected）。
  if (grant.claimed === true) return { kind: 'claimed' }
  // connected / offline 都意味着服务端已认领过该令牌，断线后也不会自动重连。
  if (grant.status === 'connected' || grant.status === 'offline') return { kind: 'claimed' }
  const expiresAt = parseSetupExpiry(setupTokenExpiresAt)
  if (expiresAt === null) return { kind: 'expired' }
  if (expiresAt <= now) return { kind: 'expired' }
  return { kind: 'candidate', expiresAt }
}
