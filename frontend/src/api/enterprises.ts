import apiClient from './client'
import { ApiResponse } from '@/types'

/**
 * 企业管理 API 客户端。
 *
 * 激活后端 enterprise.py 中的 5 个"半接入"端点：
 * - GET    /enterprises/{id}/members                  成员列表
 * - DELETE /enterprises/{id}/members/{user_id}        移除成员
 * - PUT    /enterprises/{id}/members/{user_id}/role   变更角色
 * - GET    /enterprises/{id}/invitations               邀请记录
 * - POST   /enterprises/{id}/invitations/{iid}/cancel  取消邀请
 *
 * 修复 Settings.tsx 中 /invites URL 拼写错误（应为 /invitations）。
 */

/** 企业成员信息 */
export interface Member {
  id: string
  email: string
  name: string
  role: 'admin' | 'member' | string
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

/** 邀请记录 */
export interface Invitation {
  id: string
  enterprise_id: string
  invited_by_user_id: string
  token: string
  email: string | null
  status: 'pending' | 'accepted' | 'expired' | 'cancelled' | string
  expires_at: string | null
  created_at: string
  used_at: string | null
  used_by_user_id: string | null
}

/** 列出企业成员（admin/member 都可查看） */
export async function listMembers(enterpriseId: string): Promise<Member[]> {
  const response = await apiClient.get<ApiResponse<Member[]>>(
    `/enterprises/${enterpriseId}/members`,
  )
  return response.data.data
}

/** 移除企业成员（admin 权限，不能移除自己） */
export async function removeMember(enterpriseId: string, userId: string): Promise<void> {
  await apiClient.delete(`/enterprises/${enterpriseId}/members/${userId}`)
}

/** 变更成员角色（admin 权限，不能改自己） */
export async function changeMemberRole(
  enterpriseId: string,
  userId: string,
  role: 'admin' | 'member',
): Promise<Member> {
  const response = await apiClient.put<ApiResponse<Member>>(
    `/enterprises/${enterpriseId}/members/${userId}/role`,
    { role },
  )
  return response.data.data
}

/** 列出企业邀请记录（admin 权限） */
export async function listInvitations(enterpriseId: string): Promise<Invitation[]> {
  const response = await apiClient.get<ApiResponse<Invitation[]>>(
    `/enterprises/${enterpriseId}/invitations`,
  )
  return response.data.data
}

/** 取消邀请（admin 权限，仅 pending 状态可取消） */
export async function cancelInvitation(
  enterpriseId: string,
  invitationId: string,
): Promise<Invitation> {
  const response = await apiClient.post<ApiResponse<Invitation>>(
    `/enterprises/${enterpriseId}/invitations/${invitationId}/cancel`,
  )
  return response.data.data
}
