import apiClient, { resetAuthSession } from './client'
import { AuthResponse, LoginRequest, RegisterRequest, User, ApiResponse } from '@/types'

export const login = async (data: LoginRequest): Promise<AuthResponse> => {
  const response = await apiClient.post<ApiResponse<AuthResponse>>('/auth/login', data)
  // 拿到新会话后解除「已终局失效」标记，否则上一次 401 触发的登出会让
  // 本次登录后的首个请求直接被判失败。
  resetAuthSession()
  return response.data.data
}

export const register = async (data: RegisterRequest): Promise<AuthResponse> => {
  const response = await apiClient.post<ApiResponse<AuthResponse>>('/auth/register', data)
  resetAuthSession()
  return response.data.data
}

export const getMe = async (): Promise<User> => {
  const response = await apiClient.get<ApiResponse<User>>('/auth/me')
  return response.data.data
}

/** P1-1: 用 HttpOnly Cookie 中的 refresh token 换取新的 token 对（由后端写入 Cookie）。 */
export const refreshToken = async (): Promise<void> => {
  await apiClient.post<ApiResponse<null>>('/auth/refresh', {})
}

/** P0-12: 登出（清除后端 refresh_token_hash）。 */
export const logout = async (): Promise<void> => {
  await apiClient.post('/auth/logout')
}
