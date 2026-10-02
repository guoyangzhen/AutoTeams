import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  Mail,
  Lock,
  Eye,
  EyeOff,
  AlertCircle,
  Loader2,
  Rocket,
  Workflow,
  ShieldCheck,
} from 'lucide-react'
import { useAuth } from '@/hooks/useAuth'
import { Button } from '@/components/ui/Button'
import { Logo } from '@/components/Logo'

/**
 * Login 页面（公共页 · 全屏独立布局）
 * 设计：方案B 暖白 + 深海军蓝 + 衬线标题
 * 桌面端左右分栏：左栏品牌叙事 / 右栏暖白表单
 * 移动端：隐藏左栏，仅显示表单
 */
export default function Login() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [rememberMe, setRememberMe] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await login({ email, password })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '登录失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  // 左栏品牌特性列表
  const features = [
    {
      icon: Rocket,
      title: '分钟级生成',
      desc: '从知识到协作，一站式完成',
    },
    {
      icon: Workflow,
      title: '可视化编排',
      desc: '把提示词逻辑变成可见执行路径',
    },
    {
      icon: ShieldCheck,
      title: '生产级交付',
      desc: '一键部署，实时监控',
    },
  ]

  return (
        <div className="min-h-[100dvh] flex bg-[var(--bg-canvas)]">

      {/* ===== 左栏：品牌叙事（桌面端可见） ===== */}
      <aside
                className="hidden lg:flex w-[52%] flex-col justify-between p-12 xl:p-16 text-white relative overflow-hidden bg-[#102842]"

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
            <Logo className="w-9 h-9" variant="light" />
                        <div className="text-3xl font-semibold tracking-[-0.045em]">AutoTeams</div>

          </div>
          <div
            className="mt-4 h-[3px] w-12 rounded-[2px]"
            style={{ background: 'rgba(255,255,255,0.7)' }}
          />
        </header>

        {/* 中部：衬线品牌主张 + 价值点列表 */}
        <div className="relative z-10 max-w-md">
                    <h1 className="text-[2.75rem] xl:text-[3.25rem] font-semibold tracking-[-0.06em] leading-[1.06] text-balance">

            把 AI 落地，
            <br />
            缩短到分钟
          </h1>
          <p className="mt-5 text-white/70 text-base leading-relaxed">
            为中小企业打造的智能体自动化部署与交付平台
          </p>

          <ul className="mt-10 space-y-6">
            {features.map(({ icon: Icon, title, desc }) => (
              <li key={title} className="flex items-start gap-4">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-white/10 border border-white/15">
                  <Icon className="h-5 w-5 text-white" />
                </span>
                <div className="pt-0.5">
                  <div className="font-semibold text-white">{title}</div>
                  <div className="text-sm text-white/60 mt-1">{desc}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* 底部：版权 */}
        <footer className="relative z-10 text-white/50 text-sm">
          © 2026 AutoTeams · 让 AI 落地更简单
        </footer>
      </aside>

      {/* ===== 右栏：暖白表单区 ===== */}
            <section className="w-full lg:w-[48%] flex items-center justify-center p-6 sm:p-10 bg-[var(--bg-canvas)] min-h-[100dvh]">

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
                    className="w-full max-w-[28rem]"

        >
          {/* 顶部：小 Logo + 欢迎语 */}
          <div className="mb-8">
            {/* 移动端展示 Logo（桌面端左栏已显示，这里隐藏） */}
                        <div className="text-xl font-semibold tracking-[-0.035em] text-brand-500 mb-4">

              AutoTeams
            </div>
                        <h2 className="text-[1.75rem] font-semibold tracking-[-0.04em] text-text-primary">

              欢迎回来
            </h2>
            <p className="mt-3 text-sm text-text-secondary">登录您的企业工作台</p>
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
            {/* 邮箱 */}
            <div>
              <label
                htmlFor="email"
                className="block text-sm font-medium text-text-primary mb-1.5"
              >
                邮箱
              </label>
              <div className="relative">
                <Mail className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none" />
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="请输入邮箱"
                  required
                                    className="w-full min-h-12 pl-10 pr-4 bg-[var(--surface-raised)] border border-border-default rounded-xl text-text-primary placeholder:text-text-tertiary outline-none transition-[border-color,box-shadow] focus:border-brand-500 focus:shadow-focus"

                />
              </div>
            </div>

            {/* 密码 */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label
                  htmlFor="password"
                  className="block text-sm font-medium text-text-primary"
                >
                  密码
                </label>
                                <span className="text-sm text-text-tertiary" title="请联系管理员重置密码">
                  请联系管理员重置
                </span>

              </div>
              <div className="relative">
                <Lock className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none" />
                <input
                  id="password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入密码"
                  required
                                    className="w-full min-h-12 pl-10 pr-11 bg-[var(--surface-raised)] border border-border-default rounded-xl text-text-primary placeholder:text-text-tertiary outline-none transition-[border-color,box-shadow] focus:border-brand-500 focus:shadow-focus"

                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                                    className="absolute right-2 top-1/2 -translate-y-1/2 ui-control inline-flex h-9 min-h-0 w-9 items-center justify-center text-text-tertiary hover:bg-[var(--surface-tint)] hover:text-text-primary"

                  aria-label="切换密码可见"
                >
                  {showPassword ? (
                    <EyeOff className="w-4 h-4" />
                  ) : (
                    <Eye className="w-4 h-4" />
                  )}
                </button>
              </div>
            </div>

            {/* 记住我 */}
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="h-4 w-4 rounded border-border-default accent-brand-500"
                />
                记住我
              </label>
            </div>

            {/* 登录按钮 */}
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
                  登录中…
                </span>
              ) : (
                '登录'
              )}
            </Button>
          </form>

          {/* 底部：注册引导 */}
          <div className="mt-8 text-center text-sm text-text-secondary">
            还没有账号？{' '}
            <Link
              to="/register"
              className="text-brand-500 font-medium hover:underline"
            >
              立即注册
            </Link>
          </div>
        </motion.div>
      </section>
    </div>
  )
}
