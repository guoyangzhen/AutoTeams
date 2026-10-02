import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { toast } from 'sonner'
import {
  Sparkles,
  CheckCircle2,
  XCircle,
  Trash2,
  Eye,
  History,
  ChevronDown,
  AlertTriangle,
  Loader2,
  RefreshCw,
  Cpu,
  GitBranch,
  FolderUp,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'
import { JsonView } from '@/components/ui/JsonView'
import { useAuth } from '@/hooks/useAuth'
import { useConfirmDialog } from '@/hooks/useConfirmDialog'
import { Agent } from '@/types'
import { getAgents } from '@/api/agents'
import {
  getSkills,
  generateSkills,
  importSkill,
  approveSkill,
  rejectSkill,
  deleteSkill,
  getSkillExecutions,
  type SkillExecutionRecord,
} from '@/api/skills'

/**
 * 技能管理面板（死代码激活：A4 + A5 + B1 + B2）
 *
 * 激活的后端能力：
 * - POST /skills/generate         → AI 自主生成 Skill（A4）
 * - POST /skills/{id}/approve     → 审批通过（A5）
 * - POST /skills/{id}/reject      → 拒绝（A5）
 * - DELETE /skills/{id}           → 删除（B1）
 * - GET  /skills/{id}/executions  → 执行历史（B2）
 *
 * 工作流程：「数字员工自主进化」闭环：
 *   AI 看完知识库 → 自动生成 Skill → 静态筛查 + 沙箱试运行
 *   → 低风险自动放行 / 高风险人工审批 → 上线执行 → 历史可追溯
 */

type SkillStatus = 'approved' | 'pending' | 'rejected' | 'draft'

interface SkillListItem {
  id: string
  agent_id: string
  name: string
  description: string | null
  skill_type: string
  input_type: string | null
  output_type: string | null
  config: Record<string, unknown> | null
  source: string
  status: SkillStatus
  review_result: Record<string, unknown> | null
  created_at: string
}

/** 状态 → 标签/徽章色/图标盒色（对标 prototype §技能管理，使用 success/warning/error 设计令牌） */
const STATUS_LABELS: Record<SkillStatus, string> = {
  approved: '已批准',
  pending: '待审批',
  rejected: '已拒绝',
  draft: '草稿',
}

const STATUS_BADGE: Record<SkillStatus, string> = {
  approved: 'bg-success/10 text-success',
  pending: 'bg-warning/10 text-warning',
  rejected: 'bg-error/10 text-error',
  draft: 'bg-elevated text-text-secondary',
}

const STATUS_ICON_BOX: Record<SkillStatus, string> = {
  approved: 'bg-success/10 text-success',
  pending: 'bg-warning/10 text-warning',
  rejected: 'bg-error/10 text-error',
  draft: 'bg-elevated text-text-tertiary',
}

const SOURCE_LABELS: Record<string, string> = {
  generated: 'AI 生成',
  imported: '导入',
  builtin: '内置',
  manual: '手动',
}

/** 技能类型 → 中文标签（对标 prototype badge） */
const SKILL_TYPE_LABELS: Record<string, string> = {
  search: '检索能力',
  lookup: '检索能力',
  summary: '业务能力',
  action: '业务能力',
  notification: '系统工具',
  tool: '系统工具',
}

function getSkillTypeLabel(t: string): string {
  return SKILL_TYPE_LABELS[t] || '业务能力'
}

export default function SkillManagement() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const confirmDialog = useConfirmDialog()

  const [agents, setAgents] = useState<Agent[]>([])
  const [selectedAgentId, setSelectedAgentId] = useState<string>('')
  const [skills, setSkills] = useState<SkillListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [acting, setActing] = useState<string | null>(null) // 正在操作的 skill id
  const [expandedSkillId, setExpandedSkillId] = useState<string | null>(null)
  const [executions, setExecutions] = useState<SkillExecutionRecord[]>([])
  const [loadingExecutions, setLoadingExecutions] = useState(false)
  const [statusFilter, setStatusFilter] = useState<SkillStatus | 'all'>('all')
  const [importing, setImporting] = useState(false)
  const folderInputRef = useRef<HTMLInputElement>(null)

  // 加载 Agent 列表
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const list = await getAgents()
        if (cancelled) return
        setAgents(list)
        if (list.length > 0 && !selectedAgentId) {
          setSelectedAgentId(list[0].id)
        }
      } catch (err) {
        console.error('加载 Agent 列表失败:', err)
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 加载技能列表
  const loadSkills = useCallback(async () => {
    if (!selectedAgentId) return
    try {
      setLoading(true)
      const list = await getSkills(selectedAgentId)
      setSkills(list as unknown as SkillListItem[])
    } catch (err) {
      console.error('加载技能列表失败:', err)
      toast.error('加载技能列表失败')
    } finally {
      setLoading(false)
    }
  }, [selectedAgentId])

  useEffect(() => {
    loadSkills()
  }, [loadSkills])

  // AI 生成 Skill
  const handleGenerate = async () => {
    if (!selectedAgentId) return
    try {
      setGenerating(true)
      toast.info('AI 正在分析上下文/流程/协作文档，自动生成技能...')
      const result = await generateSkills(selectedAgentId, 3)
      const autoApproved = result.skills.filter(
        (s) => (s as unknown as SkillListItem).status === 'approved',
      ).length
      toast.success(
        `已自动生成 ${result.generated} 个技能（${autoApproved} 个低风险自动放行，${result.generated - autoApproved} 个待人工审批）`,
      )
      await loadSkills()
    } catch (err) {
      console.error('生成 Skill 失败:', err)
      toast.error(err instanceof Error ? err.message : 'AI 自动生成技能失败')
    } finally {
      setGenerating(false)
    }
  }

  // 导入技能文件夹：读取文件夹内的技能定义文件，逐个导入为待审批技能
  const handleImportFolder = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files || [])
    if (!selectedFiles.length) return
    if (!selectedAgentId) {
      toast.error('请先选择一个 Agent')
      return
    }
    setImporting(true)
    let success = 0
    let failed = 0
    try {
      // 限制数量，避免一次导入过多
      const files = selectedFiles.slice(0, 20)
      for (const file of files) {
        try {
          const text = await file.text()
          const name = file.name.replace(/\.[^.]+$/, '')
          const importResult = await importSkill(selectedAgentId, {
            name,
            description: `${file.name}（导入技能）`,
            skill_type: 'custom',
            input_type: 'text',
            output_type: 'text',
            config: {
              prompt_template: text.slice(0, 4000),
              source_file: file.name,
            },
          })
          if (importResult.status === 'pending' || importResult.status === 'approved') {
            success += 1
          } else {
            failed += 1
          }
        } catch {
          failed += 1
        }
      }
      toast.success(
        `导入完成：成功 ${success} 个技能进入待审批，失败 ${failed} 个`,
      )
      await loadSkills()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '导入技能文件夹失败')
    } finally {
      setImporting(false)
      e.target.value = ''
    }
  }

  // 审批通过
  const handleApprove = async (skillId: string) => {
    try {
      setActing(skillId)
      await approveSkill(skillId, '通过 SkillManagement 面板审批')
      toast.success('已批准该 Skill')
      await loadSkills()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '审批失败')
    } finally {
      setActing(null)
    }
  }

  // 拒绝
  const handleReject = async (skillId: string) => {
    try {
      setActing(skillId)
      await rejectSkill(skillId, '通过 SkillManagement 面板拒绝')
      toast.success('已拒绝该 Skill')
      await loadSkills()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '拒绝失败')
    } finally {
      setActing(null)
    }
  }

  // 删除
  const handleDelete = async (skillId: string, name: string) => {
    const ok = await confirmDialog.ask({
      title: '删除 Skill',
      description: `确定删除 Skill「${name}」？此操作不可撤销。`,
      confirmText: '删除',
      cancelText: '取消',
      variant: 'danger',
    })
    if (!ok) return
    try {
      setActing(skillId)
      await deleteSkill(skillId)
      toast.success('已删除')
      await loadSkills()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
    } finally {
      setActing(null)
    }
  }

  // 查看执行历史
  const handleViewExecutions = async (skillId: string) => {
    if (expandedSkillId === skillId) {
      setExpandedSkillId(null)
      return
    }
    setExpandedSkillId(skillId)
    setLoadingExecutions(true)
    try {
      const list = await getSkillExecutions(skillId, { limit: 10 })
      setExecutions(list)
    } catch (err) {
      console.error('加载执行历史失败:', err)
      setExecutions([])
    } finally {
      setLoadingExecutions(false)
    }
  }

  // 状态过滤
  const filteredSkills =
    statusFilter === 'all' ? skills : skills.filter((s) => s.status === statusFilter)

  const pendingCount = skills.filter((s) => s.status === 'pending').length

  /** 解析 review_result 摘要（用于 4 列详情） */
  function parseReview(skill: SkillListItem) {
    if (!skill.review_result) return null
    const r = skill.review_result as Record<string, unknown>
    return {
      staticScreening: r.static_screening as { passed?: boolean; risk_level?: string } | undefined,
      reviewRun: r.review_run as { passed?: boolean; reason?: string } | undefined,
      autoApproved: r.auto_approved as { reason?: string; timestamp?: string } | undefined,
    }
  }

  return (
    <Layout>
      <div className="max-w-[1600px] mx-auto w-full px-4 sm:px-6 py-8">
        {/* 标题 */}
        <PageHeader
          title="技能管理"
          subtitle="AI 自主生成 Skill 全生命周期闭环 · 生成 → 双重筛查 → 审批 → 上线 → 执行追溯"
          actions={
            <div className="flex flex-col items-end gap-1.5">
              <div className="flex items-center gap-2">
                {pendingCount > 0 && (
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs bg-warning/10 text-warning border border-warning/20">
                    <AlertTriangle className="w-3 h-3" />
                    {pendingCount} 待审批
                  </span>
                )}
                <Button
                  onClick={handleGenerate}
                  disabled={generating || !selectedAgentId}
                  size="sm"
                >
                  {generating ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      AI 生成中...
                    </>
                  ) : (
                    <>
                      <Sparkles className="w-4 h-4" />
                      自动生成技能
                    </>
                  )}
                </Button>
                <input
                  ref={folderInputRef}
                  type="file"
                  className="hidden"
                  onChange={handleImportFolder}
                  disabled={importing}
                  {...{ webkitdirectory: 'true', directory: '' }}
                />
                <Button
                  onClick={() => folderInputRef.current?.click()}
                  disabled={importing || !selectedAgentId}
                  size="sm"
                  variant="outline"
                >
                  {importing ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      导入中...
                    </>
                  ) : (
                    <>
                      <FolderUp className="w-4 h-4" />
                      导入技能文件夹
                    </>
                  )}
                </Button>
              </div>
              <p className="text-xs text-text-tertiary">
                也可复制 GitHub 技能安装命令到协作工作台让智能体安装
              </p>
            </div>
          }
        />

        {/* 5 步流程卡（对标 prototype §技能管理 流程提示卡） */}
        <div className="mb-5 rounded-xl border border-brand-100 bg-brand-50/50 p-4">
          <div className="flex items-center gap-2 mb-3">
            <GitBranch className="w-4 h-4 text-brand-500" aria-hidden="true" />
            <span className="text-sm font-semibold text-brand-500">
              生成 → 筛查 → 审批 → 上线 → 追溯
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs">
            {[
              { n: '①', title: '自主生成', desc: '根据缺口自动生成' },
              { n: '②', title: '静态筛查', desc: 'AST 解析 + 关键词' },
              { n: '③', title: '沙箱试运行', desc: '隔离环境验证' },
              { n: '④', title: '审批上线', desc: '管理员审批' },
              { n: '⑤', title: '执行追溯', desc: '全量调用记录' },
            ].map((step) => (
              <div
                key={step.n}
                className="rounded-md bg-surface p-2 border border-border-subtle"
              >
                <div className="font-medium text-text-primary">
                  {step.n} {step.title}
                </div>
                <div className="text-text-tertiary mt-0.5">{step.desc}</div>
              </div>
            ))}
          </div>
        </div>

        {/* 筛选栏（对标 prototype §技能管理 筛选栏） */}
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <select
            value={selectedAgentId}
            onChange={(e) => setSelectedAgentId(e.target.value)}
            className="bg-surface border border-border-default rounded-md px-3 py-2 text-sm focus:outline-none focus:border-brand-500 text-text-primary"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>

          <div className="flex items-center bg-elevated rounded-md p-0.5">
            {(['all', 'approved', 'pending', 'rejected'] as const).map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-3 py-1.5 text-xs font-medium rounded transition-colors ${
                  statusFilter === s
                    ? 'bg-surface text-brand-500 shadow-soft'
                    : 'text-text-tertiary hover:text-text-secondary'
                }`}
              >
                {s === 'all' ? '全部' : STATUS_LABELS[s]}
              </button>
            ))}
          </div>

          <button
            onClick={loadSkills}
            disabled={loading}
            title="刷新"
            aria-label="刷新技能列表"
            className="ml-auto p-2 text-text-secondary hover:text-brand-500 border border-border-default rounded-md transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          </button>
        </div>

        {/* 技能卡片列表 */}
        {loading ? (
          <div className="flex items-center justify-center min-h-[40vh]">
            <Spinner size="lg" className="text-brand-500" />
          </div>
        ) : filteredSkills.length === 0 ? (
          <div className="bg-surface border border-border-default rounded-xl p-12 text-center">
            <Cpu className="w-12 h-12 mx-auto mb-3 text-text-tertiary opacity-40" aria-hidden="true" />
            <p className="text-text-secondary mb-1">
              {selectedAgentId ? '该 Agent 暂无 Skill' : '请先选择一个 Agent'}
            </p>
            <p className="text-xs text-text-tertiary">
              点击右上角「AI 生成 Skill」让 AI 自主生成第一个技能
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {filteredSkills.map((skill) => {
              const review = parseReview(skill)
              return (
              <motion.div
                key={skill.id}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                className="bg-surface border border-border-default rounded-xl overflow-hidden"
              >
                {/* 卡片主体（对标 prototype：图标盒 + 徽章组 + 4 列详情） */}
                <div className="p-4 flex items-start gap-3">
                  {/* 图标盒（按状态着色） */}
                  <div
                    className={`w-10 h-10 rounded-lg ${STATUS_ICON_BOX[skill.status]} flex items-center justify-center flex-shrink-0`}
                  >
                    <Cpu className="w-5 h-5" aria-hidden="true" />
                  </div>

                  {/* 主信息 */}
                  <div className="flex-1 min-w-0">
                    {/* 标题 + 徽章组 */}
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <Link
                        to={`/skill/${skill.id}`}
                        className="text-sm font-medium text-text-primary hover:text-brand-500 transition-colors"
                      >
                        {skill.name}
                      </Link>
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_BADGE[skill.status]}`}>
                        {STATUS_LABELS[skill.status]}
                      </span>
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-elevated text-text-secondary">
                        {getSkillTypeLabel(skill.skill_type)}
                      </span>
                      {skill.source && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-brand-50 text-brand-500">
                          {SOURCE_LABELS[skill.source] || skill.source}
                        </span>
                      )}
                    </div>

                    <p className="text-xs text-text-tertiary">
                      {skill.description || '暂无描述'}
                    </p>

                    {/* 4 列详情网格（对标 prototype §技能管理） */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3 text-xs">
                      <div>
                        <div className="text-text-muted">静态筛查</div>
                        <div
                          className={`font-medium ${
                            review?.staticScreening?.passed ? 'text-success' : review?.staticScreening ? 'text-error' : 'text-text-tertiary'
                          }`}
                        >
                          {review?.staticScreening
                            ? review.staticScreening.passed
                              ? '通过 · 0 高危'
                              : '未通过'
                            : '—'}
                        </div>
                      </div>
                      <div>
                        <div className="text-text-muted">沙箱试运行</div>
                        <div
                          className={`font-medium ${
                            review?.reviewRun?.passed ? 'text-success' : review?.reviewRun ? 'text-warning' : 'text-text-tertiary'
                          }`}
                        >
                          {review?.reviewRun
                            ? review.reviewRun.passed
                              ? '通过'
                              : '部分通过'
                            : '—'}
                        </div>
                      </div>
                      <div>
                        <div className="text-text-muted">来源</div>
                        <div className="text-text-primary font-medium">
                          {SOURCE_LABELS[skill.source] || skill.source || '—'}
                        </div>
                      </div>
                      <div>
                        <div className="text-text-muted">生成时间</div>
                        <div className="text-text-primary font-medium">
                          {new Date(skill.created_at).toLocaleDateString('zh-CN')}
                        </div>
                      </div>
                    </div>

                    {/* 自动放行标识 */}
                    {review?.autoApproved && (
                      <div className="mt-2 text-xs text-success font-medium flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
                        低风险自动放行
                      </div>
                    )}

                    {/* 待审批操作按钮（对标 prototype：实色 bg-success/bg-error） */}
                    {skill.status === 'pending' && isAdmin && (
                      <div className="flex items-center gap-2 mt-3">
                        <button
                          onClick={() => handleApprove(skill.id)}
                          disabled={acting === skill.id}
                          className="bg-success text-white rounded-md px-3 py-1.5 text-xs font-medium hover:bg-success/90 flex items-center gap-1 disabled:opacity-50"
                        >
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          批准
                        </button>
                        <button
                          onClick={() => handleReject(skill.id)}
                          disabled={acting === skill.id}
                          className="bg-error text-white rounded-md px-3 py-1.5 text-xs font-medium hover:bg-error/90 flex items-center gap-1 disabled:opacity-50"
                        >
                          <XCircle className="w-3.5 h-3.5" />
                          拒绝
                        </button>
                      </div>
                    )}
                  </div>

                  {/* 右侧操作图标（对标 prototype：history + trash） */}
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      onClick={() => handleViewExecutions(skill.id)}
                      title="执行历史"
                      className="p-1.5 text-text-secondary hover:text-brand-500 transition-colors"
                    >
                      {expandedSkillId === skill.id ? (
                        <ChevronDown className="w-4 h-4" />
                      ) : (
                        <History className="w-4 h-4" />
                      )}
                    </button>
                    {isAdmin && (
                      <button
                        onClick={() => handleDelete(skill.id, skill.name)}
                        disabled={acting === skill.id}
                        title="删除"
                        aria-label={`删除技能 ${skill.name}`}
                        className="p-1.5 text-text-secondary hover:text-error transition-colors disabled:opacity-50"
                      >
                        <Trash2 className="w-4 h-4" aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </div>

                {/* 展开区：执行历史 */}
                {expandedSkillId === skill.id && (
                  <div className="border-t border-border-subtle bg-elevated/30 p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <Eye className="w-4 h-4 text-text-tertiary" aria-hidden="true" />
                      <h4 className="text-sm font-medium text-text-primary">执行历史</h4>
                      <span className="text-xs text-text-tertiary">最近 10 条</span>
                    </div>
                    {loadingExecutions ? (
                      <div className="flex justify-center py-4">
                        <Spinner size="sm" className="text-brand-500" />
                      </div>
                    ) : executions.length === 0 ? (
                      <div className="text-center py-4">
                        <p className="text-xs text-text-tertiary">暂无执行记录</p>
                        <p className="text-[11px] text-text-muted mt-1">
                          执行该 Skill（如详情页「在线试用」）后，执行历史会在此留痕。
                        </p>
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-text-tertiary border-b border-border-subtle">
                              <th className="text-left py-2 px-2 font-medium">时间</th>
                              <th className="text-left py-2 px-2 font-medium">状态</th>
                              <th className="text-right py-2 px-2 font-medium">耗时</th>
                              <th className="text-left py-2 px-2 font-medium">输入</th>
                              <th className="text-left py-2 px-2 font-medium">输出</th>
                            </tr>
                          </thead>
                          <tbody>
                            {executions.map((e) => (
                              <tr
                                key={e.id}
                                className="border-b border-border-subtle/50 hover:bg-elevated"
                              >
                                <td className="py-2 px-2 text-text-tertiary font-mono">
                                  {new Date(e.created_at).toLocaleString('zh-CN')}
                                </td>
                                <td className="py-2 px-2">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-xs ${
                                      e.status === 'success'
                                        ? 'bg-success/10 text-success'
                                        : 'bg-error/10 text-error'
                                    }`}
                                  >
                                    {e.status}
                                  </span>
                                </td>
                                <td className="py-2 px-2 text-right font-mono text-text-secondary">
                                  {e.execution_time_ms}ms
                                </td>
                                <td className="py-2 px-2 text-text-tertiary min-w-[180px] max-w-xs">
                                  {e.input_data ? <JsonView value={e.input_data} title="输入" /> : '—'}
                                </td>
                                <td className="py-2 px-2 text-text-tertiary min-w-[180px] max-w-xs">
                                  {e.error_message ? (
                                    <span className="text-error text-xs">{e.error_message}</span>
                                  ) : e.output_data ? (
                                    <JsonView value={e.output_data} title="输出" />
                                  ) : '—'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </motion.div>
              )
            })}
          </div>
        )}
      </div>
    </Layout>
  )
}
