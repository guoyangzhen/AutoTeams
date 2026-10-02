/**
 * RuntimeVisualizer — Enterprise Runtime 可视化组件。
 *
 * 展示 Runtime 四大组成（PRD §4.6）：
 * - 企业运转模型（operating）：自绘麦肯锡式动态组件结构图
 * - 组织运行时（organization）：部门层级树（mermaid）
 * - Agent 配置模板（agents）：Agent 列表卡片
 * - 流程引擎实例（process_engines）：流程步骤可视化
 * - 协作关系图（collaboration_graph）：mermaid 协作关系图
 */
import { useMemo } from 'react'
import { Building2, Users, GitBranch, Share2, Boxes } from 'lucide-react'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { MermaidDiagram } from '@/components/MermaidDiagram'
import { EnterpriseModelDiagram } from '@/components/EnterpriseModelDiagram'
import { buildOrgChart, buildCollaborationChart } from '@/utils/mermaidCharts'
import { processTypeToLabel, agentStatusToLabel } from '@/utils/fieldMappings'
import type {
  RuntimeCompileResult,
  AgentConfigTemplate,
  ProcessEngineInstance,
  DepartmentInstance,
} from '@/types'

export type RuntimeVisualizerView =
  | 'operating'
  | 'graph'
  | 'organization'
  | 'agents'
  | 'processes'

interface RuntimeVisualizerProps {
  runtime: RuntimeCompileResult
  /** 可视化视图标签 */
  view?: RuntimeVisualizerView
  onViewChange?: (view: RuntimeVisualizerView) => void
}

const VIEWS: { key: RuntimeVisualizerView; label: string; icon: typeof Users }[] = [
  { key: 'operating', label: '企业运转模型', icon: Boxes },
  { key: 'graph', label: '协作关系图', icon: Share2 },
  { key: 'organization', label: '组织架构', icon: Building2 },
  { key: 'agents', label: 'Agent 状态', icon: Users },
  { key: 'processes', label: '流程引擎', icon: GitBranch },
]

/** 协作关系图（mermaid，按部门聚簇、可缩放、默认清晰） */
function CollaborationGraphView({ runtime }: { runtime: RuntimeCompileResult }) {
  const chart = useMemo(() => buildCollaborationChart(runtime.collaboration_graph), [runtime])
  return (
    <MermaidDiagram
      chart={chart}
      height={620}
      className="bg-elevated/20"
    />
  )
}

/** 组织架构视图（mermaid 树状，随 departments/agents 自动更新、可缩放）
 *  命名导出，供 OrganizationChart 独立页面复用，保证两处为同一组件、数据同步。 */
export function OrganizationView({
  departments,
  agents,
}: {
  departments: DepartmentInstance[]
  agents?: AgentConfigTemplate[]
}) {
  const chart = useMemo(() => buildOrgChart(departments, agents), [departments, agents])
  if (!departments || departments.length === 0) {
    return (
      <div className="text-center py-12 text-text-tertiary text-sm">
        暂无组织架构数据，请先完成企业编译
      </div>
    )
  }
  return <MermaidDiagram chart={chart} height={620} />
}

/** 占位/非岗位实体的 Agent 名（多来自知识图谱中的抽象节点，非真实数字员工岗位） */
const NON_AGENT_NAMES = new Set([
  '法定代表人', '直属上级', '接收人', '离职员工', '部门经理', '部门总监',
  '主管', '工程师', '财务', '销售', '经理', '总监', 'CEO / 总经理',
])

/** AI 员工生命周期阶段（Recruit → Training → Certification → Shadow → Production）
 *  用于 Agent 状态视图的生命周期流水线演示，直观体现 AI 员工生命周期管理。 */
const LIFECYCLE_STAGES: { key: string; label: string; desc: string; bar: string; dot: string }[] = [
  { key: 'recruit', label: '招聘', desc: '入职', bar: 'bg-brand-400', dot: 'bg-brand-500' },
  { key: 'training', label: '培训', desc: '学习企业知识', bar: 'bg-sky-400', dot: 'bg-sky-500' },
  { key: 'certification', label: '认证', desc: '能力考核', bar: 'bg-amber-400', dot: 'bg-amber-500' },
  { key: 'shadow', label: '影子', desc: '真人伴飞', bar: 'bg-violet-400', dot: 'bg-violet-500' },
  { key: 'production', label: '投产', desc: '正式上岗', bar: 'bg-emerald-400', dot: 'bg-emerald-500' },
]

/** 由 Agent 的编译状态推导其在生命周期流水线中的当前阶段下标。
 *  状态 → 阶段映射：draft=招聘、training=培训、shadow=影子、active=投产，
 *  其余（含未分类）归入招聘阶段。 */
function statusToStageIndex(status: string | undefined | null): number {
  switch (status) {
    case 'active':
      return 4
    case 'shadow':
      return 3
    case 'training':
      return 1
    case 'draft':
    default:
      return 0
  }
}

/** 生成单个 Agent 的生命周期事件记录（用于生命周期演示时间线）。
 *  以当前阶段为终点，按阶段顺序生成「已完成/进行中」事件，事件内容贴合阶段语义。 */
function buildLifecycleEvents(agent: AgentConfigTemplate): { stage: string; stageIdx: number; title: string; done: boolean }[] {
  const idx = statusToStageIndex(agent.status)
  return LIFECYCLE_STAGES.map((s, i) => ({
    stage: s.label,
    stageIdx: i,
    title:
      i < idx
        ? `${agent.agent_name} 已完成「${s.label}」阶段（${s.desc}）`
        : i === idx
          ? `${agent.agent_name} 正在进行「${s.label}」阶段（${s.desc}）`
          : `${agent.agent_name} 待进入「${s.label}」阶段`,
    done: i <= idx,
  }))
}

/** Agent 状态视图：按「岗位名 + 部门」归类合并去重，过滤占位岗位，
 *  按部门分组展示整个 AI 公司内各岗位 Agent 的实时状态。 */
function AgentsView({ agents }: { agents: AgentConfigTemplate[] }) {
  // 1) 过滤占位岗位
  const real = (agents ?? []).filter((a) => a.agent_name && !NON_AGENT_NAMES.has(a.agent_name))

  // 2) 按「岗位名 + 部门」归类并去重（去重后保留首位作为代表实例）
  const grouped = useMemo(() => {
    const map = new Map<string, AgentConfigTemplate & { count: number }>()
    for (const a of real) {
      const key = `${a.agent_name}@@${a.department}`
      const existing = map.get(key)
      if (existing) {
        existing.count += 1
      } else {
        map.set(key, { ...a, count: 1 })
      }
    }
    // 按部门分组
    const byDept = new Map<string, (AgentConfigTemplate & { count: number })[]>()
    for (const item of map.values()) {
      const dept = item.department || '未分组'
      const arr = byDept.get(dept) ?? []
      arr.push(item)
      byDept.set(dept, arr)
    }
    return Array.from(byDept.entries())
  }, [real])

  // 3) 顶部状态汇总
  const statusCount: Record<string, number> = {}
  for (const a of real) {
    const label = agentStatusToLabel(a.status)
    statusCount[label] = (statusCount[label] ?? 0) + 1
  }

  // 4) 生命周期流水线：统计各阶段的人数（由真实 agent 状态推导，非凭空捏造）
  const stageCounts = LIFECYCLE_STAGES.map((s, i) => ({
    ...s,
    count: real.filter((a) => statusToStageIndex(a.status) === i).length,
  }))
  const pipelineActive = stageCounts.reduce((n, s) => n + s.count, 0)

  // 5) 生命周期事件时间线（去重后的代表岗位，每条展示该岗位从招聘到当前阶段的事件）
  const lifecycleEvents = useMemo(() => {
    const reps: AgentConfigTemplate[] = []
    const seen = new Set<string>()
    for (const a of real) {
      const k = `${a.agent_name}@@${a.department}`
      if (seen.has(k)) continue
      seen.add(k)
      reps.push(a)
    }
    return reps.slice(0, 8).map((a) => ({ agent: a, events: buildLifecycleEvents(a) }))
  }, [real])

  if (real.length === 0) {
    return (
      <div className="text-center py-12 text-text-tertiary text-sm">
        暂无 Agent 状态数据，请先完成企业编译
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* 状态汇总 */}
      <div className="flex flex-wrap gap-2">
        {Object.entries(statusCount).map(([label, count]) => (
          <span
            key={label}
            className="inline-flex items-center gap-1.5 rounded-md bg-elevated border border-border-default px-3 py-1.5 text-xs text-text-secondary"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-brand-500" aria-hidden="true" />
            {label}
            <span className="font-medium text-text-primary">{count}</span>
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5 rounded-md bg-elevated border border-border-default px-3 py-1.5 text-xs text-text-secondary">
          <Users className="w-3.5 h-3.5" aria-hidden="true" />
          去重后 {grouped.reduce((n, [, items]) => n + items.length, 0)} 个岗位
        </span>
      </div>

      {/* 生命周期流水线（Recruit → Training → Certification → Shadow → Production） */}
      <div className="rounded-lg border border-border-default p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="font-medium text-text-primary">AI 员工生命周期流水线</span>
          <span className="text-xs text-text-muted">
            <Users className="w-3.5 h-3.5 inline -mt-0.5 mr-1" aria-hidden="true" />
            {pipelineActive} 个岗位在册
          </span>
        </div>
        <div className="flex flex-col sm:flex-row items-stretch gap-1.5">
          {stageCounts.map((s, i) => (
            <div key={s.key} className="flex-1 min-w-0">
              <div
                className={`relative rounded-md border px-3 py-2.5 h-full ${
                  s.count > 0
                    ? 'border-border-strong bg-elevated'
                    : 'border-border-subtle bg-surface/40'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${s.dot}`} aria-hidden="true" />
                  <span className="text-sm font-medium text-text-primary truncate">{s.label}</span>
                  <span className="ml-auto text-xs font-semibold text-text-secondary tabular-nums">{s.count}</span>
                </div>
                <div className="text-[11px] text-text-tertiary mt-0.5 truncate">{s.desc}</div>
                {/* 阶段进度条：以在册数为基准示意 */}
                <div className="mt-2 h-1.5 rounded-full bg-elevated overflow-hidden">
                  <div
                    className={`h-full rounded-full ${s.bar} transition-all`}
                    style={{ width: pipelineActive > 0 ? `${Math.max(8, (s.count / pipelineActive) * 100)}%` : '0%' }}
                  />
                </div>
              </div>
              {i < stageCounts.length - 1 && (
                <div className="hidden sm:flex justify-center -my-1 relative z-10">
                  <span className="text-text-tertiary">›</span>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* 近期生命周期事件（时间线） */}
      {lifecycleEvents.length > 0 && (
        <div className="rounded-lg border border-border-default p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="font-medium text-text-primary">近期生命周期事件</span>
            <span className="text-xs text-text-muted">按岗位展示从招聘到当前阶段</span>
          </div>
          <div className="space-y-4">
            {lifecycleEvents.map(({ agent, events }) => {
              const cur = events.find((e) => !e.done) || events[events.length - 1]
              const curIdx = statusToStageIndex(agent.status)
              return (
                <div key={`${agent.agent_name}@@${agent.department}`}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm font-medium text-text-primary truncate">{agent.agent_name}</span>
                    <span className="text-[11px] text-text-tertiary truncate">{agent.department}</span>
                  </div>
                  <div className="flex items-center gap-1 overflow-x-auto pb-1">
                    {events.map((e, i) => {
                      const isCur = i === curIdx
                      return (
                        <div key={i} className="flex items-center flex-shrink-0">
                          <div
                            className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs ${
                              e.done
                                ? isCur
                                  ? 'border-brand-300 bg-brand-50 text-brand-600'
                                  : 'border-border-strong bg-elevated text-text-primary'
                                : 'border-border-subtle bg-surface/40 text-text-tertiary'
                            }`}
                          >
                            <span className={`w-1.5 h-1.5 rounded-full ${e.done ? LIFECYCLE_STAGES[i].dot : 'bg-border-default'}`} aria-hidden="true" />
                            {e.stage}
                          </div>
                          {i < events.length - 1 && (
                            <span className={`mx-1 text-text-tertiary ${e.done ? 'text-text-muted' : ''}`}>—</span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                  <p className="text-xs text-text-tertiary mt-1">{cur?.title}</p>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* 按部门分组的岗位卡 */}
      <div className="grid gap-3 sm:grid-cols-2">
        {grouped.map(([dept, items]) => (
          <div key={dept} className="rounded-lg border border-border-default p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="font-medium text-text-primary">{dept}</span>
              <span className="text-xs text-text-muted">{items.length} 个岗位</span>
            </div>
            <div className="space-y-2">
              {items.map((agent) => (
                <div
                  key={agent.agent_id}
                  className="flex items-center justify-between gap-2 rounded-md bg-elevated/50 border border-border-subtle px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="text-sm text-text-primary truncate">
                      {agent.agent_name}
                      {agent.count > 1 && (
                        <span className="ml-1.5 text-xs text-text-muted">×{agent.count}</span>
                      )}
                    </div>
                    <div className="text-xs text-text-tertiary truncate">{agent.level}</div>
                  </div>
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded text-xs flex-shrink-0 ${
                      agent.status === 'active'
                        ? 'bg-success/10 text-success'
                        : agent.status === 'paused'
                        ? 'bg-warning/10 text-warning'
                        : agent.status === 'training'
                        ? 'bg-brand-50 text-brand-500'
                        : 'bg-elevated text-text-tertiary'
                    }`}
                  >
                    {agentStatusToLabel(agent.status)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/** 流程引擎视图 */
function ProcessesView({ processes }: { processes: ProcessEngineInstance[] }) {
  return (
    <div className="space-y-4">
      {processes.map((proc, idx) => (
        <div key={proc.engine_id} className="rounded-lg border border-border-default p-4">
          <div className="flex items-center gap-2 mb-3">
            <GitBranch className="w-5 h-5 text-warning" aria-hidden="true" />
            <span className="font-medium text-text-primary">
              {proc.name || `${processTypeToLabel(proc.process_type)} ${idx + 1}`}
            </span>
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-elevated text-text-secondary">
              {processTypeToLabel(proc.process_type)}
            </span>
          </div>
          <div className="flex items-center gap-1 overflow-x-auto pb-2">
            {proc.steps.map((step, idx) => (
              <div key={step.step_id} className="flex items-center flex-shrink-0">
                <div
                  className={`rounded-md px-3 py-2 text-xs ${
                    step.approval_required
                      ? 'bg-warning/10 text-warning border border-warning/30'
                      : 'bg-elevated text-text-secondary border border-border-default'
                  }`}
                >
                  <div className="font-medium">{step.name}</div>
                  {step.approval_required && step.approver_role && (
                    <div className="text-xs mt-0.5 opacity-80">{step.approver_role}</div>
                  )}
                </div>
                {idx < proc.steps.length - 1 && (
                  <span className="text-text-tertiary mx-0.5 flex-shrink-0">→</span>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

export function RuntimeVisualizer({
  runtime,
  view = 'operating',
  onViewChange,
}: RuntimeVisualizerProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-h4 text-text-primary">Enterprise Runtime 可视化</h3>
            <p className="text-sm text-text-tertiary mt-1">
              版本 {runtime.version} · 完成度 {Math.round(runtime.completeness * 100)}% · 编译于{' '}
              {new Date(runtime.compiled_at).toLocaleString('zh-CN')}
            </p>
          </div>
          <div className="flex flex-wrap gap-1">
            {VIEWS.map((v) => {
              const Icon = v.icon
              const active = view === v.key
              return (
                <button
                  key={v.key}
                  onClick={() => onViewChange?.(v.key)}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors ${
                    active
                      ? 'bg-brand-50 text-brand-500 border border-brand-200'
                      : 'text-text-secondary hover:bg-elevated border border-transparent'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" aria-hidden="true" />
                  {v.label}
                </button>
              )
            })}
          </div>
        </div>
      </CardHeader>
      <CardBody>
        {view === 'operating' && <EnterpriseModelDiagram runtime={runtime} />}
        {view === 'graph' && <CollaborationGraphView runtime={runtime} />}
        {view === 'organization' && (
          <OrganizationView
            departments={runtime.organization.departments}
            agents={runtime.agents}
          />
        )}
        {view === 'agents' && <AgentsView agents={runtime.agents} />}
        {view === 'processes' && <ProcessesView processes={runtime.process_engines} />}
      </CardBody>
    </Card>
  )
}

export default RuntimeVisualizer