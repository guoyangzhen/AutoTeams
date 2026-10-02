/**
 * WorkflowsPage —「工作流」核心工作空间（/workflows）。
 *
 * 面向中小企业经理：用最朴素的方式管理公司的 SOP 规程与自动化触发规则。
 * 按重构方案 §4.4.4，MVP 不做可视化流程编辑器，只做「规程规则列表 + 触发方式」。
 *
 * 数据全部来自真实后端 flow-core 域，无任何兜底假数据：
 * - GET  /flow-core/cards                     规程规则列表
 * - POST /flow-core/synthesize                自然语言经验 → 标准 SOP 状态机
 * - POST /flow-core/cards                     保存 / 更新规程规则（按 flow_id upsert）
 * - POST /flow-core/execute/start|step        手动触发执行（服务端持有执行态）
 * - POST /flow-core/execute/runs/{id}/approve 人工审批（非授权审批人 403）
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlarmClock,
  CheckCircle2,
  FileText,
  Hand,
  ListChecks,
  MessageSquare,
  Play,
  Plus,
  ShieldCheck,
  Sparkles,
  Square,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { PageHeader } from '@/components/ui/PageHeader'
import { SectionPanel } from '@/components/ui/SectionPanel'
import { EmptyState } from '@/components/ui/EmptyState'
import { MetricGrid, type Metric } from '@/components/ui/MetricGrid'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { InlineTabs } from '@/components/ui/InlineTabs'
import {
  listFlowCards,
  synthesizeSOP,
  saveFlowCard,
  startFlow,
  stepFlow,
  approveFlowRun,
  type FlowCard,
  type FlowExecutionRun,
  type FlowNodeType,
} from '@/api/flowCore'
import { listChannelAccounts } from '@/api/connectors'

const NODE_TYPE_LABELS: Record<FlowNodeType, string> = {
  collect_info: '信息采集',
  action_tool: '工具执行',
  branch_condition: '条件分支',
  approval_human: '人工审批',
  sub_flow: '子流程',
}

const GUARDRAIL_LABELS: Array<{ key: keyof NonNullable<FlowCard['guardrails']>; label: string }> = [
  { key: 'closed_loop_required', label: '闭环必达' },
  { key: 'adaptive_slot_filling', label: '自适应填槽' },
  { key: 'high_risk_confirmation', label: '高危二次确认' },
]

export default function WorkflowsPage() {
  const [activeTab, setActiveTab] = useState('rules')
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null)
  /** 自增信号：空态 CTA 点击后聚焦 SOP 输入框 */
  const [composerFocus, setComposerFocus] = useState(0)

  const cardsQuery = useQuery({ queryKey: ['flow-core', 'cards'], queryFn: listFlowCards })
  const channelsQuery = useQuery({
    queryKey: ['connectors', 'accounts'],
    queryFn: listChannelAccounts,
  })

  const cards = useMemo(() => cardsQuery.data ?? [], [cardsQuery.data])
  const channelAccounts = useMemo(() => channelsQuery.data ?? [], [channelsQuery.data])
  const selectedCard = useMemo(
    () => cards.find((c) => c.flow_id === selectedFlowId) ?? null,
    [cards, selectedFlowId],
  )

  const guardrailCount = cards.filter(
    (c) => c.guardrails?.high_risk_confirmation === true,
  ).length

  const metrics: Metric[] = [
    { key: 'cards', icon: FileText, value: cards.length, label: 'SOP 规程规则', tone: 'brand' },
    {
      key: 'nodes',
      icon: ListChecks,
      value: cards.reduce((sum, c) => sum + c.nodes.length, 0),
      label: '规程步骤节点',
      tone: 'info',
    },
    {
      key: 'guardrail',
      icon: ShieldCheck,
      value: guardrailCount,
      label: '启用高危确认',
      tone: guardrailCount > 0 ? 'success' : 'warning',
    },
    {
      key: 'channels',
      icon: MessageSquare,
      value: channelAccounts.length,
      label: '客户消息触发源',
      tone: channelAccounts.length > 0 ? 'success' : 'warning',
    },
  ]

  return (
    <Layout>
      <div className="mx-auto w-full max-w-[1280px] px-10 py-10">
        <PageHeader
          title="工作流"
          subtitle="把公司的 SOP 沉淀成规程规则，让数字员工按规矩干活"
        />

        <MetricGrid metrics={metrics} className="mb-8" />

        <InlineTabs
          className="mb-8"
          activeKey={activeTab}
          onChange={setActiveTab}
          tabs={[
            { key: 'rules', label: '规程规则', icon: ListChecks, badge: cards.length },
            { key: 'triggers', label: '自动化触发规则', icon: AlarmClock },
          ]}
        />

        {activeTab === 'rules' ? (
          <div className="grid grid-cols-12 gap-8">
            <div className="col-span-12 lg:col-span-7 space-y-6">
              <SOPComposer focusSignal={composerFocus} />
              <SectionPanel
                title="规程规则列表"
                description="点击规则可查看节点结构并手动执行"
                icon={<ListChecks className="w-4 h-4" />}
                bodyClassName="px-5 py-4"
              >
                {cardsQuery.isPending ? (
                  <p className="text-[12px] text-text-tertiary">加载中…</p>
                ) : cardsQuery.isError ? (
                  <p className="text-[12px] text-error">规程规则加载失败，请稍后重试</p>
                ) : cards.length === 0 ? (
                  <EmptyState
                    icon={FileText}
                    title="还没有 SOP 规程"
                    description="用一段话描述老员工是怎么做事的（例：收到客户询盘后，先查产品手册，再生成报价单，最后由主管确认），AI 会自动编译成可执行的规程规则。"
                    action={{ label: '去写第一条规程', onClick: () => setComposerFocus((n) => n + 1) }}
                  />
                ) : (
                  <ul className="space-y-2">
                    {cards.map((card) => (
                      <li key={card.flow_id}>
                        <button
                          type="button"
                          onClick={() => setSelectedFlowId(card.flow_id)}
                          className={`w-full text-left border rounded-[6px] px-4 py-3 transition-colors ${
                            selectedFlowId === card.flow_id
                              ? 'border-brand-500 bg-brand-50'
                              : 'border-border-default hover:border-border-strong'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-3">
                            <p className="text-[13px] font-medium text-text-primary truncate">
                              {card.name}
                            </p>
                            <span className="text-[11px] font-mono text-text-tertiary flex-shrink-0">
                              v{card.version} · {card.nodes.length} 步
                            </span>
                          </div>
                          {card.description && (
                            <p className="text-[12px] text-text-tertiary mt-1 line-clamp-2">
                              {card.description}
                            </p>
                          )}
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            <span className="text-[10px] px-1.5 py-0.5 rounded-[2px] bg-elevated text-text-tertiary">
                              触发：手动执行
                            </span>
                            {GUARDRAIL_LABELS.filter((g) => card.guardrails?.[g.key]).map((g) => (
                              <span
                                key={g.key}
                                className="text-[10px] px-1.5 py-0.5 rounded-[2px] bg-success/10 text-success"
                              >
                                {g.label}
                              </span>
                            ))}
                          </div>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </SectionPanel>
            </div>

            <div className="col-span-12 lg:col-span-5 space-y-6">
              <RunPanel card={selectedCard} totalRules={cards.length} />
            </div>
          </div>
        ) : (
          <TriggerRulesPanel
            rules={cards}
            channelCount={channelAccounts.length}
            onGoToRules={() => setActiveTab('rules')}
            onRunRule={(flowId) => {
              setSelectedFlowId(flowId)
              setActiveTab('rules')
            }}
          />
        )}
      </div>
    </Layout>
  )
}

// ============================================================
// 自然语言 → SOP 规程
// ============================================================

function SOPComposer({ focusSignal }: { focusSignal: number }) {
  const queryClient = useQueryClient()
  const [textSop, setTextSop] = useState('')
  const [draft, setDraft] = useState<FlowCard | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (focusSignal > 0) textareaRef.current?.focus()
  }, [focusSignal])

  const synthesize = useMutation({
    mutationFn: () => synthesizeSOP(textSop.trim()),
    onSuccess: (card) => {
      setDraft(card)
      toast.success('已编译为 SOP 规程规则，请确认后保存')
    },
    onError: () => toast.error('编译失败，请补充更完整的业务描述'),
  })

  const persist = useMutation({
    mutationFn: (card: FlowCard) => saveFlowCard(card),
    onSuccess: () => {
      toast.success('规程规则已保存，现在可以手动执行')
      setDraft(null)
      setTextSop('')
      void queryClient.invalidateQueries({ queryKey: ['flow-core', 'cards'] })
    },
    onError: () => toast.error('保存失败，请稍后重试'),
  })

  return (
    <SectionPanel
      title="从经验生成规程"
      description="把老员工的做法用一段话写下来，AI 编译成可执行的状态机"
      icon={<Sparkles className="w-4 h-4" />}
      bodyClassName="px-5 py-4 space-y-3"
    >
      <textarea
        ref={textareaRef}
        value={textSop}
        onChange={(e) => setTextSop(e.target.value)}
        rows={4}
        aria-label="SOP 经验描述"
        placeholder="例：收到客户询盘邮件后，先在产品手册里查对应型号与价格，生成报价单，超过 5 万的报价需要主管审批，通过后回复客户并记录跟进时间。"
        className="w-full px-3 py-2 bg-elevated text-text-primary border border-border-default rounded-lg text-body placeholder:text-text-tertiary focus:outline-none focus:border-brand-500"
      />
      <span className="text-[11px] text-text-tertiary">
        {synthesize.isPending
          ? 'AI 正在把这段经验拆解成状态机节点，请稍候…'
          : textSop.trim().length === 0
            ? '至少写 10 个字，越具体（触发条件 / 判断依据 / 审批人 / 产出物）编译结果越准确。'
            : `已写 ${textSop.trim().length} 字，描述越具体，编译出的节点与护栏越贴合实际。`}
      </span>
      <Button
        onClick={() => synthesize.mutate()}
        disabled={textSop.trim().length < 10 || synthesize.isPending}
      >
        <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
        {synthesize.isPending ? '编译中…' : 'AI 编译为规程'}
      </Button>

      {draft && (
        <div className="border border-brand-500/30 bg-brand-50 rounded-[6px] p-4 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-[13px] font-medium text-text-primary">
              {draft.name}
              <span className="ml-2 text-[11px] font-normal text-text-tertiary">
                {draft.nodes.length} 个节点
              </span>
            </p>
            <Button
              size="sm"
              disabled={persist.isPending}
              onClick={() => persist.mutate(draft)}
            >
              <Plus className="w-3.5 h-3.5" aria-hidden="true" />
              保存为规程规则
            </Button>
          </div>
          <ol className="space-y-1.5">
            {draft.nodes.map((node, index) => (
              <li key={node.node_id} className="text-[12px] text-text-secondary flex gap-2">
                <span className="font-mono text-text-tertiary flex-shrink-0">{index + 1}.</span>
                <span>
                  <span className="px-1.5 py-0.5 rounded-[2px] bg-elevated text-[10px] text-text-tertiary mr-1.5">
                    {NODE_TYPE_LABELS[node.node_type] ?? node.node_type}
                  </span>
                  {node.name}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </SectionPanel>
  )
}

// ============================================================
// 手动执行面板
// ============================================================

function RunPanel({ card, totalRules }: { card: FlowCard | null; totalRules: number }) {
  // AUD-15：执行态由服务端持有，客户端只保存 run_id 对应的最新快照。
  const [run, setRun] = useState<FlowExecutionRun | null>(null)
  const [userInput, setUserInput] = useState('')
  const [pending, setPending] = useState(false)

  const activeFlowId = run?.flow_id ?? card?.flow_id ?? ''

  const call = async <T,>(fn: () => Promise<T>): Promise<T | null> => {
    setPending(true)
    try {
      return await fn()
    } catch (err) {
      toast.error(`规程推进失败：${(err as Error).message || '请重试'}`)
      return null
    } finally {
      setPending(false)
    }
  }

  const start = async () => {
    const next = await call(() => startFlow(activeFlowId))
    if (next) {
      setRun(next)
      setUserInput('')
      toast.success('规程已启动')
    }
  }

  const advance = async (options?: { userInput?: string }) => {
    if (!run) return
    const next = await call(() =>
      stepFlow(run.run_id, { userInput: options?.userInput, expectedVersion: run.version }),
    )
    if (next) setRun(next)
  }

  // 审批走独立端点：非授权审批人 403 且不落库、不推进；版本过期 409。
  const decide = async (decision: 'granted' | 'rejected') => {
    if (!run) return
    const nodeId = run.state?.pending_approval_node_id ?? run.current_node_id
    const next = await call(() =>
      approveFlowRun(run.run_id, nodeId, decision, { expectedVersion: run.version }),
    )
    if (next) setRun(next)
  }

  if (!card) {
    return (
      <SectionPanel title="规则执行" icon={<Play className="w-4 h-4" />} bodyClassName="px-5 py-4">
        <EmptyState
          icon={Hand}
          title={totalRules === 0 ? '还没有可执行的规程' : '选择一条规程规则'}
          description={
            totalRules === 0
              ? '先用上方「从经验生成规程」沉淀第一条 SOP，保存后即可在此逐步执行。'
              : '在左侧列表点选规程，即可查看节点结构并按步骤手动执行。'
          }
        />
      </SectionPanel>
    )
  }

  const currentNode = card.nodes.find((n) => n.node_id === run?.current_node_id)
  const trace = run?.state?.history_trace ?? []

  return (
    <SectionPanel
      title="规则执行"
      description={card.name}
      icon={<Play className="w-4 h-4" />}
      bodyClassName="px-5 py-4 space-y-4"
    >
      <ol className="space-y-1.5">
        {card.nodes.map((node, index) => {
          const isCurrent = run?.current_node_id === node.node_id
          return (
            <li
              key={node.node_id}
              className={`text-[12px] flex gap-2 px-2 py-1.5 rounded-[4px] ${
                isCurrent ? 'bg-brand-50 text-text-primary font-medium' : 'text-text-tertiary'
              }`}
            >
              <span className="font-mono flex-shrink-0">{index + 1}.</span>
              <span>
                <span className="px-1.5 py-0.5 rounded-[2px] bg-elevated text-[10px] mr-1.5">
                  {NODE_TYPE_LABELS[node.node_type] ?? node.node_type}
                </span>
                {node.name}
                {node.instruction && (
                  <span className="block text-[11px] text-text-tertiary mt-0.5">
                    {node.instruction}
                  </span>
                )}
              </span>
            </li>
          )
        })}
      </ol>

      {!run ? (
        <Button onClick={() => void start()} disabled={pending}>
          <Play className="w-3.5 h-3.5" aria-hidden="true" />
          立即执行这条规程
        </Button>
      ) : (
        <div className="space-y-3 border-t border-border-default pt-3">
          <div className="flex items-center gap-2 text-[12px]">
            <span className="text-text-tertiary">当前节点</span>
            <span className="font-medium text-text-primary">
              {currentNode?.name ?? run.current_node_id}
            </span>
            <span className="font-mono text-[11px] text-text-tertiary">
              RUN {run.run_id.slice(0, 8)} · v{run.version} · {run.step_count} 步
            </span>
            <span
              className={`ml-auto px-2 py-0.5 rounded-[2px] text-[11px] ${
                run.status === 'completed'
                  ? 'bg-success/10 text-success'
                  : run.status === 'failed'
                    ? 'bg-error/10 text-error'
                    : 'bg-elevated text-text-tertiary'
              }`}
            >
              {run.status}
            </span>
          </div>

          {run.last_output && (
            <p className="text-[12px] text-text-secondary bg-elevated p-3 rounded-[4px] leading-relaxed">
              {run.last_output}
            </p>
          )}

          {run.status === 'waiting_user_input' && (
            <div className="space-y-2">
              <Input
                label="补充信息"
                value={userInput}
                onChange={(e) => setUserInput(e.target.value)}
                hint="规程执行到该节点正在等待你的输入"
              />
              <Button
                disabled={pending || userInput.trim().length === 0}
                onClick={() => void advance({ userInput: userInput.trim() })}
              >
                提交并继续
              </Button>
            </div>
          )}

          {run.status === 'waiting_approval' && (
            <div className="space-y-2">
              <p className="text-[12px] text-text-secondary">
                该步骤属于高危操作，需人工核准后才会继续执行。
              </p>
              <div className="flex gap-2">
                <Button variant="secondary" disabled={pending} onClick={() => void decide('rejected')}>
                  驳回
                </Button>
                <Button disabled={pending} onClick={() => void decide('granted')}>
                  <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />
                  批准并继续
                </Button>
              </div>
            </div>
          )}

          {run.status === 'running' && (
            <Button disabled={pending} onClick={() => void advance()}>
              推进下一步
            </Button>
          )}

          {(run.status === 'completed' || run.status === 'failed') && (
            <div className="space-y-2">
              {run.error_message && (
                <p className="text-[12px] text-error">{run.error_message}</p>
              )}
              <Button variant="secondary" onClick={() => setRun(null)}>
                <Square className="w-3.5 h-3.5" aria-hidden="true" />
                重新执行
              </Button>
            </div>
          )}

          {trace.length > 0 && (
            <details className="text-[12px] text-text-tertiary">
              <summary className="cursor-pointer">执行轨迹（{trace.length} 步）</summary>
              <ul className="mt-2 space-y-1">
                {trace.map((t, i) => (
                  <li key={i} className="font-mono">
                    {t.from_node ?? '—'} → {t.to_node} {t.action ? `· ${t.action}` : ''}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </SectionPanel>
  )
}

// ============================================================
// 自动化触发规则
// ============================================================

interface TriggerDefinition {
  key: string
  label: string
  description: string
  icon: typeof AlarmClock
  /** 上线状态由真实后端能力决定，未上线的通道显式禁用，绝不假装可用 */
  available: boolean
  availabilityNote: string
}

function TriggerRulesPanel({
  rules,
  channelCount,
  onGoToRules,
  onRunRule,
}: {
  rules: FlowCard[]
  channelCount: number
  onGoToRules: () => void
  onRunRule: (flowId: string) => void
}) {
  const triggers: TriggerDefinition[] = [
    {
      key: 'manual',
      label: '手动触发',
      description: '由经理在工作流页点击执行，规程按节点逐步推进，遇高危节点等待人工核准。',
      icon: Hand,
      available: true,
      availabilityNote: '已上线：规则下方可直接执行。',
    },
    {
      key: 'customer-message',
      label: '收到客户消息',
      description: '企微 / 飞书收到客户消息时，按规程自动分派给对应数字员工处理。',
      icon: MessageSquare,
      available: channelCount > 0,
      availabilityNote:
        channelCount > 0
          ? `已接入 ${channelCount} 个渠道账号，消息由渠道侧接入后触发。`
          : '尚无渠道账号：先在「全渠道网关」完成企微 / 飞书接入。',
    },
    {
      key: 'schedule',
      label: '定时触发',
      description: '每天固定时间自动执行日报生成、数据巡检等周期性规程。',
      icon: AlarmClock,
      available: false,
      availabilityNote: '调度服务尚未上线，暂不可用；当前请用手动触发执行周期性规程。',
    },
  ]

  return (
    <div className="grid grid-cols-12 gap-8">
      <div className="col-span-12 lg:col-span-5 space-y-3">
        {triggers.map((t) => (
          <div
            key={t.key}
            className={`border rounded-[6px] p-4 ${
              t.available ? 'border-border-default bg-surface' : 'border-border-default bg-elevated'
            }`}
          >
            <div className="flex items-start gap-3">
              <t.icon className="w-4 h-4 mt-0.5 text-brand-500 flex-shrink-0" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[13px] font-medium text-text-primary">{t.label}</p>
                  <span
                    className={`text-[11px] px-2 py-0.5 rounded-[2px] flex-shrink-0 ${
                      t.available ? 'bg-success/10 text-success' : 'bg-elevated text-text-tertiary'
                    }`}
                  >
                    {t.available ? '可用' : '未上线'}
                  </span>
                </div>
                <p className="text-[12px] text-text-tertiary mt-1 leading-relaxed">
                  {t.description}
                </p>
                <p className="text-[11px] text-text-tertiary mt-1">{t.availabilityNote}</p>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="col-span-12 lg:col-span-7">
        <SectionPanel
          title="规则清单"
          description="每条 SOP 规程即一条可触发的规则"
          icon={<ListChecks className="w-4 h-4" />}
          bodyClassName="px-5 py-4"
        >
          {rules.length === 0 ? (
            <EmptyState
              icon={ListChecks}
              title="还没有可触发的规则"
              description="自动化触发规则的最小单位就是一条 SOP 规程。先去「规程规则」把公司的做事流程沉淀下来，规则才能被触发执行。"
              action={{ label: '去创建规程', onClick: onGoToRules }}
            />
          ) : (
            <ul className="space-y-2">
              {rules.map((rule) => (
                <li
                  key={rule.flow_id}
                  className="flex items-center justify-between gap-3 border border-border-default rounded-[6px] px-4 py-3"
                >
                  <div className="min-w-0">
                    <p className="text-[13px] font-medium text-text-primary truncate">{rule.name}</p>
                    <p className="text-[11px] text-text-tertiary truncate">
                      触发方式：手动执行 · {rule.nodes.length} 个步骤
                    </p>
                  </div>
                  <Button variant="secondary" size="sm" onClick={() => onRunRule(rule.flow_id)}>
                    <Play className="w-3.5 h-3.5" aria-hidden="true" />
                    立即触发
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </SectionPanel>
      </div>
    </div>
  )
}
