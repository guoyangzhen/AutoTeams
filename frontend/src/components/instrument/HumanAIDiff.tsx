import { motion } from 'framer-motion'
import { User, Bot, Check, X, HelpCircle } from 'lucide-react'
import { formatRatio } from '@/utils/format'

/**
 * HumanAIDiff — 人机答案对照（UI v4 §6 战场三）
 *
 * 影子模式的**全部价值**就是「AI 答案 vs 真人答案」的可比对，
 * 而审计发现原页面对 ShadowTask.human_answer / ai_answer 一个字都没展示，
 * 只展示了写死的信任度百分比。这是本次修复的最高优先项。
 *
 * 设计要点：
 *   - 左右并排对照，人类基线在左（权威参照），AI 输出在右（待验证）
 *   - 一致/分歧用信号色明确标注，不含糊
 *   - shadowing 阶段 AI 尚未作答，诚实显示「AI 静默观察中」而非编造内容
 */

interface HumanAIDiffProps {
  question: string
  humanAnswer?: string | null
  aiAnswer?: string | null
  confidence?: number | null
  /** pending（未评估）/ match（一致）/ mismatch（分歧） */
  evalResult?: string | null
  taskType?: string
  className?: string
}

const EVAL_META: Record<
  string,
  { label: string; tone: string; bg: string; Icon: typeof Check }
> = {
  match: {
    label: '判断一致',
    tone: 'sig-alive',
    bg: 'bg-[rgba(77,216,192,0.12)]',
    Icon: Check,
  },
  mismatch: {
    label: '存在分歧',
    tone: 'sig-fault',
    bg: 'bg-[rgba(255,93,93,0.12)]',
    Icon: X,
  },
  pending: {
    label: '待评估',
    tone: 'sig-idle',
    bg: 'bg-[var(--bg-elevated)]',
    Icon: HelpCircle,
  },
}

export function HumanAIDiff({
  question,
  humanAnswer,
  aiAnswer,
  confidence,
  evalResult,
  taskType,
  className = '',
}: HumanAIDiffProps) {
  const meta = EVAL_META[evalResult || 'pending'] || EVAL_META.pending
  const EvalIcon = meta.Icon

  return (
    <div className={className}>
      {/* 问题 */}
      <div className="mb-3">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="instrument-label">业务场景</span>
          {taskType && (
            <span className="text-caption px-1.5 py-0.5 rounded-sm bg-[var(--bg-elevated)] text-[var(--text-tertiary)]">
              {taskType}
            </span>
          )}
        </div>
        <p className="text-body-sm text-[var(--text-primary)] leading-relaxed">{question}</p>
      </div>

      {/* 对照区 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* 人类基线 */}
        <motion.div
          initial={{ opacity: 0, x: -8 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.3 }}
          className="rounded-md border border-[var(--border-default)] bg-[var(--bg-elevated)] p-3"
        >
          <div className="flex items-center gap-2 mb-2">
            <span className="w-5 h-5 rounded-full bg-[var(--brand)] flex items-center justify-center flex-shrink-0">
              <User className="w-3 h-3 text-white" aria-hidden="true" />
            </span>
            <span className="instrument-label">人类基线</span>
          </div>
          {humanAnswer ? (
            <p className="text-body-sm text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">
              {humanAnswer}
            </p>
          ) : (
            <p className="text-body-sm text-[var(--text-muted)] italic">暂无人工基线记录</p>
          )}
        </motion.div>

        {/* AI 输出 */}
        <motion.div
          initial={{ opacity: 0, x: 8 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.3, delay: 0.08 }}
          className={`rounded-md border p-3 ${
            evalResult === 'mismatch'
              ? 'border-[var(--sig-fault,#DC2626)]'
              : evalResult === 'match'
                ? 'border-[var(--sig-alive,#16A34A)]'
                : 'border-[var(--border-default)]'
          } bg-[var(--bg-elevated)]`}
        >
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="flex items-center gap-2 min-w-0">
              <span className="w-5 h-5 rounded-full sig-bg-focus flex items-center justify-center flex-shrink-0">
                <Bot className="w-3 h-3 text-white" aria-hidden="true" />
              </span>
              <span className="instrument-label">AI 输出</span>
            </div>
            {typeof confidence === 'number' && (
              <span
                className={`instrument-readout text-caption font-semibold flex-shrink-0 ${
                  confidence >= 0.85 ? 'sig-alive' : confidence >= 0.7 ? 'sig-alert' : 'sig-fault'
                }`}
                title="AI 自评置信度"
              >
                {formatRatio(confidence)}
              </span>
            )}
          </div>
          {aiAnswer ? (
            <p className="text-body-sm text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">
              {aiAnswer}
            </p>
          ) : (
            /* 诚实表达：影子阶段 AI 只观察不输出，不编造内容 */
            <p className="text-body-sm text-[var(--text-muted)] italic">
              AI 静默观察中 —— 该阶段仅记录人工基线，尚未产生输出
            </p>
          )}
        </motion.div>
      </div>

      {/* 评估结论 */}
      {aiAnswer && (
        <div
          className={`mt-3 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full ${meta.bg}`}
        >
          <EvalIcon className={`w-3.5 h-3.5 ${meta.tone}`} strokeWidth={2.5} aria-hidden="true" />
          <span className={`text-caption font-semibold ${meta.tone}`}>{meta.label}</span>
        </div>
      )}
    </div>
  )
}
