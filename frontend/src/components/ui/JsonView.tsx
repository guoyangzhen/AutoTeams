/**
 * JsonView — 原始数据友好化展示组件（同类信息优化）。
 *
 * 把后端返回的原始 object / JSON 字符串（优化输入输出、测试结果、技能执行数据等）
 * 收敛为「可折叠 + 语法高亮 + 可复制」的视图，替代一长串 JSON.stringify 原文，
 * 让普通用户也能读懂结构化数据。
 *
 * 用法：<JsonView value={obj|string} title="输入" />
 */
import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Copy, Check } from 'lucide-react'

interface JsonViewProps {
  /** 任意对象或 JSON 字符串 */
  value: unknown
  /** 标题（可选） */
  title?: string
  /** 默认是否折叠内容（默认 true，仅显示标题行） */
  defaultCollapsed?: boolean
}

function normalize(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') {
    // 若本身就是 JSON 字符串，尝试格式化
    try {
      const parsed = JSON.parse(value)
      return JSON.stringify(parsed, null, 2)
    } catch {
      return value
    }
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** 极简语法高亮：key（品牌色）、字符串（绿）、数字/布尔/null（橙/青/灰） */
function HighlightedJson({ text }: { text: string }) {
  return <>{text.split('\n').map((line, i) => <RenderLine key={i} line={line} />)}</>
}

function RenderLine({ line }: { line: string }) {
  const keyMatch = line.match(/^(\s*"([^"]+)")(\s*:)(.*)$/)
  if (keyMatch) {
    return (
      <span>
        <span className="text-brand-400">{keyMatch[1]}</span>
        <span className="text-text-muted">{keyMatch[3]}</span>
        <RenderValue value={keyMatch[4]} />
      </span>
    )
  }
  return <RenderValue value={line} />
}

function RenderValue({ value }: { value: string }) {
  const trimmed = value.trim()
  let cls = 'text-text-secondary'
  if (/^".*"$/.test(trimmed)) cls = 'text-success'
  else if (/^-?\d+(\.\d+)?$/.test(trimmed)) cls = 'text-warning'
  else if (/^(true|false)$/.test(trimmed)) cls = 'text-info'
  else if (/^(null|undefined)$/.test(trimmed)) cls = 'text-error'
  return <span className={cls}>{value}</span>
}

export function JsonView({
  value,
  title,
  defaultCollapsed = true,
}: JsonViewProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)
  const [copied, setCopied] = useState(false)

  const text = useMemo(() => normalize(value), [value])

  const handleCopy = () => {
    navigator.clipboard?.writeText(text).catch(() => {})
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }

  return (
    <div className="rounded-md border border-border-subtle bg-canvas overflow-hidden">
      <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-elevated/60">
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="flex items-center gap-1 text-xs font-medium text-text-secondary hover:text-text-primary transition-colors"
        >
          {collapsed ? (
            <ChevronRight className="w-3.5 h-3.5 text-text-muted" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5 text-text-muted" />
          )}
          {title || '数据'}
        </button>
        <button
          type="button"
          onClick={handleCopy}
          title="复制原始数据"
          className="ml-auto inline-flex items-center gap-1 text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
        >
          {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      {!collapsed && (
        <pre className="p-2.5 text-[11px] font-mono text-text-secondary overflow-x-auto max-h-64 leading-relaxed whitespace-pre-wrap break-all">
          <HighlightedJson text={text} />
        </pre>
      )}
    </div>
  )
}