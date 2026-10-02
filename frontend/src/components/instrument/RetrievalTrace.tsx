import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, ChevronRight, FileText, Sparkles } from 'lucide-react'
import type { Source } from '@/types'
import { formatRatio, formatRelevance, toRelevanceValue } from '@/utils/format'

/**
 * RetrievalTrace — Self-RAG 检索过程可视化（UI v4 §4.2 / §6 战场四）
 *
 * 后端 agentic_rag 会做多轮检索：判定 query_type → 检索 → 自评相关性 →
 * 不足则改写查询再检索，最终给出 self_rag_score。
 * 审计发现前端只把这些渲染成一个折叠的键值列表，「思考过程」这一
 * 最有说服力的能力表达完全没有形态。
 *
 * 本组件把它还原为**一条可读的检索轨迹**：
 *   轮次 1（原始提问）→ 轮次 2（改写后的查询）→ … → 命中来源与相关度
 *
 * 诚实约束：
 *   - 不编造轮次。refined_queries 有几条就画几条改写轮次
 *   - 相关度统一走 utils/format.formatRelevance（有界倒数衰减），
 *     不再用 `1 - distance` 这种对余弦/L2 距离都不成立的线性映射
 *   - 无任何检索元数据时组件返回 null，不画空壳
 */

interface RetrievalTraceProps {
  /** 查询分类：simple / complex / fallback / error */
  queryType?: string
  /** 实际检索轮数（后端 iterations） */
  rounds?: number
  /** 改写后的查询（仅 complex 检索有） */
  refinedQueries?: string[]
  /** Self-RAG 自评分（0-1） */
  selfRagScore?: number | null
  /** 命中的知识片段 */
  sources?: Source[]
  /** 用户原始提问（作为第一轮展示，缺失则以「原始提问」占位） */
  query?: string
  /** 是否检索进行中（流式尚未结束） */
  active?: boolean
  /** 默认展开 */
  defaultOpen?: boolean
  className?: string
}

const QUERY_TYPE_META: Record<string, { label: string; tone: string }> = {
  simple: { label: '简单检索', tone: 'sig-alive' },
  complex: { label: '多轮推理检索', tone: 'sig-focus' },
  fallback: { label: '降级检索', tone: 'sig-alert' },
  error: { label: '检索异常', tone: 'sig-fault' },
}

export function RetrievalTrace({
  queryType,
  rounds,
  refinedQueries = [],
  selfRagScore,
  sources = [],
  query,
  active = false,
  defaultOpen = false,
  className = '',
}: RetrievalTraceProps) {
  const [open, setOpen] = useState(defaultOpen)

  const hasScore = typeof selfRagScore === 'number'
  const hasRounds = typeof rounds === 'number' && rounds > 0
  if (!hasScore && !hasRounds && refinedQueries.length === 0 && sources.length === 0) {
    return null
  }

  const typeMeta = queryType ? QUERY_TYPE_META[queryType] : undefined

  // 检索轨迹：第 1 轮为原始提问，其后每条 refined_query 各占一轮。
  // 后端 iterations 可能大于 refined_queries.length + 1（同一查询重试），
  // 此处只画有据可依的轮次，不用占位符凑数。
  const steps = [
    { key: 'origin', label: '原始提问', text: query || '—' },
    ...refinedQueries.map((q, i) => ({
      key: `refined-${i}`,
      label: `改写查询 ${i + 1}`,
      text: q,
    })),
  ]

  return (
    <div
      className={`rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elevated)] ${className}`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-left"
      >
        <Search
          className={`w-3.5 h-3.5 flex-shrink-0 ${active ? 'sig-focus' : 'text-[var(--text-tertiary)]'}`}
          aria-hidden="true"
        />
        <span className="text-caption font-semibold text-[var(--text-secondary)]">
          {active ? '检索中' : '检索过程'}
        </span>
        {typeMeta && (
          <span className={`text-caption font-medium ${typeMeta.tone}`}>{typeMeta.label}</span>
        )}
        {hasRounds && (
          <span className="instrument-readout text-caption text-[var(--text-tertiary)]">
            {rounds} 轮
          </span>
        )}
        {hasScore && (
          <span
            className={`instrument-readout text-caption font-semibold ${
              selfRagScore! >= 0.7 ? 'sig-alive' : selfRagScore! >= 0.4 ? 'sig-alert' : 'sig-fault'
            }`}
            title="Self-RAG 自评相关性"
          >
            {formatRatio(selfRagScore)}
          </span>
        )}
        <ChevronRight
          className={`w-3.5 h-3.5 ml-auto flex-shrink-0 text-[var(--text-muted)] transition-transform ${
            open ? 'rotate-90' : ''
          }`}
          aria-hidden="true"
        />
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="trace-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-3 pt-1 space-y-3">
              {/* 检索轨迹 */}
              <ol className="relative space-y-2.5">
                <div
                  className="absolute left-[5px] top-2 bottom-2 w-px bg-[var(--border-default)]"
                  aria-hidden="true"
                />
                {steps.map((step, i) => (
                  <li key={step.key} className="relative pl-5">
                    <span
                      className={`absolute left-0 top-1.5 w-2.5 h-2.5 rounded-full ${
                        i === steps.length - 1 ? 'sig-bg-focus' : 'sig-bg-idle'
                      }`}
                      aria-hidden="true"
                    />
                    <p className="instrument-label">{step.label}</p>
                    <p className="text-caption text-[var(--text-secondary)] leading-relaxed break-words">
                      {step.text}
                    </p>
                  </li>
                ))}
              </ol>

              {/* 命中来源与相关度 */}
              {sources.length > 0 && (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-1.5">
                    <Sparkles className="w-3 h-3 text-[var(--text-tertiary)]" aria-hidden="true" />
                    <span className="instrument-label">命中片段 {sources.length}</span>
                  </div>
                  {sources.map((src, i) => {
                    const pct = toRelevanceValue(src.distance)
                    return (
                      <div key={`src-${i}`} className="flex items-start gap-2">
                        <FileText
                          className="w-3.5 h-3.5 text-[var(--text-tertiary)] flex-shrink-0 mt-0.5"
                          aria-hidden="true"
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-caption text-[var(--text-primary)] truncate">
                            {src.source || `来源 ${i + 1}`}
                          </p>
                          <div className="flex items-center gap-1.5 mt-1">
                            <div className="flex-1 h-1 rounded-full bg-[var(--bg-surface-2,var(--bg-surface))] overflow-hidden">
                              <motion.div
                                className="h-full rounded-full sig-bg-focus"
                                initial={{ width: 0 }}
                                animate={{ width: `${pct}%` }}
                                transition={{ duration: 0.45, delay: i * 0.04 }}
                              />
                            </div>
                            <span className="instrument-readout text-caption text-[var(--text-tertiary)]">
                              {formatRelevance(src.distance)}
                            </span>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
