/**
 * useEnterpriseId — 获取当前登录用户所属企业 ID。
 *
 * 根因修复：v3 各页面（AICompanyView / CompilePage / RuntimeView / WorkforceView /
 * EvolutionPage / InterviewPage）原先用模块级常量 ENTERPRISE_ID，在非 mock 模式下
 * 兜底到 MOCK_ENTERPRISE_ID='ent-zhilian-001'，与真实后端企业 ID 不匹配，导致
 * 今日 AI 公司、编译、运行时、版本对比等全部数据为空。
 *
 * 本 hook 从认证态用户读取真实 enterprise_id，仅在 mock 模式下回退到 mock 企业。
 * Mock 模式（VITE_USE_MOCK=true）保留 mock 企业用于前端独立开发。
 */
import { useAuth } from './useAuth'
import { MOCK_ENTERPRISE_ID } from '@/api/mockData'

export function useEnterpriseId(): string {
  const { user } = useAuth()

  // Mock 模式：使用 mock 示例企业，便于前端独立于后端开发
  if (import.meta.env.VITE_USE_MOCK === 'true') {
    return MOCK_ENTERPRISE_ID
  }

  // 真实模式：优先用登录用户的 enterprise_id
  if (user?.enterprise_id) {
    return user.enterprise_id
  }

  // 显式配置的默认企业 ID（部署期覆盖）
  const configured = import.meta.env.VITE_DEFAULT_ENTERPRISE_ID as string | undefined
  if (configured) {
    return configured
  }

  // 兜底：未拿到企业 ID 时返回空串，调用方会因 404/空数据触发错误态，
  // 比静默用错误的 mock ID 更安全（避免展示不属于当前用户的数据）
  return ''
}

export default useEnterpriseId
