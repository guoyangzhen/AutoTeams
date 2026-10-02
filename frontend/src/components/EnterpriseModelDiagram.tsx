/**
 * EnterpriseModelDiagram — 企业运转模型（#9 分层金字塔版）。
 *
 * 麦肯锡式"分层金字塔"：自上而下逐层加宽，呈现企业运转的层级结构——
 *   顶层 战略与组织 → 次层 数字员工 → 中层 业务流程 → 底层 运行底座。
 * 每层数据全部来自 runtime（organization.departments / agents /
 * process_engines / tool_registry / knowledge_index），随编译结果自动更新，
 * 不是静态装饰；层内元素按需求归类合并、去重后展示。
 */
import { useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Users,
  GitBranch,
  Wrench,
  Crown,
  Boxes,
} from 'lucide-react'
import type {
  RuntimeCompileResult,
  DepartmentInstance,
} from '@/types'
import { processTypeToLabel, agentStatusToLabel } from '@/utils/fieldMappings'

interface EnterpriseModelDiagramProps {
  runtime: RuntimeCompileResult
}

/** 占位/非岗位实体名（多来自知识图谱抽象节点，非真实数字员工岗位） */
const NON_AGENT_NAMES = new Set([
  '法定代表人', '直属上级', '接收人', '离职员工', '部门经理', '部门总监',
  '主管', '工程师', '财务', '销售', '经理', '总监', 'CEO / 总经理',
])

/** 顶层部门（核心管理层 / 高管层） */
function topDepartments(departments: DepartmentInstance[]): DepartmentInstance[] {
  const byId = new Map(departments.map((d) => [d.dept_id, d]))
  return departments.filter((d) => !d.parent_dept_id || !byId.has(d.parent_dept_id!))
}

/** Agent 按「岗位名 + 部门」归类去重，返回代表实例 + 数量 */
function dedupeAgents(agents: unknown[]): { item: Record<string, unknown>; count: number }[] {
  const map = new Map<string, { item: Record<string, unknown>; count: number }>()
  for (const a of agents ?? []) {
    const rec = a as Record<string, unknown>
    const name = rec.agent_name as string
    if (!name || NON_AGENT_NAMES.has(name)) continue
    const key = `${name}@@${rec.department ?? ''}`
    const existing = map.get(key)
    if (existing) {
      existing.count += 1
    } else {
      map.set(key, { item: rec, count: 1 })
    }
  }
  return Array.from(map.values())
}

/** 流程按类型归类去重（同名流程只保留一个） */
function dedupeProcesses(processes: unknown[]): { item: Record<string, unknown>; count: number }[] {
  const map = new Map<string, { item: Record<string, unknown>; count: number }>()
  for (const p of processes ?? []) {
    const rec = p as Record<string, unknown>
    const name = rec.name as string || processTypeToLabel(rec.process_type as string)
    const key = `${name}@@${rec.process_id ?? ''}`
    map.set(key, { item: rec, count: 1 })
  }
  return Array.from(map.values())
}

export function EnterpriseModelDiagram({ runtime }: EnterpriseModelDiagramProps) {
  const { organization, agents, process_engines, tool_registry, knowledge_index } = runtime
  const orgTop = useMemo(
    () => topDepartments(organization?.departments ?? []),
    [organization?.departments],
  )

  const agentGroups = useMemo(() => dedupeAgents(agents ?? []), [agents])
  const processGroups = useMemo(() => dedupeProcesses(process_engines ?? []), [process_engines])
  // 运行底座完整展示全部工具（含 LLM 服务/向量数据库等基础底座），不做截断
  const toolList = useMemo(() => tool_registry ?? [], [tool_registry])

  // 各层集合
  const layerOrg = orgTop.slice(0, 6)
  const layerAgents = agentGroups.slice(0, 8)
  // 业务流程层完整展示全部流程引擎（不截断，保证第二层流程名与流程引擎一致）
  const layerProcess = processGroups
  const layerBase = toolList.length > 0
    ? toolList.map((t) => ({ title: t.name, sub: t.installed ? '已就绪' : '未安装' }))
    : []

  const hasAny =
    layerOrg.length + layerAgents.length + layerProcess.length + layerBase.length +
    (knowledge_index?.vector_store_ref ? 1 : 0) > 0

  if (!hasAny) {
    return (
      <div className="text-center py-12 text-text-tertiary text-sm">
        暂无运转模型数据，请先完成企业编译
      </div>
    )
  }

  /** 层内元素统一结构 */
  interface LayerItem {
    title: string
    sub?: string
    count?: number
  }

  /** 金字塔层定义：level 越小越靠上（越窄），width 为梯形上/下边比例 */
  const layers: {
    key: string
    label: string
    icon: typeof Users
    tone: string
    dot: string
    width: string
    items: LayerItem[]
    empty: string
  }[] = [
    {
      key: 'org',
      label: '战略与组织',
      icon: Crown,
      tone: 'text-brand-500',
      dot: 'bg-brand-500',
      width: 'w-[38%]',
      items: layerOrg.map((d) => ({ title: d.name, sub: d.level === 0 ? '核心管理层' : '部门' })),
      empty: '暂无部门',
    },
    {
      key: 'agents',
      label: '数字员工',
      icon: Users,
      tone: 'text-info',
      dot: 'bg-info',
      width: 'w-[56%]',
      items: layerAgents.map(({ item, count }) => ({
        title: item.agent_name as string,
        sub: `${item.department ?? ''} · ${agentStatusToLabel(item.status as string)}`,
        count,
      })),
      empty: '暂无数字员工',
    },
    {
      key: 'process',
      label: '业务流程',
      icon: GitBranch,
      tone: 'text-success',
      dot: 'bg-success',
      width: 'w-[74%]',
      items: layerProcess.map(({ item }) => ({
        title: (item.name as string) || processTypeToLabel(item.process_type as string),
        sub: `${processTypeToLabel(item.process_type as string)}`,
      })),
      empty: '暂无流程引擎',
    },
    {
      key: 'base',
      label: '运行底座',
      icon: Wrench,
      tone: 'text-warning',
      dot: 'bg-warning',
      width: 'w-[92%]',
      items: layerBase,
      empty: '暂无工具',
    },
  ]

  return (
    <div className="rounded-lg border border-border-default bg-surface overflow-hidden">
      {/* 顶部中枢 */}
      <div className="relative px-4 sm:px-6 py-4 border-b border-border-default bg-elevated/40">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-2">
            <Boxes className="w-5 h-5 text-brand-500" aria-hidden="true" />
            <span className="font-serif-display text-base font-semibold text-text-primary">
              企业运转模型
            </span>
          </div>
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-brand-50 text-brand-500 font-mono">
            版本 {runtime.version}
          </span>
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-success/10 text-success">
            完成度 {Math.round(runtime.completeness * 100)}%
          </span>
          <span className="text-xs text-text-tertiary ml-auto">
                        {organization?.departments?.length ?? 0} 部门 · {agentGroups.length} 岗位 ·{' '}

            {processGroups.length} 流程引擎 · {tool_registry?.length ?? 0} 工具
          </span>
        </div>
      </div>

      {/* 分层金字塔：自上而下逐层加宽，居中堆叠 */}
      <div className="relative px-4 sm:px-8 py-6 space-y-1.5 flex flex-col items-center">
        {layers.map((layer, idx) => {
          const Icon = layer.icon
          return (
            <div key={layer.key} className={`relative ${layer.width} transition-all`}>
              {/* 梯形底（clip-path 形成金字塔斜坡） */}
              <div
                className="absolute inset-0 rounded-sm opacity-100"
                style={{
                  clipPath: `polygon(${97 - idx * 2}% 0, ${3 + idx * 2}% 0, 0% 100%, 100% 100%)`,
                  background: 'var(--elevated, rgba(127,127,127,.06))',
                }}
                aria-hidden="true"
              />
              <motion.div
                className="relative mx-2 my-1 rounded-md border border-border-subtle bg-surface/70 px-3 py-2.5 shadow-soft"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: idx * 0.08 }}
              >
                {/* 层头 */}
                <div className="flex items-center gap-1.5 mb-2">
                  <Icon className={`w-4 h-4 ${layer.tone}`} aria-hidden="true" />
                  <span className="text-sm font-medium text-text-primary">{layer.label}</span>
                  <span className="text-xs text-text-muted ml-auto">
                    {layer.items.length}
                    {layer.items.length > 0 ? ' 项' : ''}
                  </span>
                </div>

                {/* 层内元素（横向排列，同类归类合并） */}
                {layer.items.length === 0 ? (
                  <div className="text-xs text-text-tertiary py-1.5 text-center">{layer.empty}</div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {layer.items.map((it, i) => (
                      <span
                        key={`${layer.key}-${i}`}
                        className="inline-flex items-center gap-1.5 rounded-md border border-border-subtle bg-elevated/50 px-2 py-1 text-xs text-text-secondary"
                      >
                        <span className={`w-1.5 h-1.5 rounded-full ${layer.dot}`} aria-hidden="true" />
                        <span className="text-text-primary">{it.title}</span>
                        {it.sub && <span className="text-text-muted">{it.sub}</span>}
                        {it.count && it.count > 1 && (
                          <span className="text-text-muted">×{it.count}</span>
                        )}
                      </span>
                    ))}
                  </div>
                )}
              </motion.div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default EnterpriseModelDiagram