import { useState, useEffect, useCallback, useContext, ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { User, LoginRequest, RegisterRequest } from '@/types'
import * as authApi from '@/api/auth'
import { AUTH_SESSION_EXPIRED_EVENT } from '@/api/client'
import { AuthContext } from './AuthContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const navigate = useNavigate()

  // P1-1: 不再维护前端 token state，认证状态完全由 HttpOnly Cookie 决定
  const isAuthenticated = !!user

  useEffect(() => {
    const initAuth = async () => {
      try {
        // AUD-26: getMe 内部允许一次静默刷新重试。access 过期而 refresh 有效时
        // 这里会直接拿到用户（会话恢复）；refresh 也失效时才会 reject 并清空用户。
        const userData = await authApi.getMe()
        setUser(userData)
      } catch {
        // Cookie 无效或已过期，认证状态由后端 Cookie 决定
        setUser(null)
      }
      setIsLoading(false)
    }

    initAuth()
  }, [])

  // AUD-26: 会话在运行期彻底失效（任意请求刷新返回 401）时，api 层广播该事件，
  // 这里统一清空登录态并回到登录页，避免停留在“看似已登录但全部请求 401”的状态。
  useEffect(() => {
    const onSessionExpired = () => {
      setUser(null)
      navigate('/login', { replace: true })
    }
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, onSessionExpired)
    return () => window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, onSessionExpired)
  }, [navigate])

  const login = useCallback(async (data: LoginRequest) => {
    const response = await authApi.login(data)
    // P1-1: token 由后端写入 HttpOnly Cookie，前端不持久化任何用户信息
    setUser(response.user)
    navigate('/')
  }, [navigate])

  const register = useCallback(async (data: RegisterRequest) => {
    const response = await authApi.register(data)
    setUser(response.user)
    navigate('/')
  }, [navigate])

  // P0-12: logout 改为 async，先调后端清除 refresh_token_hash，再清本地
  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // 后端登出失败不阻塞前端登出（token 可能已过期）
    }
    setUser(null)
    navigate('/login')
  }, [navigate])

  const updateUser = useCallback((updatedUser: User) => {
    setUser(updatedUser)
  }, [])

  return (
    <AuthContext.Provider value={{
      user,
      token: null,
      isAuthenticated,
      isLoading,
      login,
      register,
      logout,
      updateUser
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}

export default useAuth
