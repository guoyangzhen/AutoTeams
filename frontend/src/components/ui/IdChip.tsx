/**
 * IdChip — 原始 ID 友好化展示组件（同类信息优化）。
 *
 * 把后端返回的原始 ID（role_id / job_id / user_id / resource_id 等）收敛为
 * 「可点击复制的短芯片」：悬停显示完整 ID，点击复制到剪贴板并短暂反馈。
 * 避免一长串 UUID/字符串直接铺满界面。
 */
import { useState } from 'react'

type IdTone = 'default' | 'info' | 'error'

interface IdChipProps {
  id: string
  /** 超出该长度时截断显示（完整值仍可通过悬停/复制获取） */
  maxLength?: number
  tone?: IdTone
  /** 是否允许点击复制（默认 true） */
  copyable?: boolean
  /** 自定义显示文案（默认取 id 截断）；复制仍使用完整 id */
  label?: string
  className?: string
}

const TONE_CLASS: Record<IdTone, string> = {
  default: 'bg-elevated text-text-secondary',
  info: 'bg-info/10 text-info',
  error: 'bg-error/10 text-error',
}

export function IdChip({
  id,
  maxLength = 24,
  tone = 'default',
  copyable = true,
  label,
  className = '',
}: IdChipProps) {
  const [copied, setCopied] = useState(false)

  const display = label ?? (id.length > maxLength ? `${id.slice(0, maxLength)}…` : id)

  const handleCopy = () => {
    if (!copyable) return
    navigator.clipboard?.writeText(id).catch(() => {})
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={`${id}${copyable ? '（点击复制）' : ''}`}
      className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-mono max-w-[180px] truncate transition-opacity ${copyable ? 'hover:opacity-80 cursor-pointer' : 'cursor-default'} ${TONE_CLASS[tone]} ${className}`}
    >
      {copied ? '已复制' : display}
    </button>
  )
}