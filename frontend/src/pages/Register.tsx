import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  User,
  Mail,
  Lock,
  Eye,
  EyeOff,
  AlertCircle,
  Loader2,
  ArrowRight,
  Rocket,
  Workflow,
  ShieldCheck,
  Hexagon,
} from 'lucide-react'
import { useAuth } from '@/hooks/useAuth'
import { Button } from '@/components/ui/Button'

/**
 * Register 页面（公共页 · 全屏独立布局）
 * 设计：方案B 暖白 + 深海军蓝 + 衬线标题
 * 桌面端左右分栏：左栏品牌叙事 / 右栏暖白表单
 * 移动端：隐藏左栏，仅显示表单
 */
export default function Register() {
  const { register } = useAuth()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [agreeTerms, setAgreeTerms] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

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

    if (!agreeTerms) {
      setError('请阅读并同意服务条款')
      return
    }

    setLoading(true)

    try {
      await register({ name, email, password })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '注册失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  // 密码强度计算
  const passwordStrength = useMemo(() => {
    if (!password) return { score: 0, label: '', color: '' }
    let score = 0
    if (password.length >= 6) score++
    if (password.length >= 10) score++
    const hasLetter = /[a-zA-Z]/.test(password)
    const hasNumber = /[0-9]/.test(password)
    const hasSymbol = /[^a-zA-Z0-9]/.test(password)
    if (hasLetter && hasNumber) score++
    if (hasSymbol) score++
    if (score > 3) score = 3
    if (score <= 1) return { score: 1, label: '弱', color: 'bg-error' }
    if (score === 2) return { score: 2, label: '中', color: 'bg-warning' }
    return { score: 3, label: '强', color: 'bg-success' }
  }, [password])

  // 左栏品牌特性列表（注册页专属）
  const features = [
    {
      icon: Workflow,
      title: '可视化工作流编排',
      desc: '拖拽即可搭建 AI 自动化流程，所见即所得',
    },
    {
      icon: Rocket,
      title: '分钟级一键部署',
      desc: '无需运维，从开发到上线一气呵成',
    },
    {
      icon: ShieldCheck,
      title: '企业级安全合规',
      desc: '数据自主可控，权限精细管控',
    },
  ]

  return (
    <div className="min-h-screen flex bg-canvas">
      {/* ===== 左栏：品牌叙事（桌面端可见） ===== */}
      <aside
        className="hidden lg:flex w-1/2 flex-col justify-between p-12 xl:p-16 text-white relative overflow-hidden"
        style={{ background: 'linear-gradient(135deg, #15293F 0%, #1E3A5F 55%, #244670 100%)' }}
      >
        {/* 装饰光晕（柔和，无霓虹） */}
        <div
          className="pointer-events-none absolute -top-24 -right-20 w-[28rem] h-[28rem] rounded-full"
          style={{ background: 'radial-gradient(circle at center, rgba(107,140,177,0.28), transparent 70%)' }}
        />
        <div
          className="pointer-events-none absolute -bottom-32 -left-16 w-96 h-96 rounded-full"
          style={{ background: 'radial-gradient(circle at center, rgba(107,140,177,0.18), transparent 70%)' }}
        />
        {/* 细网格底纹 */}
        <div
          className="absolute inset-0 pointer-events-none opacity-[0.04]"
          style={{
            backgroundImage:
              'linear-gradient(rgba(255,255,255,0.7) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.7) 1px, transparent 1px)',
            backgroundSize: '56px 56px',
          }}
        />

        {/* 顶部：Logo + 品牌装饰线（白色变体） */}
        <header className="relative z-10">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-lg bg-white/10 border border-white/15 flex items-center justify-center backdrop-blur-sm">
              <Hexagon className="w-5 h-5 text-white" />
            </div>
            <div className="font-serif-display text-xl font-bold tracking-tight">AutoTeams</div>
          </div>
        </header>

        {/* 中部：衬线品牌主张 + 价值点列表 */}
        <div className="relative z-10 max-w-md">
          <div
            className="brand-rule mb-6"
            style={{ background: 'rgba(255,255,255,0.85)' }}
          />
          <h1 className="font-serif-display text-4xl xl:text-[2.75rem] font-semibold leading-[1.15]">
            把 AI 落地，
            <br />
            缩短到分钟
          </h1>
          <p className="mt-5 text-white/70 text-base leading-relaxed">
            面向中小企业的 AI 智能体自动化落地平台，让每一个业务流程都能被设计、被编排、被部署。
          </p>

          <ul className="mt-10 space-y-5">
            {features.map(({ icon: Icon, title, desc }) => (
              <li key={title} className="flex items-start gap-3.5">
                <span className="mt-0.5 flex w-9 h-9 shrink-0 items-center justify-center rounded-lg bg-white/10 border border-white/15">
                  <Icon className="h-[18px] w-[18px] text-white" />
                </span>
                <div>
                  <div className="font-medium text-white">{title}</div>
                  <div className="text-sm text-white/60 mt-0.5 leading-relaxed">{desc}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* 底部：版权 */}
        <footer className="relative z-10 text-white/45 text-sm">
          © 2026 AutoTeams · 让 AI 真正落地到每一个业务
        </footer>
      </aside>

      {/* ===== 右栏：暖白表单区 ===== */}
      <section className="w-full lg:w-1/2 flex items-center justify-center p-6 sm:p-8 bg-canvas min-h-screen">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
          className="w-full max-w-md"
        >
          {/* 顶部：小 Logo + 标题 */}
          <div className="mb-8">
            {/* Logo（所有屏幕尺寸可见） */}
            <div className="font-serif-display text-xl font-bold text-brand-500 mb-4">
              AutoTeams
            </div>
            <h2 className="text-2xl font-semibold text-text-primary">
              创建账号
            </h2>
            <p className="mt-3 text-sm text-text-secondary">开始您的 AI 落地之旅</p>
          </div>

          {/* 错误提示区 */}
          {error && (
            <div
              role="alert"
              className="mb-5 flex items-start gap-2.5 rounded-md border border-error/20 bg-error/5 p-3 text-sm text-error"
            >
              <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* 表单 */}
          <form onSubmit={handleSubmit} className="space-y-5" noValidate>
            {/* 姓名 */}
            <div>
              <label
                htmlFor="name"
                className="block text-sm font-medium text-text-primary mb-1.5"
              >
                姓名
              </label>
              <div className="relative">
                <User className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none" />
                <input
                  id="name"
                  name="name"
                  type="text"
                  autoComplete="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="请输入姓名"
                  required
                  className="w-full pl-10 pr-4 py-2.5 text-sm bg-surface-2 border border-border-default rounded-lg text-text-primary placeholder:text-text-tertiary outline-none transition focus:border-brand-500 focus:shadow-focus"
                />
              </div>
            </div>

            {/* 邮箱 */}
            <div>
              <label
                htmlFor="register-email"
                className="block text-sm font-medium text-text-primary mb-1.5"
              >
                邮箱
              </label>
              <div className="relative">
                <Mail className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none" />
                <input
                  id="register-email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="请输入邮箱"
                  required
                  className="w-full pl-10 pr-4 py-2.5 text-sm bg-surface-2 border border-border-default rounded-lg text-text-primary placeholder:text-text-tertiary outline-none transition focus:border-brand-500 focus:shadow-focus"
                />
              </div>
            </div>

            {/* 密码 + 强度指示器 */}
            <div>
              <label
                htmlFor="register-password"
                className="block text-sm font-medium text-text-primary mb-1.5"
              >
                密码
              </label>
              <div className="relative">
                <Lock className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none" />
                <input
                  id="register-password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入密码（至少6位）"
                  required
                  minLength={6}
                  className="w-full pl-10 pr-11 py-2.5 text-sm bg-surface-2 border border-border-default rounded-lg text-text-primary placeholder:text-text-tertiary outline-none transition focus:border-brand-500 focus:shadow-focus"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-primary transition"
                  aria-label="切换密码可见"
                >
                  {showPassword ? (
                    <EyeOff className="h-5 w-5" />
                  ) : (
                    <Eye className="h-5 w-5" />
                  )}
                </button>
              </div>
              {/* 密码强度指示器 */}
              {password && (
                <div className="mt-2 flex items-center gap-2">
                  <div className="flex-1 flex gap-1">
                    {[1, 2, 3].map((seg) => (
                      <div
                        key={seg}
                        className={`h-1.5 flex-1 rounded-full transition-colors ${
                          seg <= passwordStrength.score ? passwordStrength.color : 'bg-border-default'
                        }`}
                      />
                    ))}
                  </div>
                  <span className="text-xs text-text-tertiary w-6 text-right">
                    {passwordStrength.label}
                  </span>
                </div>
              )}
              {!password && (
                <p className="text-xs text-text-tertiary mt-1.5">密码长度至少6位</p>
              )}
            </div>

            {/* 确认密码 */}
            <div>
              <label
                htmlFor="register-confirm"
                className="block text-sm font-medium text-text-primary mb-1.5"
              >
                确认密码
              </label>
              <div className="relative">
                <Lock className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none" />
                <input
                  id="register-confirm"
                  name="confirmPassword"
                  type={showConfirmPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="请再次输入密码"
                  required
                  className="w-full pl-10 pr-11 py-2.5 text-sm bg-surface-2 border border-border-default rounded-lg text-text-primary placeholder:text-text-tertiary outline-none transition focus:border-brand-500 focus:shadow-focus"
                />
                <button
                  type="button"
                  onClick={() => setShowConfirmPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary hover:text-text-primary transition"
                  aria-label="切换确认密码可见"
                >
                  {showConfirmPassword ? (
                    <EyeOff className="h-5 w-5" />
                  ) : (
                    <Eye className="h-5 w-5" />
                  )}
                </button>
              </div>
            </div>

            {/* 服务条款 */}
            <label className="flex items-start gap-2 text-sm text-text-secondary cursor-pointer select-none">
              <input
                type="checkbox"
                checked={agreeTerms}
                onChange={(e) => setAgreeTerms(e.target.checked)}
                className="h-4 w-4 rounded border-border-default accent-brand-500 mt-0.5"
              />
              <span>
                我已阅读并同意{' '}
                <span className="text-brand-500 cursor-help" title="服务条款待发布，请联系管理员获取">
                  服务条款
                </span>{' '}
                和{' '}
                <span className="text-brand-500 cursor-help" title="隐私政策待发布，请联系管理员获取">
                  隐私政策
                </span>
              </span>
            </label>

            {/* 注册按钮 */}
            <Button
              type="submit"
              disabled={loading}
              variant="primary"
              size="lg"
              className="w-full shadow-soft"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  创建中…
                </span>
              ) : (
                <span className="flex items-center justify-center gap-2">
                  创建账号
                  <ArrowRight className="h-4 w-4" />
                </span>
              )}
            </Button>
          </form>

          {/* 底部：登录引导 */}
          <div className="mt-8 text-center text-sm text-text-secondary">
            已有账号？{' '}
            <Link
              to="/login"
              className="text-brand-500 font-medium hover:underline"
            >
              立即登录
            </Link>
          </div>
        </motion.div>
      </section>
    </div>
  )
}
