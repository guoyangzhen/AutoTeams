import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import apiClient from '@/api/client'
import { Spinner } from '@/components/ui/Spinner'
import { ApiResponse } from '@/types'

export default function Invite() {
  const { token } = useParams<{ token: string }>()
  const navigate = useNavigate()
  const { updateUser } = useAuth()

  const [enterpriseName, setEnterpriseName] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(true)
  const [registering, setRegistering] = useState(false)
  const [error, setError] = useState('')

  const validateInvite = useCallback(async () => {
    try {
      setLoading(true)
      const response = await apiClient.get<ApiResponse<{ enterprise_id: string; enterprise_name: string }>>(`/auth/invite/${token}`)
      setEnterpriseName(response.data.data.enterprise_name)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '邀请链接无效或已过期')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    if (token) {
      validateInvite()
    }
  }, [token, validateInvite])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }

    if (password.length < 6) {
      setError('密码长度至少为6位')
      return
    }

    try {
      setRegistering(true)
      // P1-1: 后端 register-with-invite 将 token 写入 HttpOnly Cookie
      const response = await apiClient.post<ApiResponse<{ user: unknown }>>(
        `/auth/register-with-invite?invite_token=${token}`,
        { name, email, password }
      )

      const { user } = response.data.data as { user: { id: string; name: string; email: string; role: 'admin' | 'member'; enterprise_id: string | null; created_at: string } }
      // M7: 不再将 user 对象存入 localStorage（XSS 可读取）
      // P1-1: 认证状态完全由 HttpOnly Cookie + AuthProvider(useAuth) 管理
      updateUser(user)
      navigate('/')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '注册失败，请重试')
    } finally {
      setRegistering(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-canvas flex items-center justify-center p-4">
        <div className="text-center">
          <Spinner size="xl" className="text-brand-500 mx-auto mb-4" />
          <p className="text-text-secondary">正在验证邀请链接...</p>
        </div>
      </div>
    )
  }

  if (error && !enterpriseName) {
    return (
      <div className="min-h-screen bg-canvas flex items-center justify-center p-4">
        <div className="w-full max-w-md text-center">
          <div className="bg-surface rounded-2xl border border-border-default p-8">
            <div className="w-16 h-16 bg-error/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-error" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-text-primary mb-2">邀请链接无效</h2>
            <p className="text-text-secondary mb-6">{error}</p>
            <Link
              to="/register"
              className="inline-block px-6 py-3 bg-brand-500 text-white rounded-lg hover:bg-brand-600 transition-colors"
            >
              前往注册
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-canvas flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-brand-400 mb-2">AutoTeams</h1>
          <p className="text-text-secondary">智能企业知识管理系统</p>
        </div>

        <div className="bg-surface rounded-2xl border border-border-default p-8">
          <div className="text-center mb-6">
            <div className="w-12 h-12 bg-success/10 rounded-full flex items-center justify-center mx-auto mb-3">
              <svg className="w-6 h-6 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-text-primary">加入企业</h2>
            <p className="text-sm text-text-secondary mt-1">
              您被邀请加入 <span className="font-semibold text-brand-400">{enterpriseName}</span>
            </p>
          </div>

          {error && (
            <div className="mb-4 p-3 bg-error/5 border border-error/20 rounded-lg text-error text-sm">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="name" className="block text-sm font-medium text-text-secondary mb-1">
                姓名
              </label>
              <input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="请输入姓名"
                required
                className="w-full px-4 py-3 bg-elevated border border-border-default rounded-lg text-text-primary focus:border-brand-500 placeholder-text-tertiary"
              />
            </div>

            <div>
              <label htmlFor="email" className="block text-sm font-medium text-text-secondary mb-1">
                邮箱
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="请输入邮箱"
                required
                className="w-full px-4 py-3 bg-elevated border border-border-default rounded-lg text-text-primary focus:border-brand-500 placeholder-text-tertiary"
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-text-secondary mb-1">
                密码
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="请输入密码（至少6位）"
                required
                minLength={6}
                className="w-full px-4 py-3 bg-elevated border border-border-default rounded-lg text-text-primary focus:border-brand-500 placeholder-text-tertiary"
              />
            </div>

            <div>
              <label htmlFor="confirmPassword" className="block text-sm font-medium text-text-secondary mb-1">
                确认密码
              </label>
              <input
                id="confirmPassword"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="请再次输入密码"
                required
                className="w-full px-4 py-3 bg-elevated border border-border-default rounded-lg text-text-primary focus:border-brand-500 placeholder-text-tertiary"
              />
            </div>

            <button
              type="submit"
              disabled={registering}
              className="w-full py-3 bg-brand-500 text-white font-semibold rounded-lg hover:bg-brand-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {registering ? (
                <span className="flex items-center justify-center">
                  <Spinner className="-ml-1 mr-3 text-white" />
                  注册中...
                </span>
              ) : '注册并加入'}
            </button>
          </form>

          <div className="mt-4 text-center">
            <p className="text-sm text-text-secondary">
              已有账号？{' '}
              <Link to="/login" className="text-brand-400 hover:text-brand-500 font-medium">
                直接登录
              </Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
