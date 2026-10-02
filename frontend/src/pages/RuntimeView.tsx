/**
 * RuntimeView — Enterprise Runtime 可视化页面（v2 重构）。
 *
 * 对应 PRD §6.5 前端可视化模块"Runtime 可视化" + spec.md §10.7 WT2 端点。
 *
 * 重构要点（v2）：
 * - 新增 Runtime 概览 MetricGrid：版本数 / Agent 数 / 流程数 / 完成度，直观展现后端 Runtime 数据
 * - 合并「版本管理」+「版本差异对比」两张 Card → 一张 Card + InlineTabs（版本历史 / 版本对比），
 *   解决两个版本相关 Card 纵向堆叠导致的 endless scrolling
 * - 空态处理：无版本时 EmptyState 引导去编译；无企业 ID 时 EmptyState
 * - Framer Motion：staggered 入场动画
 * - diff 变更项按 section 分组保留，但用更紧凑的卡片样式
 *
 * 功能：
 * - Runtime 四视图切换（协作图/组织/Agent/流程）
 * - 版本列表与切换
 * - 版本 diff 对比展示
 * - 回滚操作
 *
 * 错误状态：API 失败时显示告警 + 重试按钮（spec.md §2.5 约束）。
 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Activity, ArrowRight, History, GitCompare, RotateCcw, CheckCircle2, Plus, Minus, Pencil, Layers, Users, GitBranch, TrendingUp, Sparkles } from 'lucide-react'
import Layout from '@/components/Layout'

import { Button } from '@/components/ui/Button'
import { Card, CardBody } from '@/components/ui/Card'
import { Spinner } from '@/components/ui/Spinner'
import { Dialog } from '@/components/ui/Dialog'
import { ApiErrorState } from '@/components/ApiErrorState'
import { RuntimeVisualizer } from '@/components/RuntimeVisualizer'
import { MetricGrid, type Metric } from '@/components/ui/MetricGrid'
import { InlineTabs } from '@/components/ui/InlineTabs'
import { EmptyState } from '@/components/ui/EmptyState'
import * as runtimeApi from '@/api/runtime'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import type {
  RuntimeCompileResult,
  RuntimeVersionSummary,
  RuntimeDiff,
  RuntimeDiffChange,
} from '@/types'

type ViewType = 'operating' | 'graph' | 'organization' | 'agents' | 'processes'

/** diff 变更类型 → 图标 + 样式 + 标签 */
const diffChangeConfig: Record<RuntimeDiffChange['change_type'], {
  icon: typeof Plus; color: string; bg: string; label: string
}> = {
  added: { icon: Plus, color: 'text-success', bg: 'bg-success/5', label: '新增' },
  removed: { icon: Minus, color: 'text-error', bg: 'bg-error/5', label: '移除' },
  modified: { icon: Pencil, color: 'text-brand-500', bg: 'bg-brand-50', label: '修改' },
}

/** section → 中文标签 */
const SECTION_LABELS: Record<string, string> = {
  agents: 'AI 员工',
  process_engines: '流程引擎',
  organization: '组织架构',
  collaboration_graph: '协作关系',
  knowledge_index: '知识索引',
  tool_registry: '工具注册表',
}

export default function RuntimeView() {
  const enterpriseId = useEnterpriseId()
  const navigate = useNavigate()

  const [runtime, setRuntime] = useState<RuntimeCompileResult | null>(null)
  const [versions, setVersions] = useState<RuntimeVersionSummary[]>([])
  const [diff, setDiff] = useState<RuntimeDiff | null>(null)
  const [view, setView] = useState<ViewType>('operating')
  const [versionTab, setVersionTab] = useState<'history' | 'compare'>('history')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)
  const [diffError, setDiffError] = useState<string | null>(null)
  const [rollbackTarget, setRollbackTarget] = useState<string | null>(null)
  const [selectedVersions, setSelectedVersions] = useState<{ a: string; b: string }>({ a: '', b: '' })

  const loadRuntime = useCallback(async () => {
    if (!enterpriseId) return
    setLoading(true)
    setError(null)
    try {
      const [rt, vers] = await Promise.all([
        runtimeApi.getRuntime(enterpriseId),
        runtimeApi.listRuntimeVersions(enterpriseId),
      ])
      setRuntime(rt)
      setVersions(vers.items)
      // 默认选最新两个版本做 diff（vers.items 最新在前）
      if (vers.items.length >= 2) {
        setSelectedVersions({
          a: vers.items[1].version,
          b: vers.items[0].version,
        })
      } else if (vers.items.length === 1) {
        setSelectedVersions({ a: vers.items[0].version, b: vers.items[0].version })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载 Runtime 数据失败')
    } finally {
      setLoading(false)
    }
  }, [enterpriseId])

  useEffect(() => {
    loadRuntime()
  }, [loadRuntime])

  const handleLoadDiff = useCallback(async () => {
    if (!enterpriseId || !selectedVersions.a || !selectedVersions.b) return
    if (selectedVersions.a === selectedVersions.b) {
      setDiffError('请选择两个不同的版本进行对比')
      setDiff(null)
      return
    }
    setDiffLoading(true)
    setDiffError(null)
    try {
      const result = await runtimeApi.diffRuntimeVersions(
        enterpriseId,
        selectedVersions.a,
        selectedVersions.b,
      )
      setDiff(result)
    } catch (err) {
      setDiffError(err instanceof Error ? err.message : '加载版本 diff 失败')
      setDiff(null)
    } finally {
      setDiffLoading(false)
    }
  }, [enterpriseId, selectedVersions.a, selectedVersions.b])

  // 版本就绪后自动加载一次 diff（仅当有两个以上不同版本时）
  useEffect(() => {
    if (versions.length >= 2 && selectedVersions.a && selectedVersions.b && selectedVersions.a !== selectedVersions.b) {
      handleLoadDiff()
    }
  }, [versions, selectedVersions.a, selectedVersions.b, handleLoadDiff])

  const handleRollback = async () => {
    if (!rollbackTarget || !enterpriseId) return
    try {
      await runtimeApi.rollbackRuntime(enterpriseId, { target_version: rollbackTarget })
      await loadRuntime()
      setRollbackTarget(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '回滚失败')
    }
  }

  const handleVersionSwitch = async (version: string) => {
    if (!enterpriseId) return
    setLoading(true)
    try {
      const rt = await runtimeApi.getRuntimeVersion(enterpriseId, version)
      setRuntime(rt)
    } catch (err) {
      setError(err instanceof Error ? err.message : '切换版本失败')
    } finally {
      setLoading(false)
    }
  }

  // 按 section 分组 diff 变更项
  const groupedChanges = useMemo(() => {
    if (!diff) return []
    const groups = new Map<string, RuntimeDiffChange[]>()
    for (const change of diff.changes) {
      const list = groups.get(change.section) || []
      list.push(change)
      groups.set(change.section, list)
    }
    return Array.from(groups.entries())
  }, [diff])

  // Runtime 概览指标
  const overviewMetrics: Metric[] = useMemo(() => {
    if (!runtime) return []
    return [
      {
        key: 'versions',
        icon: Layers,
        value: versions.length,
        label: 'Runtime 版本',
        tone: 'brand',
      },
      {
        key: 'agents',
        icon: Users,
        value: runtime.agents?.length ?? 0,
        label: 'Agent 状态',
        tone: 'info',
      },
      {
        key: 'processes',
        icon: GitBranch,
        value: runtime.process_engines?.length ?? 0,
        label: '流程引擎',
        tone: 'success',
      },
      {
        key: 'completeness',
        icon: TrendingUp,
        value: Math.round(runtime.completeness * 100),
        unit: '%',
        label: '完成度',
        tone: runtime.completeness >= 0.8 ? 'success' : runtime.completeness >= 0.6 ? 'warning' : 'error',
      },
    ]
  }, [runtime, versions.length])

  // 容器入场动画 variants
  const containerVariants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
  }
  const itemVariants = {
    hidden: { opacity: 0, y: 12 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: 'easeOut' as const } },
  }

  if (loading && !runtime) {
    return (
      <Layout>
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      </Layout>
    )
  }

  if (!enterpriseId) {
    return (
      <Layout>
        <div className="max-w-3xl mx-auto w-full px-4 sm:px-6 py-8">
          <EmptyState
            icon={Layers}
            title="未检测到企业信息"
            description="请先登录或联系管理员分配企业，才能查看企业运行时（Enterprise Runtime）。"
            variant="warning"
          />
        </div>
      </Layout>
    )
  }

  // 无 Runtime 数据（尚未编译过）
  if (!runtime && !loading) {
    return (
      <Layout>
        <motion.div
          className="max-w-3xl mx-auto w-full px-4 sm:px-6 py-8"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <EmptyState
            icon={Sparkles}
            title="尚未生成企业运行时"
            description="Enterprise Runtime 由五级编译器编译产出。请先在「企业编译」页完成一次编译，生成 Runtime 后此处将展示可视化运行态、版本管理与差异对比。"
            variant="brand"
            action={{ label: '前往企业编译', to: '/compile' }}
          />
        </motion.div>
      </Layout>
    )
  }

  return (
    <Layout>
      <motion.div
        className="max-w-[1600px] mx-auto w-full px-4 sm:px-6 py-8 space-y-6"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {/* 成果导向首屏：解释 Runtime 如何进入真实协作和经营结果，而非只展示技术结构。 */}
        <motion.div variants={itemVariants}>
          {runtime && (
            <RuntimeOutcomeBrief
              runtime={runtime}
              versionCount={versions.length}
              onOpenCompany={() => navigate('/company')}
              onExplore={() => document.getElementById('runtime-visualizer')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
            />
          )}
        </motion.div>

        {error && (
          <motion.div variants={itemVariants}>
            <ApiErrorState message={error} onRetry={loadRuntime} retrying={loading} />
          </motion.div>
        )}

        {/* Runtime 概览指标 */}
        {runtime && (
          <motion.div variants={itemVariants}>
            <MetricGrid metrics={overviewMetrics} columns={4} />
          </motion.div>
        )}

        {/* Runtime 可视化 */}
        {runtime && (
          <motion.div id="runtime-visualizer" variants={itemVariants}>
            <RuntimeVisualizer runtime={runtime} view={view} onViewChange={setView} />
          </motion.div>
        )}

        {/* 版本管理 + 版本对比（合并为 InlineTabs） */}
        <motion.div variants={itemVariants}>
          <Card>
            <CardBody>
              <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
                <InlineTabs
                  variant="underline"
                  tabs={[
                    { key: 'history', label: '版本历史', icon: History, badge: versions.length > 0 ? <span className="ml-1 text-xs text-text-muted">{versions.length}</span> : undefined },
                    { key: 'compare', label: '版本对比', icon: GitCompare, disabled: versions.length < 2 },
                  ]}
                  activeKey={versionTab}
                  onChange={(k) => setVersionTab(k as 'history' | 'compare')}
                />
              </div>

              {/* 版本历史 */}
              {versionTab === 'history' && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.25 }}
                >
                  {versions.length === 0 ? (
                    <EmptyState
                      icon={History}
                      title="暂无 Runtime 版本"
                      description="请先在「企业编译」页完成一次编译，生成 Runtime 后此处将展示版本历史。"
                      variant="default"
                      action={{ label: '前往企业编译', to: '/compile' }}
                    />
                  ) : (
                    <div className="space-y-2">
                      {versions.map((v, idx) => (
                        <motion.div
                          key={v.version}
                          initial={{ opacity: 0, x: -8 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ duration: 0.3, delay: idx * 0.05 }}
                          className={`flex items-center justify-between rounded-lg border p-3 transition-colors ${
                            v.version === runtime?.version
                              ? 'border-brand-300 bg-brand-50/30'
                              : 'border-border-default hover:border-border-strong'
                          }`}
                        >
                          <div className="flex items-center gap-3 min-w-0 flex-1">
                            {v.is_active ? (
                              <CheckCircle2 className="w-5 h-5 text-success flex-shrink-0" aria-hidden="true" />
                            ) : (
                              <div className="w-5 h-5 rounded-full border-2 border-border-default flex-shrink-0" aria-hidden="true" />
                            )}
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="font-medium text-text-primary font-mono">{v.version}</span>
                                {v.is_active && (
                                  <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-success/10 text-success">
                                    当前激活
                                  </span>
                                )}
                                {v.model_version && (
                                  <span className="text-xs text-text-muted">模型 {v.model_version}</span>
                                )}
                              </div>
                              <div className="flex items-center gap-3 mt-0.5 text-xs text-text-tertiary">
                                <span>{v.compiled_at ? new Date(v.compiled_at).toLocaleString('zh-CN') : '—'}</span>
                                <span>完成度 {Math.round(v.completeness * 100)}%</span>
                              </div>
                            </div>
                          </div>
                          <div className="flex gap-2 flex-shrink-0">
                            {v.version !== runtime?.version && (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleVersionSwitch(v.version)}
                              >
                                查看
                              </Button>
                            )}
                            {!v.is_active && (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setRollbackTarget(v.version)}
                              >
                                <RotateCcw className="w-3.5 h-3.5" aria-hidden="true" />
                                回滚
                              </Button>
                            )}
                          </div>
                        </motion.div>
                      ))}
                    </div>
                  )}
                </motion.div>
              )}

              {/* 版本对比 */}
              {versionTab === 'compare' && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.25 }}
                >
                  {versions.length < 2 ? (
                    <EmptyState
                      icon={GitCompare}
                      title="至少需要 2 个版本才能对比"
                      description={`当前仅有 ${versions.length} 个版本，完成更多编译后即可对比版本间差异。`}
                      variant="default"
                    />
                  ) : (
                    <>
                      {/* 版本选择器 */}
                      <div className="flex flex-wrap items-center gap-3 mb-4 p-3 rounded-lg bg-elevated/40">
                        <div className="flex items-center gap-2">
                          <label className="text-xs text-text-tertiary whitespace-nowrap">基准</label>
                          <select
                            value={selectedVersions.a}
                            onChange={(e) => setSelectedVersions({ ...selectedVersions, a: e.target.value })}
                            className="ui-control min-h-9 rounded-xl border border-border-default bg-[var(--surface-raised)] px-3 py-1.5 text-sm text-text-primary font-mono focus:border-brand-400"
                            aria-label="基准版本"
                          >
                            {versions.map((v) => (
                              <option key={v.version} value={v.version}>{v.version}</option>
                            ))}
                          </select>
                        </div>
                        <GitCompare className="w-4 h-4 text-text-tertiary" aria-hidden="true" />
                        <div className="flex items-center gap-2">
                          <label className="text-xs text-text-tertiary whitespace-nowrap">目标</label>
                          <select
                            value={selectedVersions.b}
                            onChange={(e) => setSelectedVersions({ ...selectedVersions, b: e.target.value })}
                            className="ui-control min-h-9 rounded-xl border border-border-default bg-[var(--surface-raised)] px-3 py-1.5 text-sm text-text-primary font-mono focus:border-brand-400"
                            aria-label="目标版本"
                          >
                            {versions.map((v) => (
                              <option key={v.version} value={v.version}>{v.version}</option>
                            ))}
                          </select>
                        </div>
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={handleLoadDiff}
                          disabled={diffLoading || selectedVersions.a === selectedVersions.b}
                        >
                          {diffLoading ? '对比中...' : '开始对比'}
                        </Button>
                      </div>

                      {/* diff 错误提示 */}
                      {diffError && (
                        <div className="mb-4 p-3 rounded-lg bg-error/5 border border-error/20 text-sm text-error flex items-center gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-error flex-shrink-0" />
                          {diffError}
                        </div>
                      )}

                      {/* diff 结果 */}
                      {diffLoading && !diff ? (
                        <div className="flex items-center justify-center py-10">
                          <Spinner size="md" />
                          <span className="ml-2 text-sm text-text-tertiary">正在对比版本差异…</span>
                        </div>
                      ) : diff ? (
                        <div>
                          {/* 摘要 */}
                          <div className="mb-4 p-4 rounded-lg bg-info/5 border border-info/20">
                            <p className="text-sm text-text-secondary leading-relaxed">{diff.summary}</p>
                            <div className="flex items-center gap-4 mt-2 text-xs text-text-tertiary flex-wrap">
                              <span>
                                <code className="font-mono text-text-secondary">{diff.version_a || selectedVersions.a}</code>
                                {diff.a_compiled_at && ` · ${new Date(diff.a_compiled_at).toLocaleDateString('zh-CN')}`}
                              </span>
                              <span>→</span>
                              <span>
                                <code className="font-mono text-text-secondary">{diff.version_b || selectedVersions.b}</code>
                                {diff.b_compiled_at && ` · ${new Date(diff.b_compiled_at).toLocaleDateString('zh-CN')}`}
                              </span>
                              <span className="ml-auto">
                                共 {diff.changes.length} 项变更
                              </span>
                            </div>
                          </div>

                          {/* 变更统计条 */}
                          <div className="flex items-center gap-4 mb-4 text-xs">
                            {(['added', 'modified', 'removed'] as const).map((type) => {
                              const cfg = diffChangeConfig[type]
                              const count = diff.changes.filter((c) => c.change_type === type).length
                              const Icon = cfg.icon
                              return (
                                <span key={type} className={`inline-flex items-center gap-1 ${cfg.color}`}>
                                  <Icon className="w-3.5 h-3.5" aria-hidden="true" />
                                  {cfg.label} {count}
                                </span>
                              )
                            })}
                          </div>

                          {/* 按 section 分组展示 */}
                          {groupedChanges.length === 0 ? (
                            <div className="py-8 text-center text-sm text-text-tertiary">
                              两个版本之间无结构差异
                            </div>
                          ) : (
                            <div className="space-y-4">
                              {groupedChanges.map(([section, changes]) => (
                                <div key={section}>
                                  <h4 className="text-xs font-semibold text-text-tertiary uppercase tracking-wider mb-2">
                                    {SECTION_LABELS[section] || section}
                                    <span className="ml-2 text-text-muted">({changes.length})</span>
                                  </h4>
                                  <div className="space-y-2">
                                    {changes.map((change, idx) => {
                                      const cfg = diffChangeConfig[change.change_type]
                                      const Icon = cfg.icon
                                      return (
                                        <div
                                          key={idx}
                                          className={`rounded-lg border border-border-subtle p-3 ${cfg.bg} flex items-start gap-3`}
                                        >
                                          <div className={`flex-shrink-0 mt-0.5 ${cfg.color}`}>
                                            <Icon className="w-4 h-4" aria-hidden="true" />
                                          </div>
                                          <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap">
                                              <span className={`text-xs font-medium ${cfg.color}`}>{cfg.label}</span>
                                              <code className="text-xs text-text-secondary font-mono bg-surface/60 px-1.5 py-0.5 rounded">
                                                {change.key}
                                              </code>
                                            </div>
                                            {change.detail && (
                                              <p className="text-sm text-text-primary mt-1 leading-relaxed">{change.detail}</p>
                                            )}
                                          </div>
                                        </div>
                                      )
                                    })}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="py-8 text-center text-sm text-text-tertiary">
                          选择基准与目标版本后点击「开始对比」
                        </div>
                      )}
                    </>
                  )}
                </motion.div>
              )}
            </CardBody>
          </Card>
        </motion.div>
      </motion.div>

      {/* 回滚确认对话框 */}
      <Dialog
        open={!!rollbackTarget}
        title="确认回滚 Runtime 版本"
        description={`将回滚至版本 ${rollbackTarget}，当前激活版本将被替换。此操作会同步回滚相关 Agent 配置。`}
        variant="danger"
        confirmText="确认回滚"
        cancelText="取消"
        onConfirm={handleRollback}
        onCancel={() => setRollbackTarget(null)}
      />
    </Layout>
  )
}


function RuntimeOutcomeBrief({
  runtime,
  versionCount,
  onOpenCompany,
  onExplore,
}: {
  runtime: RuntimeCompileResult
  versionCount: number
  onOpenCompany: () => void
  onExplore: () => void
}) {
  const completion = Math.round(runtime.completeness * 100)
  const nextFocus = completion >= 80
    ? '运行 AI 团队，观察任务链路与业务效果'
    : '补齐关键知识或流程缺口，再扩大自动化范围'

  return (
    <section aria-labelledby="runtime-outcome-title" className="ui-card overflow-hidden rounded-2xl border border-border-default">
      <div className="relative grid gap-5 p-5 sm:p-6 lg:grid-cols-[auto_minmax(0,1fr)_auto] lg:items-center">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-brand-500/15 bg-brand-500/10 text-brand-500">
          <Layers className="h-5 w-5" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="ui-context-kicker">编译成果 · Enterprise Runtime</span>
            <span className="inline-flex items-center gap-1.5 text-xs text-text-tertiary">
              <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden="true" />
              当前版本 {runtime.version} · {versionCount} 个可追溯快照
            </span>
          </div>
          <h1 id="runtime-outcome-title" className="ui-page-title mt-1.5">企业运行模型已经可以驱动下一步协作</h1>
          <p className="ui-page-description mt-1.5 max-w-3xl text-sm">
            组织、岗位、流程与知识已被编译为可版本化的共同上下文。下一步重点是：{nextFocus}。
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-text-secondary">
            <span className="rounded-lg bg-[var(--surface-sunken)] px-2.5 py-1.5">完成度 {completion}%</span>
            <span className="rounded-lg bg-[var(--surface-sunken)] px-2.5 py-1.5">{runtime.agents?.length ?? 0} 项 Agent 配置</span>
            <span className="rounded-lg bg-[var(--surface-sunken)] px-2.5 py-1.5">{runtime.process_engines?.length ?? 0} 个流程引擎</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 lg:justify-self-end">
          <Button variant="outline" size="md" onClick={onExplore}>
            <Layers className="h-4 w-4" aria-hidden="true" />
            查看运行模型
          </Button>
          <Button variant="primary" size="md" onClick={onOpenCompany}>
            <Activity className="h-4 w-4" aria-hidden="true" />
            进入实时运转
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </div>
    </section>
  )
}
