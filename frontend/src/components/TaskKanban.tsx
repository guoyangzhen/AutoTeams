/**
 * TaskKanban — 任务流看板（未开始 / 进行中 / 已完成 / 待审批 纵列）。
 *
 * 承接 WT-Company #3：将原本的「任务流事件列表」重构为看板。
 * - 每张卡片 = 一条协作任务（来自 collaboration events），纵列按任务阶段划分。
 * - 协作关系融合进看板：卡片上展示 source→target 的协作边（谁交给谁）。
 * - 待审批纵列直接渲染 ApprovalPanel（完整模式，#4 已优化按钮对比度）。
 *
 * 数据源：collaboration events + pending approvals（均为真实后端数据，绝不造假）。
 */
import { useMemo } from 'react'
import {
  Circle, Loader2 as LoaderSpin, CheckCircle2, Bell,
  ArrowRight, type LucideIcon,
} from 'lucide-react'
import { ApprovalPanel } from '@/components/ApprovalPanel'
import type { CollaborationEvent, CollaborationEventType, ApprovalRequest, AgentRunMetrics } from '@/types'
import { formatPayloadLabel } from '@/utils/fieldMappings'

type KanbanColumnKey = 'todo' | 'in_progress' | 'done' | 'approval'

interface KanbanColumnMeta {
  key: KanbanColumnKey
  title: string
  icon: LucideIcon
  accent: string
  dot: string
}

const COLUMNS: KanbanColumnMeta[] = [
  { key: 'todo', title: '未开始', icon: Circle, accent: 'text-text-tertiary', dot: 'bg-text-muted' },
  { key: 'in_progress', title: '进行中', icon: LoaderSpin, accent: 'text-info', dot: 'bg-info' },
  { key: 'done', title: '已完成', icon: CheckCircle2, accent: 'text-success', dot: 'bg-success' },
  { key: 'approval', title: '待审批', icon: Bell, accent: 'text-warning', dot: 'bg-warning' },
]

// 事件类型 → 看板纵列（待审批事件由 approvals 数组承载，Event 卡片跳过）
const EVENT_COLUMN: Partial<Record<CollaborationEventType, KanbanColumnKey>> = {
  inquiry_received: 'todo',
  product_query: 'in_progress',
  quotation_generated: 'in_progress',
  approval_submitted: 'approval',
  approval_flow_created: 'approval',
  approval_approved: 'done',
  order_synced: 'done',
  after_sales: 'done',
  handoff: 'in_progress',
  escalation: 'in_progress',
  error: 'todo',
  opportunity_created: 'in_progress',
  deal_closed: 'done',
}

interface KanbanTask {
  id: string
  title: string
  subtitle: string
  column: KanbanColumnKey
  fromAgent?: string
  toAgent?: string
  time?: string
  failed?: boolean
}

interface TaskKanbanProps {
  events: CollaborationEvent[]
  approvals: ApprovalRequest[]
  typeLabels: Record<CollaborationEventType, string>
  agentNames: Record<string, string>
  /** 正在处理中的审批 id（仅该项转圈，其余不受影响） */
  processingApprovalId?: string | null
  onApprove?: (id: string) => void
  onReject?: (id: string, reason: string) => void
  /** 用于占位展示的 Agent 列表（空态引导时提示去生成员工） */
  agents?: AgentRunMetrics[]
  onTriggerDemo?: () => void
}

function formatTime(iso?: string): string | undefined {
  if (!iso) return undefined
  const d = new Date(iso)
  if (isNaN(d.getTime())) return undefined
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

/** 从 payload 中提取一条简短的可读描述（隐藏 UUID） */
function formatPayload(payload: Record<string, unknown>): string {
  const parts: string[] = []
  if (payload.amount != null) parts.push(`金额 ¥${Number(payload.amount).toLocaleString('zh-CN')}`)
  if (payload.product != null) {
    const p = formatPayloadLabel(payload.product)
    if (p) parts.push(`产品 ${p}`)
  }
  if (payload.customer_id != null) {
    const c = formatPayloadLabel(payload.customer_id)
    if (c) parts.push(`客户 ${c}`)
  }
  if (parts.length > 0) return parts.join(' · ')
  return ''
}

export function TaskKanban({
  events,
  approvals,
  typeLabels,
  agentNames,
  processingApprovalId = null,
  onApprove,
  onReject,
  agents = [],
  onTriggerDemo,
}: TaskKanbanProps) {
  // 事件 → 卡片（跳过待审批事件，由 approvals 承载）
  const eventCards = useMemo<KanbanTask[]>(() => {
    return events
      .map((e): KanbanTask | null => {
        const col = EVENT_COLUMN[e.event_type]
        if (!col || col === 'approval') return null
        return {
          id: e.event_id,
          title: typeLabels[e.event_type] ?? e.event_type,
          subtitle: formatPayload(e.payload),
          column: col as KanbanColumnKey,
          fromAgent: e.source_agent_id ? agentNames[e.source_agent_id] : undefined,
          toAgent: e.target_agent_id ? agentNames[e.target_agent_id] : undefined,
          time: formatTime(e.created_at),
          failed: e.status === 'failed',
        }
      })
      .filter((t): t is KanbanTask => t !== null)
  }, [events, typeLabels, agentNames])

  const hasData = eventCards.length > 0 || approvals.length > 0

  return (
    <div>
      {!hasData ? (
        <div className="py-10 flex flex-col items-center justify-center text-center">
          <div className="w-12 h-12 rounded-full bg-elevated flex items-center justify-center text-text-muted mb-3">
            <Bell className="w-6 h-6" aria-hidden="true" />
          </div>
          <p className="text-sm text-text-secondary">暂无任务数据</p>
          <p className="text-xs text-text-tertiary mt-1">编译企业运行时并运行协作流程后，将在此以看板形式展示任务流转。</p>
          {agents.length > 0 && onTriggerDemo && (
            <button
              type="button"
              onClick={onTriggerDemo}
              className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-brand-500 text-white hover:bg-brand-600 transition-colors"
            >
              运行协作流程
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {COLUMNS.map((col) => {
            const Icon = col.icon
            // 待审批纵列 = approvals 数组；其余纵列 = 事件卡片
            const taskCards = col.key === 'approval' ? [] : eventCards.filter((t) => t.column === col.key)
            const approvalCards = col.key === 'approval' ? approvals : []
            return (
              <div
                key={col.key}
                className="rounded-lg border border-border-default bg-surface p-3 flex flex-col min-h-[120px]"
              >
                {/* 纵列标题 */}
                <div className="flex items-center gap-2 mb-3">
                  <Icon className={`w-4 h-4 ${col.accent}`} aria-hidden="true" />
                  <span className="text-sm font-semibold text-text-primary">{col.title}</span>
                  <span className="ml-auto text-xs text-text-tertiary">
                    {col.key === 'approval' ? approvalCards.length : taskCards.length}
                  </span>
                </div>

                {/* 卡片区 */}
                <div className="space-y-2 flex-1">
                  {col.key === 'approval' ? (
                    approvalCards.length === 0 ? (
                      <p className="text-xs text-text-tertiary text-center py-4">暂无待审批</p>
                    ) : (
                      approvalCards.map((appr) => (
                        <div
                          key={appr.id}
                          className="rounded-md border border-warning/30 bg-warning/5 p-2.5"
                        >
                          <div className="flex items-center justify-between gap-1 mb-1">
                            <span className="text-xs font-medium text-text-primary truncate">{appr.title}</span>
                            {appr.amount != null && (
                              <span className="text-xs font-bold text-warning">¥{appr.amount.toLocaleString()}</span>
                            )}
                          </div>
                          <ApprovalPanel
                            title={appr.title}
                            description={appr.description}
                            amount={appr.amount}
                            // 申请人：后端已返回中文名；若仍是 UUID（如未匹配到 Agent），
                            // 用 agentNames 映射兜底，避免把原始 ID 暴露给用户
                            requesterName={agentNames[appr.requester_name] || appr.requester_name}
                            typeLabel="待审批"
                            processing={processingApprovalId === appr.id}
                            onApprove={() => onApprove?.(appr.id)}
                            onReject={(reason) => onReject?.(appr.id, reason)}
                          />
                        </div>
                      ))
                    )
                  ) : taskCards.length === 0 ? (
                    <p className="text-xs text-text-tertiary text-center py-4">暂无任务</p>
                  ) : (
                    taskCards.map((task) => (
                      <div
                        key={task.id}
                        className={`rounded-md border p-2.5 ${
                          task.failed ? 'border-error/30 bg-error/5' : 'border-border-default bg-elevated/40'
                        }`}
                      >
                        <div className="flex items-center gap-1.5 mb-1">
                          <span className={`w-1.5 h-1.5 rounded-full ${col.dot} flex-shrink-0`} />
                          <span className="text-xs font-medium text-text-primary truncate">{task.title}</span>
                          {task.failed && (
                            <span className="text-[10px] text-error flex-shrink-0">· 失败</span>
                          )}
                        </div>
                        {task.subtitle && (
                          <p className="text-xs text-text-secondary mt-0.5 line-clamp-2">{task.subtitle}</p>
                        )}
                        {/* 协作关系融合进看板：source → target */}
                        {(task.fromAgent || task.toAgent) && (
                          <div className="flex items-center gap-1 mt-1.5 text-[11px] text-text-tertiary">
                            <span className="truncate max-w-[40%]">{task.fromAgent ?? '系统'}</span>
                            <ArrowRight className="w-3 h-3 flex-shrink-0" aria-hidden="true" />
                            <span className="truncate max-w-[40%]">{task.toAgent ?? '下一环节'}</span>
                          </div>
                        )}
                        {task.time && (
                          <div className="text-[10px] text-text-muted mt-1">{task.time}</div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default TaskKanban