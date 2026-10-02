/**
 * 数字员工工号（employee badge）统一展示口径。
 *
 * 后端 `backend/app/services/workforce/profile_service.py` 生成工号时已带
 * `ATE-` 前缀（`ATE-{年}-{部门码}-{序号}`），因此对真实接口数据而言
 * `formatEmployeeBadge` 是幂等的；补前缀分支只服务于历史迁移行与测试夹具，
 * 避免同一批员工在不同页面出现「有的带 ATE-、有的不带」的不一致。
 */

/** 工号前缀，与后端生成规则保持一致。 */
const BADGE_PREFIX = 'ATE-'

/** 无工号且无 id 时的占位工号。 */
const PLACEHOLDER_BADGE = 'ATE-2026-001'

/**
 * 统一工号展示：保证返回值始终以 `ATE-` 开头。
 *
 * @param badge 后端返回的 employee_badge，可能为空或缺少前缀
 * @param fallbackId 档案 id，工号缺失时用其前 4 位派生一个稳定占位
 */
export function formatEmployeeBadge(
  badge: string | null | undefined,
  fallbackId?: string,
): string {
  const raw = (badge ?? '').trim()
  if (!raw) {
    return fallbackId ? `${BADGE_PREFIX}${fallbackId.slice(0, 4).toUpperCase()}` : PLACEHOLDER_BADGE
  }
  if (raw.startsWith(BADGE_PREFIX)) return raw
  return `${BADGE_PREFIX}${raw}`
}
