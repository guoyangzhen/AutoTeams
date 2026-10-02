import { useState, memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import { Check, Copy, ImageOff } from 'lucide-react'

// P2-21: 按需注册 highlight.js 语言，避免全量引入 (~500KB → ~60KB)
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import markdown from 'highlight.js/lib/languages/markdown'
import sql from 'highlight.js/lib/languages/sql'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import java from 'highlight.js/lib/languages/java'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import diff from 'highlight.js/lib/languages/diff'
import shell from 'highlight.js/lib/languages/shell'

const highlightLanguages = {
  javascript,
  typescript,
  python,
  bash,
  json,
  yaml,
  markdown,
  sql,
  xml,
  css,
  go,
  rust,
  java,
  c,
  cpp,
  diff,
  shell,
}

// P0-02: URL 安全验证
const ALLOWED_PROTOCOLS = new Set(['http:', 'https:'])
const DANGEROUS_PROTOCOLS = new Set(['javascript:', 'data:', 'vbscript:', 'file:', 'blob:'])
const PRIVATE_IP_PATTERNS = [
  /^https?:\/\/(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}/i,
  /^https?:\/\/(?:169\.254|192\.168)\.\d{1,3}\.\d{1,3}/i,
  /^https?:\/\/172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}/i,
  /^https?:\/\/localhost/i,
  /^https?:\/\/0\.0\.0\.0/i,
  /^https?:\/\/\[::1?\]/i,
]

function isSafeUrl(url: string | undefined): boolean {
  if (!url) return false
  const trimmed = url.trim().toLowerCase()

  if (DANGEROUS_PROTOCOLS.has(trimmed.substring(0, trimmed.indexOf(':') + 1))) {
    return false
  }

  try {
    const parsed = new URL(trimmed, window.location.origin)
    if (!ALLOWED_PROTOCOLS.has(parsed.protocol)) {
      return false
    }
    for (const pattern of PRIVATE_IP_PATTERNS) {
      if (pattern.test(trimmed)) {
        return false
      }
    }
    return true
  } catch {
    return false
  }
}

// P0-02: 自定义安全 schema，扩展默认配置但保持安全
const secureSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    a: [...(defaultSchema.attributes?.a || []), 'href', 'target', 'rel', 'referrerPolicy'],
    img: [...(defaultSchema.attributes?.img || []), 'src', 'alt', 'title', 'loading', 'referrerPolicy'],
  },
  tagNames: [...(defaultSchema.tagNames || []), 'img'],
}

interface MarkdownRendererProps {
  content: string
}

/** 代码块组件：带语言标签和复制按钮 */
function CodeBlock({ language, children }: { language: string; children: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(children)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // 剪贴板 API 可能在非 HTTPS 下不可用
    }
  }

  return (
    <div className="group relative my-3">
      <div className="flex items-center justify-between px-4 py-1.5 border-b border-border-subtle bg-elevated rounded-t-lg">
        <span className="text-caption text-text-tertiary font-mono">{language || 'text'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-caption text-text-tertiary hover:text-text-primary transition-colors"
          title="复制代码"
          aria-label={copied ? '已复制' : '复制代码'}
        >
          {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre className="overflow-x-auto p-4 bg-elevated rounded-b-lg border border-border-subtle border-t-0">
        <code className="hljs text-sm font-mono">{children}</code>
      </pre>
    </div>
  )
}

/** 安全图片组件：加载失败时显示占位符 */
function SafeImage({ src, alt }: { src?: string; alt?: string }) {
  const [hasError, setHasError] = useState(false)

  if (!src || !isSafeUrl(src)) {
    return (
      <div className="flex items-center gap-2 my-3 p-3 rounded-lg bg-elevated border border-border-subtle text-text-tertiary text-sm">
        <ImageOff className="h-4 w-4" />
        <span>图片已被安全策略阻止</span>
      </div>
    )
  }

  if (hasError) {
    return (
      <div className="flex items-center gap-2 my-3 p-3 rounded-lg bg-elevated border border-border-subtle text-text-tertiary text-sm">
        <ImageOff className="h-4 w-4" />
        <span>图片加载失败</span>
      </div>
    )
  }

  return (
    <img
      src={src}
      alt={alt || ''}
      loading="lazy"
      referrerPolicy="no-referrer"
      className="max-w-full rounded-lg my-3 border border-border-subtle"
      onError={() => setHasError(true)}
    />
  )
}

/** 安全链接组件：验证 URL 并添加安全属性 */
function SafeLink({ href, children }: { href?: string; children?: React.ReactNode }) {
  if (!href || !isSafeUrl(href)) {
    return <span className="text-text-tertiary">{children}</span>
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
      className="text-brand-400 hover:text-brand-500 underline underline-offset-2"
    >
      {children}
    </a>
  )
}

function MarkdownRendererBase({ content }: MarkdownRendererProps) {
  return (
    <div className="max-w-none text-text-primary text-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          [rehypeHighlight, { detect: true, ignoreMissing: true, languages: highlightLanguages }],
          // P0-02: 使用加强版安全 schema 过滤危险 HTML/JS
          [rehypeSanitize, secureSchema],
        ]}
        components={{
          // 标题
          h1: ({ children }) => <h1 className="text-h1 mt-6 mb-3 first:mt-0">{children}</h1>,
          h2: ({ children }) => <h2 className="text-h2 mt-5 mb-2 first:mt-0">{children}</h2>,
          h3: ({ children }) => <h3 className="text-h3 mt-4 mb-2 first:mt-0">{children}</h3>,
          h4: ({ children }) => <h4 className="text-base font-semibold mt-3 mb-1 first:mt-0">{children}</h4>,
          // 段落
          p: ({ children }) => <p className="my-2 leading-relaxed">{children}</p>,
          // 列表
          ul: ({ children }) => <ul className="my-2 pl-5 list-disc space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 pl-5 list-decimal space-y-1">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          // 链接 - 使用安全链接组件
          a: ({ href, children }) => <SafeLink href={href}>{children}</SafeLink>,
          // 引用
          blockquote: ({ children }) => (
            <blockquote className="my-3 pl-4 border-l-2 border-brand-500 text-text-secondary italic">
              {children}
            </blockquote>
          ),
          // 分隔线
          hr: () => <hr className="my-4 border-border-subtle" />,
          // 表格
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto">
              <table className="w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="border-b border-border-default">{children}</thead>,
          th: ({ children }) => <th className="px-3 py-2 text-left font-semibold text-text-primary">{children}</th>,
          td: ({ children }) => <td className="px-3 py-2 border-t border-border-subtle text-text-secondary">{children}</td>,
          // 行内代码
          code: ({ node: _node, className, children, ...props }) => {
            const match = /language-(\w+)/.exec(className || '')
            const isInline = !className && !String(children).includes('\n')
            if (isInline) {
              return (
                <code className="px-1.5 py-0.5 rounded bg-elevated text-brand-400 text-[0.875em] font-mono" {...props}>
                  {children}
                </code>
              )
            }
            return <CodeBlock language={match?.[1] || 'text'}>{String(children)}</CodeBlock>
          },
          // 图片 - 使用安全图片组件
          img: ({ src, alt }) => <SafeImage src={src} alt={alt} />,
          // 强调
          strong: ({ children }) => <strong className="font-semibold text-text-primary">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          // 删除线
          del: ({ children }) => <del className="text-text-tertiary">{children}</del>,
          // 任务列表
          input: ({ node: _node, checked, ...props }) => (
            <input
              type="checkbox"
              checked={checked}
              readOnly
              className="mr-2 h-4 w-4 rounded accent-brand-500"
              {...props}
            />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

export const MarkdownRenderer = memo(MarkdownRendererBase)
