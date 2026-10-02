import { ReactNode } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { User } from '@/types'

type Role = 'admin' | 'member'

interface Props {
  /** 允许访问的角色列表 */
  roles: Role[]
  /** 子组件 */
  children: ReactNode
  /** 无权限时显示的降级 UI（默认显示提示信息） */
  fallback?: ReactNode
}

/**
 * P1-04-E: 角色守卫组件
 *
 * 根据 current_user.role 控制前端 UI 的可见性。
 * 注意：这只是 UX 层的隐藏，真正的权限校验在后端 API。
 *
 * 用法：
 *   <RoleGuard roles={['admin']}>
 *     <InviteButton />
 *   </RoleGuard>
 */
export function RoleGuard({ roles, children, fallback }: Props) {
  const { user, isLoading } = useAuth()

  // 加载中时显示 loading（避免闪烁）
  if (isLoading) {
    return null
  }

  // 未登录或无角色信息，显示 fallback
  if (!user || !roles.includes(user.role as Role)) {
    return (
      fallback ?? (
        <div className="p-4 rounded-lg border border-border-default bg-surface text-sm text-text-secondary">
          您没有权限执行此操作
        </div>
      )
    )
  }

  return <>{children}</>
}

export default RoleGuard

// 辅助类型守卫
export function hasRole(user: User | null, role: Role): boolean {
  return user?.role === role
}
