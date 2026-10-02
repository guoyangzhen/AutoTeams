/**
 * AutoTeams 品牌 Logo：圆 + 正六边形 + 中心点。
 *
 * 设计源自登录页品牌标识：外层圆环象征企业闭环，
 * 内嵌正六边形代表结构化能力矩阵，中心点为 AI 核心。
 */
interface LogoProps {
  className?: string
  /** brand = 品牌色描边；light = 白色描边（深色背景用）；mono = 当前文字色 */
  variant?: 'brand' | 'light' | 'mono'
}

const VARIANT_COLOR: Record<NonNullable<LogoProps['variant']>, string> = {
  brand: 'text-brand-500',
  light: 'text-white',
  mono: 'text-text-primary',
}

export function Logo({ className = 'w-7 h-7', variant = 'brand' }: LogoProps) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      className={`${className} ${VARIANT_COLOR[variant]}`}
      aria-hidden="true"
    >
      {/* 外层圆环（细线条，与头像尺寸协调） */}
      <circle cx="16" cy="16" r="14" stroke="currentColor" strokeWidth="1.2" />
      {/* 内嵌正六边形（细线条） */}
      <path
        d="M16 7 L23.5 11.5 L23.5 20.5 L16 25 L8.5 20.5 L8.5 11.5 Z"
        stroke="currentColor"
        strokeWidth="1.2"
        fill="none"
        strokeLinejoin="round"
      />
      {/* 中心点（保持原大小作为视觉锚点） */}
      <circle cx="16" cy="16" r="2.5" fill="currentColor" />
    </svg>
  )
}

export default Logo
