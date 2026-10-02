/**
 * EvolutionPage — 组织进化中枢（AutoTeams 11 屏 · 克制·编辑式）。
 *
 * 视觉基线：autoteams_ui/DESIGN.md 与 screens-v2/11-evolution-hub.html。
 * 纸面 #FAFAF9、内容面 #FFFFFF、1px 发丝线分隔、6px 状态点、单一主色
 * #1F4FD8（每屏 ≤1 枚实心主按钮，≤3 处主色），层级由排版与留白建立，
 * 不使用描边卡片堆、彩色胶囊、渐变或进度环。
 *
 * 一屏回答一个问题：**组织进化得怎么样，下一步该做什么。**
 * 四个区块：
 * 1. 进化飞轮 —— 观测 → 归因 → 提案 → 裁决 → 验证 的状态机，数字全部来自真实接口
 * 2. 突变提议审计 —— Advisor 建议逐条裁决（采纳 / 驳回），裁决走居中浮层三事实确认
 * 3. 进化历程 —— 时间线与 Runtime 版本对比切换
 * 4. 指标对比 —— 成熟度阶梯 L1–L5、组织指标横排、组织版本与回滚
 *
 * 数据来源：evolution + runtime + workforce API；任一端点失败只降级对应区块，
 * 不拖垮整页（spec.md §2.5 约束）。
 */
import { useState, useEffect, useCallback, useMemo, useRef, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Lightbulb, GitCompare, RefreshCw, Undo2 } from 'lucide-react'
import Layout from '@/components/Layout'
import { Spinner } from '@/components/ui/Spinner'
import { ApiErrorState } from '@/components/ApiErrorState'
import { EvolutionTimeline } from '@/components/EvolutionTimeline'
import { ModalShell } from '@/components/ui/ModalShell'
import { EmptyState } from '@/components/ui/EmptyState'
import * as evolutionApi from '@/api/evolution'
import * as runtimeApi from '@/api/runtime'
import * as workforceApi from '@/api/workforce'
import * as shadowApi from '@/api/shadow'
import * as counterfactualApi from '@/api/counterfactualShadow'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { SECTION_LABELS } from '@/utils/fieldMappings'
import type {
  AdvisorSuggestion,
  AgentRunMetrics,
  AuthorizationLevel,
  EvolutionTimelineItem,
  MaturityRating,
  OrgMetrics,
  OptimizationHistoryItem,
  RuntimeDiff,
  RuntimeDiffChange,
  RuntimeVersionSummary,
  ShadowStatus,
  ShadowTask,
  SuggestionType,
} from '@/types'

/** SOP 版本对比范围（动态取最近两个版本，避免硬编码版本号导致 404） */
interface DiffRange {
  from: string
  to: string
}

// ---------------------------------------------------------------------------
// 设计令牌与基础零件
// ---------------------------------------------------------------------------

const DOT_TONE_CLASS: Record<'positive' | 'caution' | 'blocked' | 'idle' | 'primary', string> = {
  positive: 'bg-at-positive',
  caution: 'bg-at-caution',
  blocked: 'bg-at-blocked',
  idle: 'bg-at-hairline',
  primary: 'bg-at-primary',
}

/** 6px 实心圆点 + 13px 文字（DESIGN.md §5：禁用彩色填充胶囊） */
function StatusDot({
  tone,
}: {
  tone: 'positive' | 'caution' | 'blocked' | 'idle' | 'primary'
}) {
  return (
    <span
      className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${DOT_TONE_CLASS[tone]}`}
      aria-hidden="true"
    />
  )
}

/** 区块标题：17px/600 墨色 + 右侧 12px 灰注记 */
function SectionHeading({ title, meta }: { title: string; meta?: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 mb-4">
      <h2 className="text-[17px] leading-6 font-semibold text-at-ink">{title}</h2>
      {meta ? <span className="text-xs leading-4 text-at-subtle">{meta}</span> : null}
    </div>
  )
}

/** 次级文字链（筛选、行动入口） */
function TextLink({
  children,
  onClick,
  disabled,
  tone = 'muted',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  tone?: 'muted' | 'blocked' | 'primary'
}) {
  const TONE_CLASS: Record<'muted' | 'blocked' | 'primary', string> = {
    muted: 'text-at-muted hover:text-at-ink',
    blocked: 'text-at-blocked hover:opacity-80',
    primary: 'text-at-primary hover:opacity-80',
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`text-[13px] leading-5 transition-colors duration-150 disabled:opacity-40 disabled:cursor-not-allowed ${TONE_CLASS[tone]}`}
    >
      {children}
    </button>
  )
}

/** 文字链式筛选组：选中项墨色加粗，未选中灰 */
function FilterLinks({
  options,
  active,
  onChange,
}: {
  options: { key: string; label: string; count: number }[]
  active: string
  onChange: (key: string) => void
}) {
  return (
    <span className="flex items-center gap-4">
      {options.map((opt) => (
        <button
          key={opt.key}
          type="button"
          onClick={() => onChange(opt.key)}
          className={
            active === opt.key
              ? 'text-at-ink font-medium'
              : 'text-at-muted hover:text-at-ink transition-colors duration-150'
          }
        >
          {opt.label} {opt.count}
        </button>
      ))}
    </span>
  )
}

/** 空态：一句说明，无插画（DESIGN.md §5） */
function HairlineEmpty({ text }: { text: string }) {
  return (
    <div className="border-t border-at-hairline py-10 text-center">
      <p className="text-sm text-at-muted">{text}</p>
    </div>
  )
}

/** 编译时间格式化：列表内仅到分钟，避免占宽 */
function formatCompileTime(ts: string): string {
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ts
  }
}

// ---------------------------------------------------------------------------
// 1. 进化飞轮 —— 状态机
// ---------------------------------------------------------------------------

/** 飞轮阶段状态由真实数据推导，不做乐观假设 */
type StageState = 'done' | 'active' | 'idle'

interface FlywheelStage {
  key: string
  name: string
  /** 该阶段的量化事实（来自真实接口） */
  figure: string
  /** 一句话说明，解释这个数字是什么 */
  fact: string
  state: StageState
}

/**
 * 五段飞轮：观测 → 归因 → 提案 → 裁决 → 验证。
 * 每一段是否「已完成」只取决于对应数据是否真实产生过，
 * 未产生则停留在「待启动」，不伪造进度。
 */
function buildFlywheelStages(input: {
  events: number
  optimizations: OptimizationHistoryItem[]
  suggestions: AdvisorSuggestion[]
}): FlywheelStage[] {
  const { events, optimizations, suggestions } = input
  const attributed = optimizations.filter((o) => o.type === 'feedback' || o.type === 'gap').length
  const proposed = suggestions.length
  const applied = suggestions.filter((s) => s.status === 'applied').length
  const rejected = suggestions.filter((s) => s.status === 'rejected').length
  const pending = suggestions.filter((s) => s.status === 'pending').length
  const verified = optimizations.filter((o) => o.applied).length

  return [
    {
      key: 'observe',
      name: '观测',
      figure: `${events} 条`,
      fact: events > 0 ? '协作事件已入库，组织运行被持续采样' : '尚无协作事件，运行数据未开始采集',
      state: events > 0 ? 'done' : 'idle',
    },
    {
      key: 'attribute',
      name: '归因',
      figure: `${attributed} 项`,
      fact:
        attributed > 0
          ? '反馈与知识缺口分析已产出优化项'
          : events > 0
            ? '已采集运行事件，等待缺口分析'
            : '缺少运行事件，暂无法归因',
      state: attributed > 0 ? 'done' : events > 0 ? 'active' : 'idle',
    },
    {
      key: 'propose',
      name: '提案',
      figure: `${proposed} 条`,
      fact: proposed > 0 ? 'AI 优化顾问已生成待审建议' : '尚未生成优化建议',
      state: proposed > 0 ? 'done' : attributed > 0 ? 'active' : 'idle',
    },
    {
      key: 'adjudicate',
      name: '裁决',
      figure: `${applied + rejected} / ${proposed}`,
      fact:
        pending > 0
          ? `已裁决 ${applied + rejected} 条，${pending} 条待审`
          : proposed > 0
            ? '全部建议已裁决完毕'
            : '没有待裁决的提议',
      state: applied + rejected > 0 ? 'done' : pending > 0 ? 'active' : 'idle',
    },
    {
      key: 'verify',
      name: '验证',
      figure: `${verified} 项`,
      fact:
        verified > 0
          ? '优化项已生效并回写版本，可做退化回滚检查'
          : '尚无已生效的优化项',
      state: verified > 0 ? 'done' : applied > 0 ? 'active' : 'idle',
    },
  ]
}

const STAGE_STATE_LABEL: Record<StageState, string> = {
  done: '已闭环',
  active: '进行中',
  idle: '待启动',
}

/** 水平步进轨道：1px 发丝线底轨 + 已完成段实线，节点 6px 圆点（对齐原型 11 stepper） */
function FlywheelTrack({ stages }: { stages: FlywheelStage[] }) {
  const doneCount = stages.filter((s) => s.state === 'done').length
  const fillPercent = (doneCount / stages.length) * 100

  return (
    <div className="relative">
      <div className="absolute left-0 right-0 top-[3px] h-px bg-at-hairline" aria-hidden="true" />
      {fillPercent > 0 && (
        <div
          className="absolute left-0 top-[3px] h-px bg-at-ink"
          style={{ width: `${fillPercent}%` }}
          aria-hidden="true"
        />
      )}

      <ol className="relative flex items-start justify-between">
        {stages.map((stage) => (
          <li
            key={stage.key}
            className="flex flex-col items-center text-center"
            style={{ flex: '1 1 0', minWidth: 0 }}
          >
            <span
              className={[
                'w-1.5 h-1.5 rounded-full ring-4 ring-at-card',
                stage.state === 'done'
                  ? 'bg-at-ink'
                  : stage.state === 'active'
                    ? 'bg-at-primary'
                    : 'bg-at-card border border-at-hairline',
              ].join(' ')}
              aria-hidden="true"
            />
            <span
              className={`mt-2 text-xs leading-4 ${
                stage.state === 'idle' ? 'text-at-muted' : 'text-at-ink font-medium'
              }`}
            >
              {stage.name}
            </span>
            <span className="mt-0.5 font-mono text-xs leading-4 text-at-subtle tabular-nums">
              {stage.figure}
            </span>
          </li>
        ))}
      </ol>

      {/* 每段事实说明：单列发丝线列表，与步进轨道一一对应 */}
      <ul className="mt-5 border-t border-at-hairline">
        {stages.map((stage) => (
          <li
            key={`${stage.key}-fact`}
            className="flex items-center justify-between gap-4 py-2 border-b border-at-hairline"
          >
            <div className="flex items-center gap-2 min-w-0">
              <StatusDot
                tone={stage.state === 'done' ? 'positive' : stage.state === 'active' ? 'primary' : 'idle'}
              />
              <span className="w-10 shrink-0 text-sm text-at-ink">{stage.name}</span>
              <span className="truncate text-sm text-at-muted">{stage.fact}</span>
            </div>
            <span
              className={`shrink-0 text-xs leading-4 ${
                stage.state === 'idle' ? 'text-at-subtle' : 'text-at-muted'
              }`}
            >
              {STAGE_STATE_LABEL[stage.state]}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 2. 突变提议审计
// ---------------------------------------------------------------------------

const SUGGESTION_TYPE_LABEL: Record<SuggestionType, string> = {
  knowledge: '知识补充',
  process: '流程优化',
  capability: '能力新增',
  organization: '组织调整',
}

const AUTHORIZATION_LABEL: Record<AuthorizationLevel, string> = {
  auto: '自动执行',
  authorized: '需授权',
  approval: '需审批',
}

const AUTHORIZATION_TIP: Record<AuthorizationLevel, string> = {
  auto: '低风险变更，系统可直接自动执行',
  authorized: '默认授权级别；需管理员授权后方可执行',
  approval: '高风险变更，需审批通过后方可执行',
}

/**
 * 解析建议的授权级别（P1-2 授权闭环）。
 * 后端 continuous_optimizer 暂未下发 authorization_level：
 * 已下发则直接使用，否则按建议类型的风险高低推断，无法推断时降级为「需授权」。
 */
function resolveAuthorizationLevel(s: AdvisorSuggestion): AuthorizationLevel {
  if (s.authorization_level) return s.authorization_level
  if (s.type === 'organization') return 'approval'
  if (s.type === 'knowledge') return 'auto'
  return 'authorized'
}

/** 裁决意图（浮层确认用） */
interface Verdict {
  kind: 'apply' | 'reject'
  suggestion: AdvisorSuggestion
}

/** 单条提议：一行式审计记录，发丝线分隔，不用卡片包裹 */
function SuggestionRow({
  suggestion,
  onVerdict,
  busy,
}: {
  suggestion: AdvisorSuggestion
  onVerdict: (v: Verdict) => void
  busy: boolean
}) {
  const handled = suggestion.status !== 'pending'
  const auth = resolveAuthorizationLevel(suggestion)

  const dotTone =
    suggestion.status === 'applied'
      ? 'positive'
      : suggestion.status === 'rejected'
        ? 'blocked'
        : auth === 'approval'
          ? 'caution'
          : 'idle'

  return (
    <li className={`border-b border-at-hairline py-3 ${handled ? 'opacity-60' : ''}`}>
      <div className="flex items-start justify-between gap-6">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <StatusDot tone={dotTone} />
            <span className="text-xs text-at-muted">{SUGGESTION_TYPE_LABEL[suggestion.type]}</span>
            <span className="text-xs text-at-subtle">·</span>
            <span className="text-xs text-at-muted" title={AUTHORIZATION_TIP[auth]}>
              {AUTHORIZATION_LABEL[auth]}
            </span>
            {handled && (
              <span className="text-xs text-at-subtle">
                · {suggestion.status === 'applied' ? '已采纳' : '已驳回'}
              </span>
            )}
          </div>

          <p className="mt-1 text-sm text-at-ink">{suggestion.title}</p>
          <p className="mt-0.5 text-[13px] leading-5 text-at-muted">{suggestion.description}</p>

          {suggestion.impact && (
            <p className="mt-0.5 text-[13px] leading-5 text-at-muted">
              预期效果：{suggestion.impact}
            </p>
          )}
        </div>

        {!handled && (
          <div className="flex shrink-0 items-center gap-5 pt-0.5">
            <TextLink
              tone="primary"
              disabled={busy}
              onClick={() => onVerdict({ kind: 'apply', suggestion })}
            >
              采纳
            </TextLink>
            <TextLink disabled={busy} onClick={() => onVerdict({ kind: 'reject', suggestion })}>
              驳回
            </TextLink>
          </div>
        )}
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// 4. 指标对比
// ---------------------------------------------------------------------------

/** 成熟度阶梯定义（对齐后端 org_analytics.MATURITY_LEVELS） */
const MATURITY_LADDER: { level: string; name: string; description: string }[] = [
  { level: 'L1', name: '辅助', description: 'AI 仅提供建议，人类执行全部' },
  { level: 'L2', name: '协作', description: 'AI 执行部分，人类审批关键节点' },
  { level: 'L3', name: '自动', description: 'AI 自主执行标准流程，异常转人工' },
  { level: 'L4', name: '自治', description: 'AI 自主处理复杂场景，仅战略决策由人类' },
  { level: 'L5', name: '进化', description: 'AI 自我优化组织结构与流程' },
]

/** 成熟度阶梯：当前等级用主色实心点，已达等级用墨色点 */
function MaturityLadder({ rating }: { rating: MaturityRating | null }) {
  const currentIdx = rating ? MATURITY_LADDER.findIndex((m) => m.level === rating.level) : -1
  return (
    <ol className="border-t border-at-hairline">
      {MATURITY_LADDER.map((step, idx) => {
        const isCurrent = idx === currentIdx
        const isReached = currentIdx >= 0 && idx <= currentIdx
        return (
          <li
            key={step.level}
            className="flex items-center justify-between gap-4 border-b border-at-hairline py-2.5"
          >
            <div className="flex min-w-0 items-center gap-3">
              <span
                className={[
                  'w-1.5 h-1.5 rounded-full shrink-0',
                  isCurrent
                    ? 'bg-at-primary'
                    : isReached
                      ? 'bg-at-ink'
                      : 'bg-at-card border border-at-hairline',
                ].join(' ')}
                aria-hidden="true"
              />
              <span className="w-6 shrink-0 font-mono text-[13px] text-at-subtle">{step.level}</span>
              <span
                className={`w-10 shrink-0 text-sm ${isCurrent ? 'font-medium text-at-ink' : 'text-at-muted'}`}
              >
                {step.name}
              </span>
              <span className="truncate text-[13px] text-at-muted">{step.description}</span>
            </div>
            {isCurrent && (
              <span className="shrink-0 text-xs text-at-primary">
                {rating?.achieved ? '已达成' : '未达 MVP 目标'}
              </span>
            )}
          </li>
        )
      })}
    </ol>
  )
}

/** 单条组织指标：左标签 + 2px 细线配数字（DESIGN.md §5：禁止环形进度） */
function MetricRow({
  label,
  display,
  ratio,
  hint,
}: {
  label: string
  display: string
  /** 0–1 归一化比例，决定细线长度 */
  ratio: number
  hint?: string
}) {
  const pct = Math.max(0, Math.min(1, ratio)) * 100
  return (
    <li className="border-b border-at-hairline py-2.5">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm text-at-muted">{label}</span>
        <span className="font-mono text-sm tabular-nums text-at-ink">{display}</span>
      </div>
      <div className="mt-1.5 h-0.5 w-full bg-at-hairline" aria-hidden="true">
        <div className="h-0.5 bg-at-ink" style={{ width: `${pct}%` }} />
      </div>
      {hint && <p className="mt-1 text-xs text-at-subtle">{hint}</p>}
    </li>
  )
}

/** 标量变更 detail 格式化：completeness 为 0-1 分数，换算为百分比以与全站展示一致 */
function formatScalarDetail(section: string, detail: unknown): string {
  if (typeof detail !== 'string') return ''
  if (section !== 'completeness') return detail
  return detail
    .split('→')
    .map((part) => {
      const n = Number(part.trim())
      return Number.isFinite(n) ? `${Math.round(n * 100)}%` : part.trim()
    })
    .join(' → ')
}

/** 版本对比：按 section 归类合并，隐藏后端原始 key，只展示归类摘要与标量取值变化 */
function VersionDiffSummary({ diff, range }: { diff: RuntimeDiff; range: DiffRange }) {
  const groups = useMemo(() => {
    const map = new Map<string, RuntimeDiffChange[]>()
    for (const c of diff.changes) {
      const arr = map.get(c.section) ?? []
      arr.push(c)
      map.set(c.section, arr)
    }
    return Array.from(map.entries())
  }, [diff])

  return (
    <div>
      <div className="flex items-center gap-2 border-b border-at-hairline pb-3">
        <span className="font-mono text-[13px] text-at-muted">{range.from}</span>
        <GitCompare className="w-3.5 h-3.5 text-at-subtle" aria-hidden="true" />
        <span className="font-mono text-[13px] text-at-ink">{range.to}</span>
        <span className="ml-auto text-xs tabular-nums text-at-subtle">
          共 {diff.changes.length} 项变更
        </span>
      </div>

      {diff.changes.length === 0 ? (
        <p className="py-4 text-[13px] text-at-muted">两个版本无差异</p>
      ) : (
        <ul>
          {groups.map(([section, changes]) => {
            const counts = { added: 0, removed: 0, modified: 0 }
            for (const c of changes) counts[c.change_type]++
            const detail = changes.find((c) => c.detail)?.detail
            const summary = [
              counts.added > 0 ? `新增 ${counts.added}` : null,
              counts.removed > 0 ? `移除 ${counts.removed}` : null,
              counts.modified > 0 ? `修改 ${counts.modified}` : null,
            ]
              .filter(Boolean)
              .join(' · ')
            return (
              <li
                key={section}
                className="flex items-center justify-between gap-4 border-b border-at-hairline py-2.5"
              >
                <div className="min-w-0">
                  <span className="text-sm text-at-ink">{SECTION_LABELS[section] || section}</span>
                  {detail && (
                    <span className="ml-2 font-mono text-xs text-at-muted">
                      {formatScalarDetail(section, detail)}
                    </span>
                  )}
                </div>
                <span className="shrink-0 text-xs tabular-nums text-at-subtle">
                  {summary || '取值变化'}
                </span>
              </li>
            )
          })}
        </ul>
      )}

      <p className="pt-3 text-[13px] leading-5 text-at-muted">{diff.summary}</p>
    </div>
  )
}
// ---------------------------------------------------------------------------
// 11-evolution-hub 原型还原：影子考核、知识缺口与绩效榜
// 全部改为真实 API 驱动（计划 §4.5.1：不使用任何硬编码展示数据）
// ---------------------------------------------------------------------------

/** SHADOW_STEPS：阶段轨道定义（展示用配置，非业务数据） */
const SHADOW_STEPS = [
  { step: 1, label: '影子陪伴' },
  { step: 2, label: '独立评估' },
  { step: 3, label: '达标审查' },
  { step: 4, label: '正式转正' },
]

/** 后端 ShadowStatus → 轨道步数（1-4）；qualified 及以后全部落在第 4 步 */
function shadowStatusToStep(status: ShadowStatus): 1 | 2 | 3 | 4 {
  switch (status) {
    case 'shadowing':
      return 1
    case 'evaluating':
      return 2
    case 'qualified':
      return 3
    case 'autonomous':
      return 4
    default:
      return 1
  }
}

/**
 * 影子考核区块：数据源 GET /shadow/tasks（真实影子任务）。
 * 对齐度 = eval_result 命中率（match / 总已评估）；未评估任务展示「待评估」。
 * 「放行」调用 POST /shadow/tasks/{id}/promote（qualified → autonomous）。
 */
function ShadowInternshipSection({
  tasks,
  agentNameMap,
  promotingId,
  onPromote,
}: {
  tasks: ShadowTask[]
  agentNameMap: Record<string, string>
  promotingId: string | null
  onPromote: (taskId: string) => void
}) {
  const rows = useMemo(() => {
    return tasks.slice(0, 6).map((t) => {
      // 对齐度：单任务已评估时 = 命中则 100% / 未命中则 0%；展示 confidence 作为参考
      const evaluated = t.eval_result !== 'pending'
      const alignment = evaluated ? (t.eval_result === 'match' ? 100 : 0) : null
      const agentName = t.agent_id ? agentNameMap[t.agent_id] || undefined : undefined
      return {
        id: t.id,
        name: agentName || '未绑定员工',
        badge: t.agent_id || '—',
        alignment,
        confidence: t.confidence,
        step: shadowStatusToStep(t.status),
        promotable: t.status === 'qualified',
      }
    })
  }, [tasks, agentNameMap])

  if (rows.length === 0) {
    return (
      <section className="flex flex-col">
        <SectionHeading title="影子考核" meta="实习考核 0 人" />
        <HairlineEmpty text="暂无影子考核任务。在「AI 员工」页创建影子任务后，这里将展示四步晋升轨道。" />
      </section>
    )
  }

  return (
    <section className="flex flex-col">
      <SectionHeading
        title="影子考核"
        meta={`实习考核 ${tasks.length} 项`}
      />
      <div className="flex flex-col border-t border-at-hairline">
        {rows.map((emp) => {
          const activeWidthPct = ((emp.step - 1) / 3) * 100
          return (
            <div
              key={emp.id}
              className="h-16 flex items-center justify-between border-b border-at-hairline"
            >
              {/* Monogram + Info */}
              <div className="flex items-center gap-3.5 w-60 shrink-0">
                <div className="w-8 h-8 rounded-full bg-at-surface-alt text-at-ink flex items-center justify-center text-xs font-medium border border-at-hairline">
                  {emp.name.slice(0, 1)}
                </div>
                <div className="flex flex-col min-w-0">
                  <span className="text-[14px] leading-tight font-medium text-at-ink truncate">
                    {emp.name}
                  </span>
                  <span className="font-mono text-xs text-at-muted mt-0.5 truncate">
                    {emp.badge}
                  </span>
                </div>
              </div>

              {/* 4-step horizontal track */}
              <div className="flex-1 max-w-lg px-8">
                <div className="relative flex items-center justify-between">
                  {/* Base Track Line */}
                  <div className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-px bg-at-hairline z-0" />
                  {/* Active Sub-line */}
                  <div
                    className="absolute left-0 top-1/2 -translate-y-1/2 h-px bg-at-ink z-0 transition-all duration-300"
                    style={{ width: `${activeWidthPct}%` }}
                  />
                  {/* Step dots */}
                  {SHADOW_STEPS.map((s) => {
                    const isDone = emp.step > s.step
                    const isCurrent = emp.step === s.step
                    return (
                      <div key={s.step} className="relative z-10 flex flex-col items-center">
                        <span
                          className={`w-2 h-2 rounded-full ring-4 ring-white transition-all ${
                            isCurrent
                              ? 'bg-at-primary scale-110'
                              : isDone
                              ? 'bg-at-ink'
                              : 'border border-at-hairline bg-at-card'
                          }`}
                        />
                        <span
                          className={`text-[11px] leading-none mt-2 ${
                            isCurrent
                              ? 'text-at-ink font-medium'
                              : 'text-at-muted'
                          }`}
                        >
                          {s.label}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Alignment Figure & Action */}
              <div className="flex items-center gap-8 pl-4">
                <span className="text-[13px] font-medium text-at-ink font-mono tabular-nums w-14 text-right">
                  {emp.alignment === null
                    ? '待评估'
                    : emp.alignment === 100
                      ? '100%'
                      : emp.alignment === 0
                        ? '0%'
                        : '—'}
                </span>
                {emp.promotable ? (
                  <TextLink
                    tone="primary"
                    disabled={promotingId === emp.id}
                    onClick={() => onPromote(emp.id)}
                  >
                    {promotingId === emp.id ? '放行中…' : '放行'}
                  </TextLink>
                ) : (
                  <TextLink tone="muted" disabled>
                    {emp.step >= 4 ? '已转正' : '评估中'}
                  </TextLink>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

/**
 * 知识缺口区块：数据源 GET /shadow/counterfactual/sessions（真实反事实推演）。
 * 语义对齐度低的推演场景即知识缺口：对齐度越低说明员工在该场景的知识越薄弱。
 * 影响任务数 = 该场景累计推演次数。
 */
interface KnowledgeGapItem {
  id: string
  title: string
  impactCount: number
  alignmentPct: number
}

function KnowledgeGapsSection({
  gaps,
  onAction,
}: {
  gaps: KnowledgeGapItem[]
  onAction: (item: KnowledgeGapItem) => void
}) {
  if (gaps.length === 0) {
    return (
      <section className="flex flex-col">
        <SectionHeading title="知识缺口" meta="高频干预归因 0 项" />
        <HairlineEmpty text="暂无知识缺口。提交反事实推演后，对齐度低的场景将自动归因为缺口。" />
      </section>
    )
  }
  return (
    <section className="flex flex-col">
      <SectionHeading
        title="知识缺口"
        meta={`高频干预归因 ${gaps.length} 项`}
      />
      <div className="flex flex-col border-t border-at-hairline">
        {gaps.map((gap) => (
          <div
            key={gap.id}
            className="h-12 flex items-center justify-between border-b border-at-hairline"
          >
            <span className="text-[13px] text-at-ink">{gap.title}</span>
            <div className="flex items-center gap-8">
              <span className="text-xs text-at-muted">对齐度 {gap.alignmentPct.toFixed(0)}%</span>
              <TextLink onClick={() => onAction(gap)}>
                编排规程卡补丁
              </TextLink>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

/**
 * 绩效榜区块：数据源 GET /workforce/{enterprise_id}（真实 AgentRunMetrics）。
 * 榜单指标 = avg_satisfaction（平均满意度，0-5 分制展示 1 位小数）。
 */
interface LeaderboardItem {
  rank: string
  name: string
  role: string
  agentId: string
  score: string
}

function LeaderboardSection({ items }: { items: LeaderboardItem[] }) {
  if (items.length === 0) {
    return (
      <div className="flex flex-col">
        <SectionHeading title="绩效榜" meta="Top 3 卓越员工" />
        <HairlineEmpty text="暂无绩效数据。数字员工上线并执行任务后，这里将按满意度排序展示。" />
      </div>
    )
  }
  return (
    <div className="flex flex-col">
      <SectionHeading title="绩效榜" meta={`Top ${items.length} 卓越员工`} />
      <div className="flex flex-col border-t border-at-hairline">
        {items.map((p) => (
          <div
            key={p.rank}
            className="h-12 flex items-center justify-between border-b border-at-hairline"
          >
            <div className="flex items-center gap-3">
              <span className="font-mono text-xs text-at-subtle w-4">{p.rank}</span>
              <div className="w-6 h-6 rounded-full bg-at-surface-alt text-at-ink flex items-center justify-center text-[11px] font-medium border border-at-hairline">
                {p.name.slice(0, 1)}
              </div>
              <span className="text-[13px] text-at-ink font-medium">{p.name}</span>
              <span className="text-xs text-at-muted">
                {p.role} · {p.agentId}
              </span>
            </div>
            <span className="font-mono text-xs font-semibold text-at-ink tabular-nums">
              {p.score}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}


// ---------------------------------------------------------------------------
// 裁决确认浮层（DESIGN.md §5：动作 / 影响 / 回滚 三行事实）
// ---------------------------------------------------------------------------

function VerdictDialog({
  verdict,
  busy,
  onCancel,
  onConfirm,
}: {
  verdict: Verdict
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const isApply = verdict.kind === 'apply'
  const s = verdict.suggestion
  const facts: { label: string; value: string }[] = [
    { label: '动作', value: `${isApply ? '采纳' : '驳回'}建议「${s.title}」` },
    { label: '影响', value: s.impact || s.description },
    {
      label: '回滚',
      value: isApply
        ? '采纳记录进入进化时间线，可随时驳回同类提议'
        : '驳回记录保留在审计轨迹中，可重新生成',
    },
  ]

  return (
    <ModalShell
      open
      onClose={onCancel}
      labelledBy="verdict-dialog-title"
      overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-at-ink/20"
      panelClassName="w-[420px] max-w-[92vw] rounded-[10px] bg-at-card p-6 shadow-[0_12px_32px_rgba(0,0,0,0.10)]"
      closeOnOverlayClick={!busy}
    >
      <h3 id="verdict-dialog-title" className="text-[17px] leading-6 font-semibold text-at-ink">
        {isApply ? '确认采纳这条提议' : '确认驳回这条提议'}
      </h3>

      <dl className="mt-4 border-t border-at-hairline">
        {facts.map((f) => (
          <div key={f.label} className="flex gap-4 border-b border-at-hairline py-2.5">
            <dt className="w-8 shrink-0 pt-0.5 text-xs text-at-subtle">{f.label}</dt>
            <dd className="min-w-0 text-[13px] leading-5 text-at-ink">{f.value}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-6 flex items-center justify-end gap-4">
        <TextLink onClick={onCancel} disabled={busy}>
          取消
        </TextLink>
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          className="rounded-[6px] bg-at-primary px-4 py-2 text-[13px] leading-5 font-medium text-at-on-primary transition-opacity hover:opacity-95 disabled:opacity-50"
        >
          {busy ? '提交中…' : isApply ? '确认采纳' : '确认驳回'}
        </button>
      </div>
    </ModalShell>
  )
}

// ---------------------------------------------------------------------------
// 页面主体
// ---------------------------------------------------------------------------

export default function EvolutionPage() {
  const enterpriseId = useEnterpriseId()

  const [suggestions, setSuggestions] = useState<AdvisorSuggestion[]>([])
  const [timeline, setTimeline] = useState<EvolutionTimelineItem[]>([])
  const [optimizations, setOptimizations] = useState<OptimizationHistoryItem[]>([])
  const [metrics, setMetrics] = useState<OrgMetrics | null>(null)
  const [maturity, setMaturity] = useState<MaturityRating | null>(null)
  const [diff, setDiff] = useState<RuntimeDiff | null>(null)
  const [diffRange, setDiffRange] = useState<DiffRange | null>(null)
  const [versions, setVersions] = useState<RuntimeVersionSummary[]>([])

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actioningId, setActioningId] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  const [rollingBack, setRollingBack] = useState(false)
  const [agentNameMap, setAgentNameMap] = useState<Record<string, string>>({})
  // 影子考核：真实影子任务（GET /shadow/tasks）
  const [shadowTasks, setShadowTasks] = useState<ShadowTask[]>([])
  // 知识缺口：真实反事实推演低对齐度场景（GET /shadow/counterfactual/sessions）
  const [knowledgeGaps, setKnowledgeGaps] = useState<KnowledgeGapItem[]>([])
  const [promotingTaskId, setPromotingTaskId] = useState<string | null>(null)
  /** 绩效榜数据源：workforce 最新一次加载结果（ref 保持，避免重复状态） */
  const workforceItemsRef = useRef<AgentRunMetrics[]>([])
  const [suggestionFilter, setSuggestionFilter] = useState<'pending' | 'all'>('pending')
  const [historyTab, setHistoryTab] = useState<'timeline' | 'diff'>('timeline')
  const [verdict, setVerdict] = useState<Verdict | null>(null)

  const handleGapAction = (gap: KnowledgeGapItem) => {
    toast.info(`正在为缺口「${gap.title}」准备规程卡补丁，请前往规程编排工作台查看`)
  }


  const loadData = useCallback(async () => {
    if (!enterpriseId) return
    setLoading(true)
    setError(null)
    try {
      // 各端点独立降级：单点失败只影响对应区块，不拖垮整页
      const [sugsRes, tlRes, optRes, metricsRes, maturityRes, versionsRes, workforceRes, shadowRes, cfRes] =
        await Promise.all([
          evolutionApi.listSuggestions(enterpriseId).catch(() => null),
          evolutionApi.getEvolutionTimeline(enterpriseId).catch(() => null),
          evolutionApi.listOptimizations(enterpriseId).catch(() => null),
          evolutionApi.getOrgMetrics(enterpriseId).catch(() => null),
          evolutionApi.getMaturity(enterpriseId).catch(() => null),
          runtimeApi.listRuntimeVersions(enterpriseId).catch(() => null),
          workforceApi.listWorkforce(enterpriseId).catch(() => null),
          shadowApi.listShadowTasks(enterpriseId, { limit: 20 }).catch(() => null),
          counterfactualApi.listCounterfactualSessions(enterpriseId, { limit: 50 }).catch(() => null),
        ])

      setSuggestions(sugsRes?.items ?? [])
      setTimeline(tlRes?.items ?? [])
      setOptimizations(optRes?.items ?? [])
      setMetrics(metricsRes)
      setMaturity(maturityRes)
      setVersions(versionsRes?.items ?? [])
      setShadowTasks(shadowRes?.items ?? [])

      // 知识缺口：按场景聚合反事实推演，语义对齐度低于阈值的场景视为缺口，
      // 按对齐度升序（最薄弱优先）取前 3 项。
      const cfSessions = cfRes?.items ?? []
      const byScenario = new Map<string, { total: number; alignmentSum: number }>()
      for (const s of cfSessions) {
        const key = s.scenario
        const prev = byScenario.get(key) ?? { total: 0, alignmentSum: 0 }
        prev.total += 1
        prev.alignmentSum += s.semantic_alignment_score
        byScenario.set(key, prev)
      }
      const GAP_ALIGNMENT_THRESHOLD = 0.85
      const gaps: KnowledgeGapItem[] = []
      for (const [scenario, agg] of byScenario) {
        const avgAlignment = agg.alignmentSum / agg.total
        if (avgAlignment < GAP_ALIGNMENT_THRESHOLD) {
          gaps.push({
            id: scenario,
            title: scenario,
            impactCount: agg.total,
            alignmentPct: avgAlignment * 100,
          })
        }
      }
      gaps.sort((a, b) => a.alignmentPct - b.alignmentPct)
      setKnowledgeGaps(gaps.slice(0, 3))

      if (workforceRes?.items) {
        const map: Record<string, string> = {}
        for (const a of workforceRes.items) {
          if (a.agent_id && a.agent_name) map[a.agent_id] = a.agent_name
        }
        setAgentNameMap(map)
        workforceItemsRef.current = workforceRes.items
      } else {
        workforceItemsRef.current = []
      }

      // diff 依赖版本列表：取最近两个版本（versions 按 compiled_at 倒序）
      const vs = versionsRes?.items ?? []
      let df: RuntimeDiff | null = null
      let range: DiffRange | null = null
      if (vs.length >= 2) {
        range = { from: vs[1].version, to: vs[0].version }
        df = await runtimeApi
          .diffRuntimeVersions(enterpriseId, range.from, range.to)
          .catch(() => null)
      }
      setDiffRange(range)
      setDiff(df)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载进化数据失败')
    } finally {
      setLoading(false)
    }
  }, [enterpriseId])

  useEffect(() => {
    loadData()
  }, [loadData])

  /** 放行：调用真实晋升接口 POST /shadow/tasks/{id}/promote（qualified → autonomous） */
  const handlePromote = useCallback(
    async (taskId: string) => {
      setPromotingTaskId(taskId)
      try {
        await shadowApi.promoteShadowTask(taskId)
        toast.success('已放行：该员工能力已晋升为正式在岗')
        await loadData()
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '放行失败，仅达标审查通过的员工可放行')
      } finally {
        setPromotingTaskId(null)
      }
    },
    [loadData],
  )

  const handleGenerate = useCallback(async () => {
    if (!enterpriseId) return
    setGenerating(true)
    try {
      const res = await evolutionApi.generateSuggestions(enterpriseId)
      toast.success(`已生成 ${res.count} 条优化提议`)
      await loadData()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '生成优化提议失败')
    } finally {
      setGenerating(false)
    }
  }, [enterpriseId, loadData])

  const handleVerdictConfirm = useCallback(async () => {
    if (!verdict) return
    const { kind, suggestion } = verdict
    setActioningId(suggestion.id)
    try {
      if (kind === 'apply') {
        await evolutionApi.applySuggestion(suggestion.id)
        setSuggestions((prev) =>
          prev.map((s) => (s.id === suggestion.id ? { ...s, status: 'applied' as const } : s)),
        )
        toast.success('提议已采纳')
      } else {
        await evolutionApi.rejectSuggestion(suggestion.id, { reason: '用户驳回' })
        setSuggestions((prev) =>
          prev.map((s) => (s.id === suggestion.id ? { ...s, status: 'rejected' as const } : s)),
        )
        toast.success('提议已驳回')
      }
      setVerdict(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '裁决失败')
    } finally {
      setActioningId(null)
    }
  }, [verdict])

  const handleRollback = useCallback(
    async (version: string) => {
      if (!enterpriseId) return
      setRollingBack(true)
      try {
        const res = await runtimeApi.rollbackRuntime(enterpriseId, { target_version: version })
        if (res.status === 'rolled_back') {
          toast.success(`已回滚至 ${version}`)
          await loadData()
        } else {
          toast.error('回滚失败')
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '回滚失败')
      } finally {
        setRollingBack(false)
      }
    },
    [enterpriseId, loadData],
  )

  const pendingCount = suggestions.filter((s) => s.status === 'pending').length
  const appliedCount = suggestions.filter((s) => s.status === 'applied').length

  const filteredSuggestions = useMemo(
    () =>
      suggestionFilter === 'all'
        ? suggestions
        : suggestions.filter((s) => s.status === suggestionFilter),
    [suggestions, suggestionFilter],
  )

  const stages = useMemo(
    () =>
      buildFlywheelStages({
        events:
          maturity?.dimensions.collaboration_events ??
          metrics?.process_efficiency?.total_events ??
          0,
        optimizations,
        suggestions,
      }),
    [maturity, metrics, optimizations, suggestions],
  )

  /** 绩效榜：真实 workforce 数据按满意度降序取 Top 3（满意度为 0-5 分制） */
  const leaderboardItems = useMemo<LeaderboardItem[]>(() => {
    const source = workforceItemsRef.current
    return [...source]
      .sort((a, b) => b.avg_satisfaction - a.avg_satisfaction)
      .slice(0, 3)
      .map((a, i) => ({
        rank: String(i + 1).padStart(2, '0'),
        name: a.agent_name,
        role: a.position || '数字员工',
        agentId: a.agent_id,
        score: a.avg_satisfaction.toFixed(1),
      }))
  }, [])

  // 无企业 ID：引导而非报错
  if (!enterpriseId) {
    return (
      <Layout>
        <div className="mx-auto w-full max-w-[1280px] px-10 py-10">
          <h1 className="text-[30px] font-bold leading-[38px] text-at-ink">组织进化</h1>
          <p className="mt-1.5 text-[13px] text-at-muted">进化飞轮、提议审计与指标对比</p>
          <div className="mt-10">
            <EmptyState
              icon={Lightbulb}
              title="未检测到企业信息"
              description="组织进化以企业为单位核算。请先登录，或联系管理员分配企业。"
              variant="warning"
            />
          </div>
        </div>
      </Layout>
    )
  }

  if (loading && suggestions.length === 0 && timeline.length === 0 && !maturity) {
    return (
      <Layout>
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className="mx-auto w-full max-w-[1280px] px-10 py-10">
        {/* 页头：30px 标题 + 一行 13px 副标题，右侧全屏唯一一枚实心主按钮 */}
        <header className="flex items-start justify-between gap-6 border-b border-at-hairline pb-8">
          <div>
            <h1 className="text-[30px] font-bold leading-[38px] tracking-tight text-at-ink">
              组织进化
            </h1>
            <p className="mt-1.5 text-[13px] text-at-muted">
              {maturity
                ? `成熟度 ${maturity.level} ${maturity.name} · 数字员工 ${Math.round(
                    maturity.dimensions.agent_count ?? 0,
                  )} 名 · 待审提议 ${pendingCount} 条`
                : '进化飞轮、提议审计与指标对比'}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-4">
            <TextLink onClick={loadData} disabled={loading}>
              <RefreshCw
                className="mr-1.5 inline-block h-3.5 w-3.5 align-[-2px]"
                aria-hidden="true"
              />
              刷新
            </TextLink>
            <button
              type="button"
              onClick={handleGenerate}
              disabled={generating}
              className="rounded-[6px] bg-at-primary px-4 py-2 text-[13px] font-medium leading-5 text-at-on-primary transition-opacity hover:opacity-95 disabled:opacity-50"
            >
              {generating ? '生成中…' : '生成优化补丁'}
            </button>
          </div>
        </header>

        {error && (
          <div className="mt-6">
            <ApiErrorState message={error} onRetry={loadData} retrying={loading} />
          </div>
        )}

        <div className="mt-10 space-y-10">
          {/* 0. 影子考核（11 屏主导区：真实影子任务四步水平轨道） */}
          <ShadowInternshipSection
            tasks={shadowTasks}
            agentNameMap={agentNameMap}
            promotingId={promotingTaskId}
            onPromote={handlePromote}
          />

          {/* 0.5. 知识缺口（真实反事实推演低对齐度场景归因） */}
          <KnowledgeGapsSection
            gaps={knowledgeGaps}
            onAction={handleGapAction}
          />

          {/* 1. 进化飞轮（主导区） */}
          <section>
            <SectionHeading
              title="进化飞轮"
              meta={`已闭环 ${stages.filter((s) => s.state === 'done').length} / ${stages.length} 段`}
            />
            <FlywheelTrack stages={stages} />
          </section>

          {/* 2. 突变提议审计 */}
          <section>
            <SectionHeading
              title="突变提议审计"
              meta={
                <FilterLinks
                  options={[
                    { key: 'pending', label: '待审', count: pendingCount },
                    { key: 'all', label: '全部', count: suggestions.length },
                  ]}
                  active={suggestionFilter}
                  onChange={(k) => setSuggestionFilter(k as 'pending' | 'all')}
                />
              }
            />
            {suggestions.length === 0 ? (
              <HairlineEmpty text="暂无优化提议，点击「生成优化补丁」让 AI 顾问扫描当前组织。" />
            ) : filteredSuggestions.length === 0 ? (
              <HairlineEmpty text="没有待审提议，全部提议均已裁决。" />
            ) : (
              <ul className="border-t border-at-hairline">
                {filteredSuggestions.map((s) => (
                  <SuggestionRow
                    key={s.id}
                    suggestion={s}
                    onVerdict={setVerdict}
                    busy={actioningId === s.id}
                  />
                ))}
              </ul>
            )}
            {appliedCount > 0 && (
              <p className="pt-3 text-xs text-at-subtle">本轮已采纳 {appliedCount} 条提议</p>
            )}
          </section>

          {/* 3. 进化历程：时间线 / 版本对比 */}
          <section>
            <SectionHeading
              title="进化历程"
              meta={
                <FilterLinks
                  options={[
                    { key: 'timeline', label: '时间线', count: timeline.length },
                    { key: 'diff', label: '版本对比', count: diff?.changes.length ?? 0 },
                  ]}
                  active={historyTab}
                  onChange={(k) => setHistoryTab(k as 'timeline' | 'diff')}
                />
              }
            />
            {historyTab === 'timeline' ? (
              timeline.length > 0 ? (
                <EvolutionTimeline items={timeline} agentNameMap={agentNameMap} />
              ) : (
                <HairlineEmpty text="暂无进化事件，完成首次编译后将从这里开始记录。" />
              )
            ) : diff && diffRange ? (
              <VersionDiffSummary diff={diff} range={diffRange} />
            ) : (
              <HairlineEmpty
                text={
                  diffRange
                    ? '暂无版本对比数据。'
                    : '需至少 2 个 Runtime 版本才能生成对比，请先在编译工坊生成新版本。'
                }
              />
            )}
          </section>

          {/* 4. 指标对比：成熟度阶梯 + 组织指标 + 组织版本与回滚 */}
          <section className="grid grid-cols-1 gap-12 border-t border-at-hairline pt-6 lg:grid-cols-2">
            <div>
              <SectionHeading
                title="成熟度"
                meta={maturity ? `当前 ${maturity.level} ${maturity.name}` : undefined}
              />
              <MaturityLadder rating={maturity} />
              {maturity && (
                <p className="pt-3 text-[13px] leading-5 text-at-muted">{maturity.description}</p>
              )}
            </div>

            <div>
              <SectionHeading title="组织指标" />
              {metrics ? (
                <ul className="border-t border-at-hairline">
                  <MetricRow
                    label="流程自动化率"
                    display={`${Math.round(
                      (metrics.process_efficiency?.automation_rate ?? 0) * 100,
                    )}%`}
                    ratio={metrics.process_efficiency?.automation_rate ?? 0}
                    hint={`已处理 ${metrics.process_efficiency?.processed_count ?? 0} / ${
                      metrics.process_efficiency?.total_events ?? 0
                    } 条事件`}
                  />
                  <MetricRow
                    label="工具安装率"
                    display={`${Math.round((metrics.tool_usage?.install_rate ?? 0) * 100)}%`}
                    ratio={metrics.tool_usage?.install_rate ?? 0}
                    hint={`已验证 ${metrics.tool_usage?.verified ?? 0} · 已安装 ${
                      metrics.tool_usage?.installed ?? 0
                    } · 共 ${metrics.tool_usage?.total_tools ?? 0} 个工具`}
                  />
                  <MetricRow
                    label="替代人工工时"
                    display={`${(metrics.business_impact?.hours_replaced ?? 0).toFixed(1)} h`}
                    ratio={Math.min((metrics.business_impact?.hours_replaced ?? 0) / 200, 1)}
                    hint={`成本节约 ¥${(metrics.business_impact?.cost_saved ?? 0).toFixed(2)}`}
                  />
                  <MetricRow
                    label="综合 ROI"
                    display={(metrics.business_impact?.roi ?? 0).toFixed(2)}
                    ratio={Math.min((metrics.business_impact?.roi ?? 0) / 5, 1)}
                    hint={
                      metrics.business_impact?.ai_cost != null
                        ? `自动化投入 ¥${metrics.business_impact.ai_cost.toFixed(2)}`
                        : undefined
                    }
                  />
                </ul>
              ) : (
                <HairlineEmpty text="暂无组织指标数据。" />
              )}
            </div>

            {/* 绩效榜：真实 workforce 满意度 Top 3（useMemo 派生，见 leaderboardItems） */}
            <LeaderboardSection items={leaderboardItems} />

            <div>
              <SectionHeading title="组织版本与回滚" meta="回滚将立即切换生效版本" />
              {versions.length === 0 ? (
                <HairlineEmpty text="尚未编译过 Runtime 版本。" />
              ) : (
                <ul className="border-t border-at-hairline">
                  {versions.slice(0, 5).map((v) => (
                    <li
                      key={v.version}
                      className="flex items-center justify-between gap-4 border-b border-at-hairline py-2.5"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <span
                          className={`font-mono text-[13px] ${
                            v.is_active ? 'font-medium text-at-ink' : 'text-at-muted'
                          }`}
                        >
                          {v.version}
                        </span>
                        {v.is_active && (
                          <span className="flex items-center gap-1.5">
                            <StatusDot tone="positive" />
                            <span className="text-[13px] text-at-ink">生效中</span>
                          </span>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-6">
                        <span className="text-[13px] text-at-muted">
                          编译于 {formatCompileTime(v.compiled_at)}
                        </span>
                        {!v.is_active && (
                          <TextLink
                            tone="blocked"
                            disabled={rollingBack}
                            onClick={() => handleRollback(v.version)}
                          >
                            <Undo2
                              className="mr-1.5 inline-block h-3.5 w-3.5 align-[-2px]"
                              aria-hidden="true"
                            />
                            回滚
                          </TextLink>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </div>
      </div>

      {verdict && (
        <VerdictDialog
          verdict={verdict}
          busy={actioningId === verdict.suggestion.id}
          onCancel={() => setVerdict(null)}
          onConfirm={handleVerdictConfirm}
        />
      )}
    </Layout>
  )
}
