/**
 * Pagination — 统一分页组件。
 *
 * 对标 prototype 知识库页分页样式：
 * "共 N 条 · 第 X/Y 页" + 页码按钮组（当前页 bg-brand-500 text-white）。
 *
 * 用于替换各页面手写分页（KnowledgePage 文件列表、AuditLogs、长列表等）。
 */
import { ChevronLeft, ChevronRight } from 'lucide-react'

interface PaginationProps {
  /** 当前页（1-based） */
  page: number
  /** 每页条数 */
  pageSize: number
  /** 总条数 */
  total: number
  /** 页码变更回调 */
  onChange: (page: number) => void
  /** 是否紧凑模式（移动端，隐藏页码只显示前后页 + 总数） */
  compact?: boolean
  className?: string
}

/** 计算要显示的页码按钮（当前页 ± 1，首尾 + 省略号） */
function getPageRange(current: number, totalPages: number): (number | 'ellipsis')[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1)
  }
  const pages: (number | 'ellipsis')[] = [1]
  const start = Math.max(2, current - 1)
  const end = Math.min(totalPages - 1, current + 1)
  if (start > 2) pages.push('ellipsis')
  for (let i = start; i <= end; i++) pages.push(i)
  if (end < totalPages - 1) pages.push('ellipsis')
  pages.push(totalPages)
  return pages
}

export function Pagination({
  page,
  pageSize,
  total,
  onChange,
  compact = false,
  className = '',
}: PaginationProps) {
  if (total <= 0) return null

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const start = (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, total)

  const pageRange = compact ? [] : getPageRange(page, totalPages)
  const canPrev = page > 1
  const canNext = page < totalPages

  return (
    <div
      className={`flex items-center justify-between gap-3 px-4 py-3 border-t border-border-subtle text-xs text-text-tertiary flex-wrap ${className}`}
    >
      <span>
        共 {total} 条 · 显示 {start}-{end} / 第 {page}/{totalPages} 页
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => canPrev && onChange(page - 1)}
          disabled={!canPrev}
          aria-label="上一页"
          className="inline-flex items-center justify-center w-8 h-8 rounded border border-border-default text-text-secondary hover:bg-elevated disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronLeft className="w-4 h-4" aria-hidden="true" />
        </button>
        {!compact &&
          pageRange.map((p, idx) =>
            p === 'ellipsis' ? (
              <span
                key={`e-${idx}`}
                className="inline-flex items-center justify-center w-8 h-8 text-text-muted"
              >
                …
              </span>
            ) : (
              <button
                key={p}
                type="button"
                onClick={() => onChange(p)}
                aria-label={`第 ${p} 页`}
                aria-current={p === page ? 'page' : undefined}
                className={`inline-flex items-center justify-center min-w-8 h-8 px-2 rounded text-sm transition-colors ${
                  p === page
                    ? 'bg-brand-500 text-white font-medium'
                    : 'border border-border-default text-text-secondary hover:bg-elevated'
                }`}
              >
                {p}
              </button>
            ),
          )}
        <button
          type="button"
          onClick={() => canNext && onChange(page + 1)}
          disabled={!canNext}
          aria-label="下一页"
          className="inline-flex items-center justify-center w-8 h-8 rounded border border-border-default text-text-secondary hover:bg-elevated disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronRight className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}

export default Pagination
