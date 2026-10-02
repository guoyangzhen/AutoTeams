/**
 * PageHeader — 统一页面标题区（融合 prototype 视觉模式）。
 *
 * 结构：serif 标题 + brand-rule 装饰条 + 副标题 + 右侧操作区。
 * 所有页面共用此组件，确保大奖级一致的视觉层级与品牌叙事。
 */
import { ReactNode } from 'react'

interface PageHeaderProps {
  /** 主标题（serif 字体） */
  title: string
  /** 副标题（tertiary 色） */
  subtitle?: string
  /** 右侧操作区（按钮、状态徽标等） */
  actions?: ReactNode
  /** 是否显示 brand-rule 装饰条（默认显示） */
  showRule?: boolean
  /** 标题下方附加内容（如日期、筛选器） */
  children?: ReactNode
  className?: string
}

export function PageHeader({
  title,
  subtitle,
  actions,
  showRule = true,
  children,
  className = '',
}: PageHeaderProps) {
  return (
    <div className={`flex items-start justify-between gap-4 flex-wrap mb-6 ${className}`}>
      <div className="min-w-0">
        <h1 className="font-serif-display text-2xl font-semibold text-text-primary tracking-tight">
          {title}
        </h1>
        {showRule && <div className="brand-rule mt-2 mb-2" />}
        {subtitle && <p className="text-sm text-text-tertiary">{subtitle}</p>}
        {children}
      </div>
      {actions && (
        <div className="flex items-center gap-2 flex-shrink-0">{actions}</div>
      )}
    </div>
  )
}

export default PageHeader
