/**
 * ChatAgentPanel — 协作工作台右栏（对标 prototype 第 1480-1547 行）
 *
 * 包含三部分：
 * 1. Agent 卡片：头像 + 名称 + 成熟度 + 在岗状态 + 三宫格统计
 * 2. Self-RAG 检索可视化：置信度 + 检索轮数 + SVG 流程图(问→检→评→答)
 * 3. 检索来源列表：文件名 + 页码 + 置信度
 */
import { motion, AnimatePresence } from 'framer-motion'
import {
  Zap, CheckCircle2, Clock, FileText, Database, Sparkles,
} from 'lucide-react'
import type { Agent, Source } from '@/types'
import type { LoopStats } from '@/api/loop'

/** D2-S9: 检索元数据（与 Chat.tsx 同步） */
interface RetrievalMetadata {
  query_type?: string
  retrieval_rounds?: number
  refined_queries?: string[]
  sources?: Source[]
  self_rag_score?: number | null
}

interface ChatAgentPanelProps {
  agent: Agent
  loopStats: LoopStats | null
  retrievalMetadata: RetrievalMetadata | null
  isStreaming: boolean
  statusDotClass: string
  statusLabel: string
}

/** Self-RAG 流程 SVG（问→检→评→答 四节点 + flow-dash 连线） */
function SelfRagFlow({ active }: { active: boolean }) {
  // 四节点坐标：问(20) → 检(80) → 评(140) → 答(200)
  const nodes = [
    { x: 20, label: '问', color: 'var(--brand)' },
    { x: 80, label: '检', color: 'var(--info)' },
    { x: 140, label: '评', color: 'var(--warning)' },
    { x: 200, label: '答', color: 'var(--success)' },
  ]
  return (
    <svg width="240" height="60" viewBox="0 0 240 60" className="w-full" aria-hidden="true">
      {/* 连线（flow-dash 动画） */}
      {nodes.slice(0, -1).map((n, i) => {
        const next = nodes[i + 1]
        return (
          <line
            key={`line-${i}`}
            x1={n.x + 12}
            y1={30}
            x2={next.x - 12}
            y2={30}
            stroke="var(--border-default)"
            strokeWidth={1.5}
            className={active ? 'flow-dash' : ''}
          />
        )
      })}
      {/* 节点 */}
      {nodes.map((n, i) => (
        <g key={`node-${i}`}>
          <circle
            cx={n.x}
            cy={30}
            r={12}
            fill="var(--bg-surface)"
            stroke={n.color}
            strokeWidth={2}
          />
          <text
            x={n.x}
            y={34}
            textAnchor="middle"
            fontSize={11}
            fill={n.color}
            fontWeight={600}
          >
            {n.label}
          </text>
        </g>
      ))}
    </svg>
  )
}

export function ChatAgentPanel({
  agent,
  loopStats,
  retrievalMetadata,
  isStreaming,
  statusDotClass,
  statusLabel,
}: ChatAgentPanelProps) {
  // 统计数据
  const todayTasks = loopStats?.trend?.reduce((s, p) => s + p.count, 0) ?? 0
  const avgResponse = loopStats?.recent_sessions?.length
    ? `${(
        loopStats.recent_sessions.reduce((s, r) => {
          const n = parseFloat(r.responseTime)
          return s + (Number.isNaN(n) ? 0 : n)
        }, 0) / loopStats.recent_sessions.length
      ).toFixed(1)}s`
    : '—'
  const satisfaction = loopStats?.satisfaction_pie?.length
    ? `${Math.round(
        (loopStats.satisfaction_pie
          .filter((p) => p.name === '满意')
          .reduce((s, p) => s + p.value, 0) /
          Math.max(
            loopStats.satisfaction_pie.reduce((s, p) => s + p.value, 1),
            1,
          )) *
          100,
      )}%`
    : '—'

  // Self-RAG 数据
  const hasRetrieval =
    retrievalMetadata &&
    (retrievalMetadata.retrieval_rounds !== undefined ||
      retrievalMetadata.self_rag_score !== undefined ||
      (retrievalMetadata.sources && retrievalMetadata.sources.length > 0))
  const selfRagScore = retrievalMetadata?.self_rag_score
  const retrievalRounds = retrievalMetadata?.retrieval_rounds
  const sources = retrievalMetadata?.sources ?? []

  // 置信度颜色
  const scoreColor =
    selfRagScore === null || selfRagScore === undefined
      ? 'text-text-tertiary'
      : selfRagScore >= 0.7
        ? 'text-success'
        : selfRagScore >= 0.4
          ? 'text-warning'
          : 'text-error'

  return (
    <aside className="hidden lg:flex flex-col w-80 bg-surface border-l border-border-subtle flex-shrink-0 overflow-y-auto scrollbar-thin">
      {/* ===== 1. Agent 卡片 ===== */}
      <div className="p-4 border-b border-border-subtle">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-lg bg-brand-500 text-white flex items-center justify-center font-semibold text-lg flex-shrink-0">
            {agent.name?.charAt(0)?.toUpperCase() || 'A'}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-text-primary truncate">{agent.name}</span>
              <span className="text-xs font-medium px-1.5 py-0.5 rounded bg-brand-50 text-brand-500 flex-shrink-0">
                L3 评估
              </span>
            </div>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span className={`dot ${statusDotClass}`} />
              <span className="text-xs text-text-tertiary">
                {statusLabel === '运行中' ? '在岗' : statusLabel}
              </span>
            </div>
          </div>
        </div>
        {agent.description && (
          <p className="text-xs text-text-tertiary mt-3 leading-relaxed line-clamp-2">
            {agent.description}
          </p>
        )}
      </div>

      {/* ===== 三宫格统计 ===== */}
      <div className="p-4 border-b border-border-subtle">
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-md bg-elevated p-2 text-center">
            <Zap className="w-3.5 h-3.5 text-brand-500 mx-auto mb-1" aria-hidden="true" />
            <div className="text-base font-bold tabular-nums text-text-primary">{todayTasks}</div>
            <div className="text-xs text-text-tertiary">今日协作</div>
          </div>
          <div className="rounded-md bg-elevated p-2 text-center">
            <CheckCircle2 className="w-3.5 h-3.5 text-success mx-auto mb-1" aria-hidden="true" />
            <div className="text-base font-bold tabular-nums text-text-primary">{satisfaction}</div>
            <div className="text-xs text-text-tertiary">满意度</div>
          </div>
          <div className="rounded-md bg-elevated p-2 text-center">
            <Clock className="w-3.5 h-3.5 text-warning mx-auto mb-1" aria-hidden="true" />
            <div className="text-base font-bold tabular-nums text-text-primary">{avgResponse}</div>
            <div className="text-xs text-text-tertiary">响应</div>
          </div>
        </div>
      </div>

      {/* ===== 2. Self-RAG 检索可视化 ===== */}
      <div className="p-4 border-b border-border-subtle">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
            <span className="text-xs font-semibold text-text-primary">Self-RAG 检索</span>
          </div>
          <div className="flex items-center gap-3 text-xs">
            {selfRagScore !== undefined && selfRagScore !== null && (
              <span className={`font-mono font-medium ${scoreColor}`}>
                置信度 {(selfRagScore * 100).toFixed(0)}%
              </span>
            )}
            {retrievalRounds !== undefined && (
              <span className="text-text-tertiary">检索 {retrievalRounds} 轮</span>
            )}
          </div>
        </div>

        {/* SVG 流程图 */}
        <SelfRagFlow active={isStreaming || !!hasRetrieval} />

        {/* 无检索数据时的提示 */}
        {!hasRetrieval && !isStreaming && (
          <p className="text-xs text-text-tertiary text-center mt-2">
            提问后将展示检索流程与置信度
          </p>
        )}
      </div>

      {/* ===== 3. 检索来源列表 ===== */}
      <div className="p-4 flex-1">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5">
            <Database className="w-3.5 h-3.5 text-info" aria-hidden="true" />
            <span className="text-xs font-semibold text-text-primary">检索来源</span>
          </div>
          {sources.length > 0 && (
            <span className="text-xs text-text-tertiary">{sources.length} 条</span>
          )}
        </div>

        <AnimatePresence mode="wait">
          {sources.length > 0 ? (
            <motion.div
              key="sources-list"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="space-y-2"
            >
              {sources.map((src, idx) => {
                // distance 越小越相关，转为置信度分数：1 - distance（夹到 0~1）
                const rawDist = typeof src.distance === 'number' ? src.distance : null
                const score = rawDist === null ? 0.5 : Math.max(0, Math.min(1, 1 - rawDist))
                const scorePct = Math.round(score * 100)
                const scoreColorClass =
                  score >= 0.7 ? 'bg-success' : score >= 0.4 ? 'bg-warning' : 'bg-error'
                // 从 source 字段提取文件名（可能是路径或文件名）
                const sourceName = src.source ? src.source.split(/[\\/]/).pop() || src.source : `来源 ${idx + 1}`
                return (
                  <div
                    key={`src-${idx}`}
                    className="rounded-md border border-border-subtle p-2 hover:border-border-default transition-colors"
                  >
                    <div className="flex items-start gap-2">
                      <FileText className="w-3.5 h-3.5 text-text-tertiary flex-shrink-0 mt-0.5" aria-hidden="true" />
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-medium text-text-primary truncate">
                          {sourceName}
                        </div>
                        {src.file_type && (
                          <div className="text-xs text-text-tertiary mt-0.5">
                            {src.file_type}
                          </div>
                        )}
                        {/* 置信度条 */}
                        <div className="flex items-center gap-1.5 mt-1.5">
                          <div className="flex-1 h-1 bg-elevated rounded-full overflow-hidden">
                            <div
                              className={`h-full ${scoreColorClass} rounded-full transition-all duration-500`}
                              style={{ width: `${scorePct}%` }}
                            />
                          </div>
                          <span className="text-xs font-mono text-text-tertiary tabular-nums">
                            {scorePct}%
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </motion.div>
          ) : (
            <motion.div
              key="sources-empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="text-center py-6"
            >
              <Database className="w-8 h-8 text-text-muted mx-auto mb-2 opacity-50" aria-hidden="true" />
              <p className="text-xs text-text-tertiary">暂无检索来源</p>
              <p className="text-xs text-text-muted mt-0.5">提问后展示知识库引用</p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </aside>
  )
}
