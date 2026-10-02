/**
 * StrikeTeamsPage — 动态敏捷特遣队编队工作台（AutoTeams 5.0 战役 1）。
 *
 * 设计基线：克制·编辑式（对齐 docs/AutoTeams-UI重设计蓝图_2026-09-26.md）
 * - 白底发丝线容器，不用彩色胶囊标签；状态一律 6px 圆点 + 13px 文字
 * - 成员以 32px 字母牌 + 工号 mono 呈现，不做头像图片外链
 * - 子任务以 DAG 依赖卡呈现，前置未验收者显式标注为「待上游」
 * - 全屏仅 1 枚实心主按钮（发起特遣队）
 *
 * 数据来源严格对齐后端 /api/v1/strike_teams（见 api/strikeTeams.ts）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Plus,
  RefreshCw,
  Users,
  X,
  ShieldCheck,
  Lock,
  CircleCheck,
  Play,
  FileCheck2,
  Swords,
} from 'lucide-react'
import { toast } from 'sonner'
import Layout from '@/components/Layout'
import { ModalShell } from '@/components/ui/ModalShell'
import {
  listStrikeTeams,
  createStrikeTeam,
  submitStrikeTeamBid,
  activateStrikeTeam,
  lockStrikeSubtask,
  deliverStrikeSubtask,
  acceptStrikeSubtask,
  requestStrikeTeamReview,
  dissolveStrikeTeam,
  type StrikeTeam,
  type StrikeTeamStatus,
  type SubtaskNode,
} from '@/api/strikeTeams'

/** 状态 → 中文标签 + 圆点语义（克制：只用中性/成功/警示三色） */
const STATUS_META: Record<StrikeTeamStatus, { label: string; tone: 'ok' | 'warn' | 'idle' }> = {
  forming: { label: '组建竞标中', tone: 'warn' },
  active: { label: '执行中', tone: 'ok' },
  reviewing: { label: '成果验收', tone: 'warn' },
  dissolved: { label: '已解散', tone: 'idle' },
}

/** 子任务状态 → 中文标签 */
const SUBTASK_LABEL: Record<SubtaskNode['status'], string> = {
  pending: '待认领',
  locked: '执行中',
  delivered: '待验收',
  accepted: '已验收',
}

/**
 * 生成 32px 字母牌字符。
 *
 * 工号形如 `ATE-2026-SALES-001`，取首字符会让全员工号都显示「A」而无法区分，
 * 故优先取部门段首字母（SALES→S、TECH→T、FIN→F），退化时才用首字符。
 */
function initial(badge: string): string {
  const parts = badge.trim().split('-').filter(Boolean)
  if (parts.length >= 3) return parts[2].charAt(0).toUpperCase()
  return parts[0]?.charAt(0).toUpperCase() || '?'
}

export default function StrikeTeamsPage() {
  const [teams, setTeams] = useState<StrikeTeam[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showDissolved, setShowDissolved] = useState(false)
  const [busy, setBusy] = useState(false)

  const [createOpen, setCreateOpen] = useState(false)
  const [draft, setDraft] = useState({
    name: '',
    mission_statement: '',
    initiator_badge: '',
    initiator_role: '队长',
    initiator_stake_hp: 20,
    allocated_compute_budget: 100,
  })

  const [bidOpen, setBidOpen] = useState(false)
  const [bidDraft, setBidDraft] = useState({ badge: '', role: '', stake_hp: 20, rationale: '' })

  const [dissolveOpen, setDissolveOpen] = useState(false)
  const [dissolveNote, setDissolveNote] = useState('')
  /** 子任务 id → 认领人工号（锁入时由操作者选择，而非默认取首位成员） */
  const [assignees, setAssignees] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const data = await listStrikeTeams({ include_dissolved: showDissolved })
      setTeams(Array.isArray(data) ? data : [])
    } catch (err) {
      setTeams([])
      setLoadError(err instanceof Error ? err.message : '特遣队数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [showDissolved])

  useEffect(() => {
    void load()
  }, [load])

  const selected = useMemo(
    () => teams.find((t) => t.id === selectedId) ?? teams[0] ?? null,
    [teams, selectedId],
  )

  /** 统一的动作包装：置 busy、调用、toast 反馈、刷新 */
  const run = useCallback(
    async (fn: () => Promise<unknown>, successMessage: string) => {
      setBusy(true)
      try {
        await fn()
        toast.success(successMessage)
        await load()
      } catch (err) {
        toast.error(err instanceof Error ? err.message : '操作失败')
      } finally {
        setBusy(false)
      }
    },
    [load],
  )

  const handleCreate = () => {
    if (!draft.name.trim() || !draft.mission_statement.trim() || !draft.initiator_badge.trim()) {
      toast.error('请填写代号、核心使命与发起人工号')
      return
    }
    void run(async () => {
      const created = await createStrikeTeam({
        name: draft.name.trim(),
        mission_statement: draft.mission_statement.trim(),
        initiator_badge: draft.initiator_badge.trim(),
        initiator_role: draft.initiator_role,
        initiator_stake_hp: draft.initiator_stake_hp,
        allocated_compute_budget: draft.allocated_compute_budget,
      })
      setSelectedId(created.id)
      setCreateOpen(false)
      setDraft({
        name: '',
        mission_statement: '',
        initiator_badge: '',
        initiator_role: '队长',
        initiator_stake_hp: 20,
        allocated_compute_budget: 100,
      })
    }, '特遣队招募已发布')
  }

  const handleBid = () => {
    if (!selected) return
    if (!bidDraft.badge.trim() || !bidDraft.role.trim()) {
      toast.error('请填写竞标人工号与申请角色')
      return
    }
    void run(async () => {
      await submitStrikeTeamBid(selected.id, {
        badge: bidDraft.badge.trim(),
        role: bidDraft.role.trim(),
        stake_hp: bidDraft.stake_hp,
        rationale: bidDraft.rationale,
      })
      setBidOpen(false)
      setBidDraft({ badge: '', role: '', stake_hp: 20, rationale: '' })
    }, '竞标已受理，抵押 HP 计入对赌池')
  }

  /** 前置依赖是否已全部验收（决定能否锁入） */
  const blockingDeps = useCallback(
    (team: StrikeTeam, node: SubtaskNode): string[] => {
      const settled = new Set(
        team.subtask_graph.nodes.filter((n) => n.status === 'accepted').map((n) => n.id),
      )
      return node.depends_on.filter((d) => !settled.has(d))
    },
    [],
  )

  const handleDissolve = (accepted: boolean) => {
    if (!selected) return
    void run(async () => {
      await dissolveStrikeTeam(selected.id, { accepted, settlement_note: dissolveNote })
      setDissolveOpen(false)
      setDissolveNote('')
    }, accepted ? '验收通过，押金已原路返还' : '验收未通过，押金已罚没')
  }

  return (
    <Layout>
      <div className="at-paper min-h-full w-full px-6 md:px-10 py-8">
        <div className="mx-auto w-full max-w-[1280px] space-y-8">
          {/* 抬头：30px 标题 + 13px 副标题 + 全屏唯一实心主按钮 */}
          <header className="flex flex-col gap-4 pb-6 border-b border-border-subtle sm:flex-row sm:items-end sm:justify-between">
            <div className="min-w-0">
              <h1 className="text-[30px] leading-9 font-bold tracking-tight text-text-primary">
                动态敏捷特遣队
              </h1>
              <p className="mt-1 text-body-sm text-text-tertiary">
                感知复杂任务后自主组队 · Contract Net Protocol 2.0 带资竞标 · 任务闭环后清算解散
              </p>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <button
                type="button"
                onClick={() => void load()}
                disabled={loading}
                aria-label="刷新特遣队列表"
                className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary hover:bg-elevated transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
                刷新
              </button>
              <button
                type="button"
                onClick={() => setCreateOpen(true)}
                className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors"
              >
                <Plus className="w-3.5 h-3.5" aria-hidden="true" />
                发起特遣队
              </button>
            </div>
          </header>

          {loadError ? (
            <div role="alert" className="rounded-md border border-error/30 bg-error/5 p-4">
              <p className="text-body-sm text-error">{loadError}</p>
              <button type="button" onClick={() => void load()} className="mt-2 text-body-sm text-brand-500 hover:underline">
                重新加载
              </button>
            </div>
          ) : loading && teams.length === 0 ? (
            <p className="py-20 text-center text-body-sm text-text-muted">正在同步特遣队编队数据…</p>
          ) : teams.length === 0 ? (
            <div className="rounded-[10px] border border-border-default bg-surface px-6 py-16 text-center">
              <Users className="mx-auto mb-3 w-8 h-8 text-text-muted/60" aria-hidden="true" />
              <p className="text-body text-text-primary">当前没有活动特遣队</p>
              <p className="mt-1 text-caption text-text-muted">
                数字员工感知到复杂任务时会自主发起组队，也可从右上角手动发起
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-12 items-start">
              {/* 队伍名录：发丝线列表，不用卡片包裹 */}
              <div className="lg:col-span-4 space-y-3">
                <div className="flex items-center justify-between pb-2 border-b border-border-subtle">
                  <h2 className="text-h4 font-semibold text-text-primary">队伍名录</h2>
                  <label className="flex items-center gap-1.5 text-caption text-text-tertiary cursor-pointer">
                    <input
                      type="checkbox"
                      checked={showDissolved}
                      onChange={(e) => setShowDissolved(e.target.checked)}
                      className="accent-[var(--brand)]"
                    />
                    含已解散
                  </label>
                </div>
                <ul>
                  {teams.map((t) => {
                    const meta = STATUS_META[t.status]
                    const isActive = selected?.id === t.id
                    return (
                      <li key={t.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedId(t.id)}
                          className={`w-full text-left py-3 px-3 -mx-3 border-l-2 transition-colors ${
                            isActive
                              ? 'border-l-brand-500 bg-elevated'
                              : 'border-l-transparent hover:bg-elevated/60'
                          }`}
                          aria-current={isActive}
                        >
                          <div className="flex items-center justify-between gap-3">
                            <span className="truncate text-[15px] font-semibold text-text-primary">
                              {t.name}
                            </span>
                            <span className="flex shrink-0 items-center gap-1.5">
                              <span className={`w-1.5 h-1.5 rounded-full at-dot-${meta.tone}`} aria-hidden="true" />
                              <span className="text-body-sm text-text-tertiary">{meta.label}</span>
                            </span>
                          </div>
                          <p className="mt-1 truncate text-caption text-text-tertiary">
                            发起 {t.initiator_badge} · 成员 {t.members.length} 人
                          </p>
                          <p className="mt-1 text-caption text-text-muted">
                            抵押池 {t.total_hp_stake.toFixed(1)} HP · 算力配额 {t.allocated_compute_budget}
                          </p>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </div>

              {/* 编队详情 */}
              {selected && (
                <div className="lg:col-span-8 space-y-6">
                  <section className="rounded-[10px] border border-border-default bg-surface">
                    <div className="flex flex-col gap-4 px-6 py-5 border-b border-border-subtle sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <h2 className="text-h4 font-semibold text-text-primary truncate">{selected.name}</h2>
                        <p className="mt-1.5 text-body-sm text-text-secondary">{selected.mission_statement}</p>
                        <p className="mt-2 flex flex-wrap items-center gap-2 text-caption text-text-muted">
                          <span>工号</span>
                          <span className="font-mono text-text-secondary">{selected.initiator_badge}</span>
                          <span>·</span>
                          <span>黑板 {selected.shared_blackboard_id}</span>
                        </p>
                      </div>
                      <div className="flex shrink-0 flex-wrap items-center gap-2">
                        {selected.status === 'forming' && (
                          <>
                            <button
                              type="button"
                              onClick={() => setBidOpen(true)}
                              disabled={busy}
                              className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary hover:bg-elevated transition-colors disabled:opacity-50"
                            >
                              <Swords className="w-3.5 h-3.5" aria-hidden="true" />
                              带资竞标
                            </button>
                            <button
                              type="button"
                              onClick={() => void run(() => activateStrikeTeam(selected.id), '资源租约已锁定')}
                              disabled={busy}
                              className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary hover:bg-elevated transition-colors disabled:opacity-50"
                            >
                              <Lock className="w-3.5 h-3.5" aria-hidden="true" />
                              锁定租约
                            </button>
                          </>
                        )}
                        {selected.status === 'active' && (
                          <button
                            type="button"
                            onClick={() => void run(() => requestStrikeTeamReview(selected.id), '已进入成果验收环节')}
                            disabled={busy}
                            className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary hover:bg-elevated transition-colors disabled:opacity-50"
                          >
                            <FileCheck2 className="w-3.5 h-3.5" aria-hidden="true" />
                            申请验收
                          </button>
                        )}
                        {(selected.status === 'active' || selected.status === 'reviewing') && (
                          <button
                            type="button"
                            onClick={() => setDissolveOpen(true)}
                            disabled={busy}
                            className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary hover:bg-elevated transition-colors disabled:opacity-50"
                          >
                            验收并解散
                          </button>
                        )}
                      </div>
                    </div>

                    {/* 成员与抵押：32px 字母牌 + HP 细条 */}
                    <div className="px-6 py-5">
                      <div className="mb-3 flex items-center justify-between pb-2 border-b border-border-subtle">
                        <h3 className="text-h4 font-semibold text-text-primary">成员与带资抵押</h3>
                        <span className="text-caption text-text-tertiary">
                          抵押池合计 {selected.total_hp_stake.toFixed(1)} HP
                        </span>
                      </div>
                      {selected.members.length === 0 ? (
                        <p className="py-3 text-body-sm text-text-muted">
                          尚无成员 —— 数字员工通过「带资竞标」质押 HP 后入队
                        </p>
                      ) : (
                        <ul>
                          {selected.members.map((m) => (
                            <li
                              key={m.badge}
                              className="flex items-center gap-3 py-3 border-b border-border-subtle last:border-b-0"
                            >
                              <span className="w-8 h-8 rounded-full bg-[#EDEEF2] text-[#374151] flex items-center justify-center text-caption font-medium shrink-0 select-none">
                                {initial(m.badge)}
                              </span>
                              <div className="min-w-0 flex-1">
                                <div className="flex items-baseline gap-2">
                                  <span className="font-mono text-body-sm text-text-primary">{m.badge}</span>
                                  <span className="text-caption text-text-muted">{m.role}</span>
                                </div>
                                <div className="mt-1.5 h-[2px] w-full max-w-[220px] rounded-full bg-border-default overflow-hidden">
                                  <div
                                    className="h-full bg-text-primary"
                                    style={{ width: `${Math.min(100, Math.max(0, m.stake_hp))}%` }}
                                  />
                                </div>
                              </div>
                              <div className="shrink-0 text-right">
                                <div className="text-body-sm font-medium text-text-primary tabular-nums">
                                  {m.stake_hp.toFixed(1)} HP
                                </div>
                                {m.settlement && (
                                  <div
                                    className={`text-caption ${m.settlement === 'refunded' ? 'text-success' : 'text-error'}`}
                                  >
                                    {m.settlement === 'refunded' ? '押金已返还' : '押金已罚没'}
                                  </div>
                                )}
                              </div>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>

                    {/* DAG 子任务：显式标注前置依赖是否满足 */}
                    <div className="px-6 py-5 border-t border-border-subtle">
                      <div className="mb-3 flex items-center justify-between pb-2 border-b border-border-subtle">
                        <h3 className="text-h4 font-semibold text-text-primary">子任务依赖图</h3>
                        <span className="text-caption text-text-tertiary">
                          {selected.subtask_graph.nodes.length} 个子任务
                        </span>
                      </div>
                      {selected.subtask_graph.nodes.length === 0 ? (
                        <p className="py-3 text-body-sm text-text-muted">
                          尚未定义子任务 —— 子任务 DAG 在发起特遣队招募时一并定义
                        </p>
                      ) : (
                        <ul>
                          {selected.subtask_graph.nodes.map((node) => {
                            const blocked = blockingDeps(selected, node)
                            const canLock = selected.status === 'active' && node.status === 'pending' && blocked.length === 0
                            return (
                              <li key={node.id} className="py-3 border-b border-border-subtle last:border-b-0">
                                <div className="flex items-start justify-between gap-4">
                                  <div className="min-w-0">
                                    <div className="flex flex-wrap items-baseline gap-2">
                                      <span className="font-mono text-caption text-text-muted">{node.id}</span>
                                      <span className="text-body-sm text-text-primary">{node.title}</span>
                                    </div>
                                    <div className="mt-1 flex flex-wrap items-center gap-2 text-caption text-text-muted">
                                      <span>{SUBTASK_LABEL[node.status]}</span>
                                      {node.assignee_badge && (
                                        <>
                                          <span>·</span>
                                          <span className="font-mono">认领 {node.assignee_badge}</span>
                                        </>
                                      )}
                                      {node.depends_on.length > 0 && (
                                        <>
                                          <span>·</span>
                                          <span>依赖 {node.depends_on.join('、')}</span>
                                        </>
                                      )}
                                    </div>
                                    {blocked.length > 0 && (
                                      <p className="mt-1 text-caption text-warning">待上游：{blocked.join('、')} 尚未验收</p>
                                    )}
                                  </div>
                                  <div className="flex shrink-0 items-center gap-2">
                                    {canLock && (
                                      <div className="flex items-center gap-2">
                                        <label className="sr-only" htmlFor={`assignee-${node.id}`}>
                                          子任务 {node.id} 认领人
                                        </label>
                                        <select
                                          id={`assignee-${node.id}`}
                                          className="h-8 pl-2 pr-1 rounded-md border border-border-default bg-surface text-caption text-text-primary"
                                          value={assignees[node.id] ?? selected.members[0]?.badge ?? ''}
                                          onChange={(e) =>
                                            setAssignees((prev) => ({ ...prev, [node.id]: e.target.value }))
                                          }
                                        >
                                          {selected.members.map((m) => (
                                            <option key={m.badge} value={m.badge}>
                                              {m.badge} · {m.role}
                                            </option>
                                          ))}
                                        </select>
                                        <button
                                          type="button"
                                          onClick={() =>
                                            void run(
                                              () =>
                                                lockStrikeSubtask(
                                                  selected.id,
                                                  node.id,
                                                  assignees[node.id] ?? selected.members[0].badge,
                                                ),
                                              `子任务 ${node.id} 已锁入`,
                                            )
                                          }
                                          disabled={busy}
                                          className="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-caption text-text-primary hover:bg-elevated transition-colors disabled:opacity-50"
                                        >
                                          <Lock className="w-3 h-3" aria-hidden="true" />
                                          锁入
                                        </button>
                                      </div>
                                    )}
                                    {selected.status === 'active' && node.status === 'locked' && (
                                      <button
                                        type="button"
                                        disabled={busy}
                                        onClick={() =>
                                          void run(
                                            () => deliverStrikeSubtask(selected.id, node.id, { summary: `${node.title} 交付物` }),
                                            `子任务 ${node.id} 已提交交付物`,
                                          )
                                        }
                                        className="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-caption text-text-primary hover:bg-elevated transition-colors disabled:opacity-50"
                                      >
                                        <Play className="w-3 h-3" aria-hidden="true" />
                                        提交交付
                                      </button>
                                    )}
                                    {selected.status === 'active' && node.status === 'delivered' && (
                                      <button
                                        type="button"
                                        disabled={busy}
                                        onClick={() =>
                                          void run(() => acceptStrikeSubtask(selected.id, node.id), `子任务 ${node.id} 已验收`)
                                        }
                                        className="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-caption text-text-primary hover:bg-elevated transition-colors disabled:opacity-50"
                                      >
                                        <CircleCheck className="w-3 h-3" aria-hidden="true" />
                                        验收
                                      </button>
                                    )}
                                  </div>
                                </div>
                              </li>
                            )
                          })}
                        </ul>
                      )}
                    </div>
                  </section>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 发起特遣队浮层 */}
        <ModalShell
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          labelledBy="strike-create-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          panelClassName="at-paper w-full max-w-lg rounded-[10px] border border-border-default p-6 shadow-xl"
        >
          <div className="mb-4 flex items-center justify-between">
            <h2 id="strike-create-title" className="text-h4 font-semibold text-text-primary">
              发起特遣队招募
            </h2>
            <button type="button" onClick={() => setCreateOpen(false)} aria-label="关闭" className="p-1 text-text-tertiary hover:text-text-primary">
              <X className="w-4 h-4" aria-hidden="true" />
            </button>
          </div>
          <div className="space-y-3 text-body-sm">
            <div>
              <label htmlFor="st-name" className="mb-1 block text-caption text-text-tertiary">特遣队代号</label>
              <input
                id="st-name"
                className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm text-text-primary"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="如：Alpha-01-海关报关突击队"
              />
            </div>
            <div>
              <label htmlFor="st-mission" className="mb-1 block text-caption text-text-tertiary">核心使命与验收标准</label>
              <textarea
                id="st-mission"
                rows={3}
                className="w-full px-3 py-2 rounded-md border border-border-default bg-surface text-body-sm text-text-primary"
                value={draft.mission_statement}
                onChange={(e) => setDraft({ ...draft, mission_statement: e.target.value })}
                placeholder="如：72 小时内完成 AEO 认证资料预审与报关链路演练"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="st-badge" className="mb-1 block text-caption text-text-tertiary">发起人工号</label>
                <input
                  id="st-badge"
                  className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm font-mono"
                  value={draft.initiator_badge}
                  onChange={(e) => setDraft({ ...draft, initiator_badge: e.target.value })}
                  placeholder="ATE-2026-SALES-001"
                />
              </div>
              <div>
                <label htmlFor="st-role" className="mb-1 block text-caption text-text-tertiary">队内角色</label>
                <input
                  id="st-role"
                  className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm"
                  value={draft.initiator_role}
                  onChange={(e) => setDraft({ ...draft, initiator_role: e.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="st-stake" className="mb-1 block text-caption text-text-tertiary">发起人抵押 HP</label>
                <input
                  id="st-stake"
                  type="number"
                  min={0}
                  max={100}
                  className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm"
                  value={draft.initiator_stake_hp}
                  onChange={(e) => setDraft({ ...draft, initiator_stake_hp: Number(e.target.value) })}
                />
              </div>
              <div>
                <label htmlFor="st-budget" className="mb-1 block text-caption text-text-tertiary">算力配额</label>
                <input
                  id="st-budget"
                  type="number"
                  min={1}
                  className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm"
                  value={draft.allocated_compute_budget}
                  onChange={(e) => setDraft({ ...draft, allocated_compute_budget: Number(e.target.value) })}
                />
              </div>
            </div>
          </div>
          <div className="mt-6 flex justify-end gap-2 pt-4 border-t border-border-subtle">
            <button
              type="button"
              onClick={() => setCreateOpen(false)}
              className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm text-text-primary hover:bg-elevated"
            >
              取消
            </button>
            <button
              type="button"
              onClick={handleCreate}
              disabled={busy}
              className="h-9 px-4 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
            >
              发布招募
            </button>
          </div>
        </ModalShell>

        {/* 带资竞标浮层 */}
        <ModalShell
          open={bidOpen}
          onClose={() => setBidOpen(false)}
          labelledBy="strike-bid-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          panelClassName="at-paper w-full max-w-md rounded-[10px] border border-border-default p-6 shadow-xl"
        >
          <div className="mb-4 flex items-center justify-between">
            <h2 id="strike-bid-title" className="flex items-center gap-2 text-h4 font-semibold text-text-primary">
              <ShieldCheck className="w-4 h-4" aria-hidden="true" />
              带资竞标响应
            </h2>
            <button type="button" onClick={() => setBidOpen(false)} aria-label="关闭" className="p-1 text-text-tertiary hover:text-text-primary">
              <X className="w-4 h-4" aria-hidden="true" />
            </button>
          </div>
          <p className="mb-4 text-body-sm text-text-tertiary">
            抵押 HP 换取入场券：验收通过原路返还，未通过则全额罚没。
          </p>
          <div className="space-y-3 text-body-sm">
            <div>
              <label htmlFor="bid-badge" className="mb-1 block text-caption text-text-tertiary">竞标人工号</label>
              <input
                id="bid-badge"
                className="w-full h-9 px-3 rounded-md border border-border-default bg-surface font-mono"
                value={bidDraft.badge}
                onChange={(e) => setBidDraft({ ...bidDraft, badge: e.target.value })}
                placeholder="ATE-2026-TECH-014"
              />
            </div>
            <div>
              <label htmlFor="bid-role" className="mb-1 block text-caption text-text-tertiary">申请角色</label>
              <input
                id="bid-role"
                className="w-full h-9 px-3 rounded-md border border-border-default bg-surface"
                value={bidDraft.role}
                onChange={(e) => setBidDraft({ ...bidDraft, role: e.target.value })}
                placeholder="如：规则编译器"
              />
            </div>
            <div>
              <label htmlFor="bid-stake" className="mb-1 block text-caption text-text-tertiary">抵押 HP（1–100）</label>
              <input
                id="bid-stake"
                type="number"
                min={1}
                max={100}
                className="w-full h-9 px-3 rounded-md border border-border-default bg-surface"
                value={bidDraft.stake_hp}
                onChange={(e) => setBidDraft({ ...bidDraft, stake_hp: Number(e.target.value) })}
              />
            </div>
            <div>
              <label htmlFor="bid-why" className="mb-1 block text-caption text-text-tertiary">胜任理由</label>
              <textarea
                id="bid-why"
                rows={2}
                className="w-full px-3 py-2 rounded-md border border-border-default bg-surface"
                value={bidDraft.rationale}
                onChange={(e) => setBidDraft({ ...bidDraft, rationale: e.target.value })}
                placeholder="简述可承担该子任务的依据"
              />
            </div>
          </div>
          <div className="mt-6 flex justify-end gap-2 pt-4 border-t border-border-subtle">
            <button
              type="button"
              onClick={() => setBidOpen(false)}
              className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm hover:bg-elevated"
            >
              取消
            </button>
            <button
              type="button"
              onClick={handleBid}
              disabled={busy}
              className="h-9 px-4 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
            >
              出价竞标
            </button>
          </div>
        </ModalShell>

        {/* 验收清算浮层：三行事实（动作 / 影响 / 回滚） */}
        <ModalShell
          open={dissolveOpen}
          onClose={() => setDissolveOpen(false)}
          labelledBy="strike-dissolve-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          panelClassName="at-paper w-full max-w-md rounded-[10px] border border-border-default p-6 shadow-xl"
        >
          <h2 id="strike-dissolve-title" className="mb-4 text-h4 font-semibold text-text-primary">
            交付验收并清算解散
          </h2>
          {selected && (
            <dl className="space-y-3 border-y border-border-subtle py-4 text-body-sm">
              <div className="flex justify-between gap-4">
                <dt className="shrink-0 text-text-tertiary">动作</dt>
                <dd className="text-right text-text-primary">解散 {selected.name} 并清算 {selected.total_hp_stake.toFixed(1)} HP 押金</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="shrink-0 text-text-tertiary">影响</dt>
                <dd className="text-right text-text-secondary">
                  验收通过则押金原路返还；未通过则 {selected.members.length} 名成员押金全额罚没
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="shrink-0 text-text-tertiary">回滚</dt>
                <dd className="text-right text-text-secondary">解散不可逆，需重新组队并再次竞标</dd>
              </div>
            </dl>
          )}
          <div className="mt-4">
            <label htmlFor="dissolve-note" className="mb-1 block text-caption text-text-tertiary">清算说明</label>
            <textarea
              id="dissolve-note"
              rows={2}
              className="w-full px-3 py-2 rounded-md border border-border-default bg-surface text-body-sm"
              value={dissolveNote}
              onChange={(e) => setDissolveNote(e.target.value)}
              placeholder="记录验收结论依据"
            />
          </div>
          <div className="mt-6 flex justify-end gap-2 pt-4 border-t border-border-subtle">
            <button
              type="button"
              onClick={() => setDissolveOpen(false)}
              className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm hover:bg-elevated"
            >
              取消
            </button>
            <button
              type="button"
              onClick={() => handleDissolve(false)}
              disabled={busy}
              className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm text-error hover:bg-elevated transition-colors disabled:opacity-50"
            >
              验收未通过
            </button>
            <button
              type="button"
              onClick={() => handleDissolve(true)}
              disabled={busy}
              className="h-9 px-4 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
            >
              验收通过
            </button>
          </div>
        </ModalShell>
      </div>
    </Layout>
  )
}
