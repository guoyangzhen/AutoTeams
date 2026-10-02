/**
 * WorkgroupKanban — 团队协同看板（多列任务矩阵 + 共享黑板信息流）。
 *
 * 设计基线：AutoTeams UI 重设计蓝图 v2「克制 · 编辑式」
 * - 列容器用浅灰底（bg-elevated）+ 10px 圆角，任务用白底 1px 发丝线卡；选中任务为 3px 主色左标
 * - 右侧共享黑板以发丝线分隔的信息流呈现，条目不加卡片包裹
 * - 全屏仅 1 枚实心主按钮（新建任务）
 *
 * 数据来源严格对齐后端 /api/v1/teams（见 api/teamMatrix.ts）。
 * 竞标裁决与履约验收由 /bidding-arena 承担，本页只负责组队、下发与看板流转。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Users,
  Plus,
  RefreshCw,
  BookOpen,
  Pin,
  ArrowRight,
  X,
  Scale,
  Search,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { ModalShell } from '@/components/ui/ModalShell'
import {
  listWorkgroupTeams,
  createWorkgroupTeam,
  listMatrixTasks,
  createMatrixTask,
  listBlackboardEntries,
  postBlackboardEntry,
  type BlackboardEntry,
  type MatrixTask,
  type MatrixTaskStatus,
  type WorkgroupTeam,
} from '@/api/teamMatrix'
import { listWorkforceProfiles, type WorkforceProfile } from '@/api/workforceProfiles'

interface BoardColumn {
  key: string
  label: string
  hint: string
  statuses: MatrixTaskStatus[]
}

const BOARD_COLUMNS: BoardColumn[] = [
  { key: 'pending', label: '待分派', hint: '刚下发，尚未进入竞价', statuses: ['pending'] },
  { key: 'bidding', label: '竞标中', hint: '候选员工正在陈述与博弈', statuses: ['bidding'] },
  { key: 'in_progress', label: '执行中', hint: '已定标，交付人正在履约', statuses: ['in_progress'] },
  { key: 'review', label: '待验收', hint: '已提交交付报告，等待裁决', statuses: ['review'] },
  {
    key: 'closed',
    label: '已收口',
    hint: '通过验收、退回返工或升级处理',
    statuses: ['done', 'rework', 'escalated'],
  },
]

const STATUS_LABEL: Record<MatrixTaskStatus, string> = {
  pending: '待分派',
  bidding: '竞标中',
  in_progress: '执行中',
  review: '待验收',
  done: '已验收',
  rework: '返工',
  escalated: '已升级',
}

const PRIORITY_LABEL = (p: number) => `P${p}`

export default function WorkgroupKanban() {
  const navigate = useNavigate()
  const [teams, setTeams] = useState<WorkgroupTeam[]>([])
  const [teamId, setTeamId] = useState('')
  const [profiles, setProfiles] = useState<WorkforceProfile[]>([])
  const [tasks, setTasks] = useState<MatrixTask[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  // 共享黑板
  const [entries, setEntries] = useState<BlackboardEntry[]>([])
  const [blackboardLoading, setBlackboardLoading] = useState(false)
  const [topicFilter, setTopicFilter] = useState('')
  const [appliedTopic, setAppliedTopic] = useState('')
  const [composeOpen, setComposeOpen] = useState(false)

  // 新建工作组
  const [teamModalOpen, setTeamModalOpen] = useState(false)
  const [teamName, setTeamName] = useState('')
  const [teamDesc, setTeamDesc] = useState('')
  const [teamLeader, setTeamLeader] = useState('')
  const [teamMembers, setTeamMembers] = useState<string[]>([])

  // 新建任务
  const [taskModalOpen, setTaskModalOpen] = useState(false)
  const [taskTitle, setTaskTitle] = useState('')
  const [taskDesc, setTaskDesc] = useState('')
  const [taskPriority, setTaskPriority] = useState(3)

  // 黑板撰写
  const [entryTopic, setEntryTopic] = useState('')
  const [entryContent, setEntryContent] = useState('')
  const [entryPinned, setEntryPinned] = useState(false)
  const [posting, setPosting] = useState(false)

  const profileMap = useMemo(() => new Map(profiles.map((p) => [p.id, p])), [profiles])
  const team = useMemo(() => teams.find((t) => t.id === teamId) ?? null, [teams, teamId])

  const loadBoards = useCallback(async () => {
    const [profileList, teamList] = await Promise.all([
      listWorkforceProfiles().catch(() => [] as WorkforceProfile[]),
      listWorkgroupTeams().catch(() => [] as WorkgroupTeam[]),
    ])
    setProfiles(profileList ?? [])
    setTeams(teamList ?? [])
    setTeamId((prev) => (prev && (teamList ?? []).some((t) => t.id === prev) ? prev : (teamList ?? [])[0]?.id ?? ''))
  }, [])

  const loadTasks = useCallback(async (targetTeamId: string) => {
    if (!targetTeamId) return
    const list = await listMatrixTasks(targetTeamId).catch(() => [] as MatrixTask[])
    setTasks(list ?? [])
  }, [])

  const loadBlackboard = useCallback(async (targetTeamId: string, topic?: string) => {
    if (!targetTeamId) return
    setBlackboardLoading(true)
    try {
      const list = await listBlackboardEntries(targetTeamId, topic?.trim() || undefined)
      setEntries(list ?? [])
    } finally {
      setBlackboardLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    loadBoards()
      .catch((err: Error) => {
        if (!cancelled) toast.error(`加载工作组失败：${err.message || '未知错误'}`)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [loadBoards])

  useEffect(() => {
    const timer = window.setTimeout(() => setAppliedTopic(topicFilter.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [topicFilter])

  useEffect(() => {
    if (!teamId) return
    // 切换工作组/主题时先清空旧条目：避免加载期间短暂显示上一组的黑板内容
    setEntries([])
    void loadTasks(teamId)
    void loadBlackboard(teamId, appliedTopic)
  }, [teamId, appliedTopic, loadTasks, loadBlackboard])

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await loadBoards()
      if (teamId) {
        await Promise.all([loadTasks(teamId), loadBlackboard(teamId, appliedTopic)])
      }
    } finally {
      setRefreshing(false)
    }
  }

  const handleCreateTeam = async () => {
    if (!teamName.trim() || !teamLeader) {
      toast.error('请填写工作组名称并指定带队数字员工')
      return
    }
    try {
      const created = await createWorkgroupTeam({
        name: teamName.trim(),
        description: teamDesc.trim() || undefined,
        leader_profile_id: teamLeader,
        member_profile_ids: teamMembers.length > 0 ? teamMembers : [teamLeader],
      })
      toast.success(`工作组「${created.name}」已组建`)
      setTeamModalOpen(false)
      setTeamName('')
      setTeamDesc('')
      setTeamLeader('')
      setTeamMembers([])
      await loadBoards()
      setTeamId(created.id)
    } catch (err) {
      toast.error(`创建工作组失败：${(err as Error).message || '未知错误'}`)
    }
  }

  const handleCreateTask = async () => {
    if (!teamId) {
      toast.error('请先选择工作组')
      return
    }
    if (!taskTitle.trim() || !taskDesc.trim()) {
      toast.error('请填写任务标题与验收标准')
      return
    }
    try {
      await createMatrixTask(teamId, {
        title: taskTitle.trim(),
        description: taskDesc.trim(),
        priority: taskPriority,
      })
      toast.success('协同任务已下发，进入候选员工竞价池')
      setTaskModalOpen(false)
      setTaskTitle('')
      setTaskDesc('')
      setTaskPriority(3)
      await loadTasks(teamId)
    } catch (err) {
      toast.error(`下发任务失败：${(err as Error).message || '未知错误'}`)
    }
  }

  const handlePostEntry = async () => {
    if (!teamId || !entryTopic.trim() || !entryContent.trim()) {
      toast.error('请填写主题与共享内容')
      return
    }
    setPosting(true)
    try {
      await postBlackboardEntry(teamId, {
        topic: entryTopic.trim(),
        content: entryContent.trim(),
        source_profile_id: team?.leader_profile_id || profiles[0]?.id || '',
        is_pinned: entryPinned,
      })
      toast.success('共识已广播至团队黑板')
      setComposeOpen(false)
      setEntryTopic('')
      setEntryContent('')
      setEntryPinned(false)
      await loadBlackboard(teamId, appliedTopic)
    } catch (err) {
      toast.error(`广播失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setPosting(false)
    }
  }

  const openArena = (task: MatrixTask) => {
    navigate(`/bidding-arena?team=${encodeURIComponent(teamId)}&task=${encodeURIComponent(task.id)}`)
  }

  const columnTasks = (col: BoardColumn) => tasks.filter((t) => col.statuses.includes(t.status))

  return (
    <Layout>
      <div className="at-paper min-h-full w-full px-6 md:px-10 py-8">
        <div className="w-full max-w-[1280px] mx-auto space-y-6">
        <header className="flex items-start justify-between gap-6 flex-wrap pb-6 border-b border-border-subtle">
          <div className="min-w-0">
            <h1 className="text-[30px] leading-9 font-bold tracking-tight text-text-primary truncate">
              {team?.name ?? '团队协同看板'}
            </h1>
            <div className="mt-2 flex items-center flex-wrap gap-2 text-body-sm text-text-tertiary">
              <span>负责人</span>
              <span className="font-mono text-caption text-text-primary">
                {team ? profileMap.get(team.leader_profile_id)?.employee_badge ?? '未指定' : '—'}
              </span>
              <span className="text-border-default">·</span>
              <span>成员 {team?.member_profile_ids.length ?? 0} 人</span>
              <span className="text-border-default">·</span>
              <span>竞标 3 轮</span>
              <span className="text-border-default">·</span>
              <span className="inline-flex items-center gap-1.5">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    team?.status === 'active' ? 'bg-success' : 'bg-text-muted'
                  }`}
                  aria-hidden="true"
                />
                {team?.status === 'active' ? '编制在运行' : (team?.status ?? '未组建')}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setTaskModalOpen(true)}
              disabled={!teamId}
              className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
            >
              <Plus className="w-3.5 h-3.5" aria-hidden="true" />
              新建任务
            </button>
          </div>
        </header>

        {/* ============ 2. 工作组切换条 ============ */}
        <div className="flex items-center gap-3 flex-wrap">
          <label htmlFor="team-switch" className="text-body-sm text-text-tertiary">
            工作组
          </label>
          <select
            id="team-switch"
            value={teamId}
            onChange={(e) => setTeamId(e.target.value)}
            className="h-9 min-w-[200px] px-2.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
          >
            {teams.length === 0 ? <option value="">暂无工作组</option> : null}
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>

          <button
            type="button"
            onClick={() => setTeamModalOpen(true)}
            className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
          >
            <Users className="w-3.5 h-3.5" aria-hidden="true" />
            组建工作组
          </button>

          <div className="flex-1" />

          <span className="font-mono text-caption text-text-tertiary">
            任务 {tasks.length} 项
          </span>
          <button
            type="button"
            onClick={handleRefresh}
            className="w-9 h-9 inline-flex items-center justify-center rounded-md border border-border-default bg-surface text-text-tertiary hover:bg-elevated hover:text-text-primary transition-colors"
            aria-label="刷新看板"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing || loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          </button>
        </div>

        {/* ============ 3. 看板 + 共享黑板信息流 ============ */}
        {teams.length === 0 && !loading ? (
          <div className="rounded-[10px] border border-border-default bg-surface px-6 py-16 text-center">
            <Users className="w-7 h-7 mx-auto text-text-muted mb-3" aria-hidden="true" />
            <p className="text-h4 font-semibold text-text-primary">还没有任何工作组</p>
            <p className="mt-1.5 text-body-sm text-text-tertiary">
              工作组是数字员工组队交付的最小单元：指定带队人、圈定成员编制，再向其下发任务。
            </p>
            <button
              type="button"
              onClick={() => setTeamModalOpen(true)}
              className="mt-5 h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" aria-hidden="true" />
              组建第一个工作组
            </button>
          </div>
        ) : (
          <div className="flex gap-8 items-start">
            {/* 任务列 */}
            <div className="flex-1 min-w-0 grid grid-cols-2 xl:grid-cols-5 gap-4">
              {BOARD_COLUMNS.map((col) => {
                const items = columnTasks(col)
                return (
                  <section
                    key={col.key}
                    className="rounded-[10px] bg-white border border-border-default p-3 flex flex-col gap-3 min-h-[420px]"
                    aria-label={col.label}
                  >
                    <div className="flex items-center justify-between px-1">
                      <span className="text-body-sm font-medium text-text-tertiary">{col.label}</span>
                      <span className="font-mono text-caption text-text-tertiary px-1.5 py-0.5 rounded-[4px] border border-border-default bg-surface">
                        {items.length}
                      </span>
                    </div>
                    <p className="px-1 -mt-1.5 text-[11px] leading-4 text-text-muted">{col.hint}</p>

                    <div className="flex flex-col gap-2.5">
                      {items.length === 0 ? (
                        <p className="py-8 text-center text-caption text-text-muted">
                          本列暂无任务
                        </p>
                      ) : (
                        items.map((task) => {
                          const assignee = task.assignee_profile_id
                            ? profileMap.get(task.assignee_profile_id)
                            : null
                          return (
                            <button
                              key={task.id}
                              type="button"
                              onClick={() => openArena(task)}
                              className="text-left bg-surface border border-border-default rounded-[6px] p-3 hover:border-border-strong transition-colors"
                            >
                              <span className="block text-[14px] leading-6 font-medium text-text-primary">
                                {task.title}
                              </span>
                              <span className="mt-2 flex items-center justify-between gap-2 text-caption text-text-tertiary">
                                <span className="font-mono truncate">
                                  {task.id.slice(0, 8)}
                                </span>
                                <span className="flex items-center gap-2 flex-shrink-0">
                                  <span>{PRIORITY_LABEL(task.priority)}</span>
                                  <span className="text-border-default">|</span>
                                  <span className="inline-flex items-center gap-1.5">
                                    <span
                                      className={`w-1.5 h-1.5 rounded-full ${
                                        task.status === 'done'
                                          ? 'bg-[#1F7A4D]'
                                          : task.status === 'review' || task.status === 'bidding'
                                            ? 'bg-[#9A6212]'
                                            : task.status === 'escalated' || task.status === 'rework'
                                              ? 'bg-[#B23A2F]'
                                              : 'bg-[#1F4FD8]'
                                      }`}
                                      aria-hidden="true"
                                    />
                                    <span>{STATUS_LABEL[task.status]}</span>
                                  </span>
                                </span>
                              </span>
                              <span className="mt-2 pt-2 border-t border-border-subtle flex items-center justify-between gap-2 text-caption">
                                <span className="flex items-center gap-1.5 min-w-0">
                                  <span className="w-7 h-7 rounded-full bg-[#EDEEF2] text-[#374151] flex items-center justify-center text-xs font-medium flex-shrink-0 select-none">
                                    {assignee ? assignee.display_name.slice(0, 1) : '—'}
                                  </span>
                                  <span className="truncate text-text-tertiary">
                                    {assignee?.display_name ?? '待定标'}
                                  </span>
                                </span>
                                <ArrowRight
                                  className="w-3 h-3 text-text-muted flex-shrink-0"
                                  aria-hidden="true"
                                />
                              </span>
                            </button>
                          )
                        })
                      )}
                    </div>
                  </section>
                )
              })}
            </div>

            {/* 共享黑板信息流 */}
            <aside className="w-[280px] shrink-0 border-l border-border-subtle pl-6">
              <div className="flex items-center justify-between gap-2 mb-1">
                <h2 className="text-h4 font-semibold text-text-primary">共享黑板</h2>
                <button
                  type="button"
                  onClick={() => setComposeOpen(true)}
                  disabled={!teamId}
                  className="h-8 px-2.5 inline-flex items-center gap-1 rounded-md border border-border-default bg-surface text-caption text-text-secondary hover:bg-elevated transition-colors disabled:opacity-50"
                >
                  <Plus className="w-3 h-3" aria-hidden="true" />
                  记录共识
                </button>
              </div>
              <p className="text-caption text-text-tertiary mb-3">
                团队内全员可见的活文档：口径、对齐结论与关键事实。
              </p>

              <div className="relative mb-2">
                <Search
                  className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-text-muted"
                  aria-hidden="true"
                />
                <input
                  type="text"
                  value={topicFilter}
                  onChange={(e) => setTopicFilter(e.target.value)}
                  placeholder="按主题过滤…"
                  className="w-full h-8 pl-8 pr-2.5 rounded-md border border-border-default bg-surface text-caption text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500"
                />
              </div>

              {blackboardLoading && entries.length === 0 ? (
                <p className="py-8 text-center text-caption text-text-muted">正在同步黑板…</p>
              ) : entries.length === 0 ? (
                <div className="py-10 text-center">
                  <Users className="w-5 h-5 mx-auto mb-2 text-text-muted/60" aria-hidden="true" />
                  <p className="text-body-sm text-text-secondary">黑板还是空的</p>
                  <p className="mt-1.5 text-caption text-text-muted leading-relaxed px-2">
                    黑板是工作组共享的协同信息流：把阶段共识、客户约束、风险提醒广播到这里，
                    全组成员都能看到。点击上方「记录共识」写下第一条。
                  </p>
                </div>
              ) : (
                <div className="divide-y divide-border-subtle">
                  {entries.map((entry) => {
                    const author = profileMap.get(entry.source_profile_id)
                    const time = entry.updated_at
                      ? entry.updated_at.replace('T', ' ').slice(5, 16)
                      : ''
                    return (
                      <article key={entry.id} className="py-3.5">
                        <h3 className="text-[14px] font-medium leading-6 text-text-primary flex items-start gap-1.5">
                          {entry.is_pinned ? (
                            <Pin
                              className="w-3 h-3 mt-1.5 text-brand-500 flex-shrink-0"
                              aria-label="已置顶"
                            />
                          ) : null}
                          <span className="min-w-0">{entry.topic}</span>
                        </h3>
                        <p className="mt-1.5 text-body-sm leading-6 text-text-secondary whitespace-pre-wrap">
                          {entry.content}
                        </p>
                        <div className="mt-1.5 font-mono text-caption text-text-muted flex items-center gap-2 flex-wrap">
                          <span>{author?.department ?? '团队'}</span>
                          <span className="text-border-default">·</span>
                          <span>{author?.employee_badge ?? entry.source_profile_id.slice(0, 8)}</span>
                          {time ? (
                            <>
                              <span className="text-border-default">·</span>
                              <span>{time}</span>
                            </>
                          ) : null}
                          {entry.citations?.length ? (
                            <>
                              <span className="text-border-default">·</span>
                              <span>引用 {entry.citations.length}</span>
                            </>
                          ) : null}
                        </div>
                      </article>
                    )
                  })}
                </div>
              )}
            </aside>
          </div>
        )}

        {/* ============ 竞标入口说明 ============ */}
        {tasks.some((t) => t.status === 'bidding') && (
          <section className="rounded-[10px] border border-border-default bg-surface px-5 py-4 flex items-center justify-between gap-4 flex-wrap">
            <p className="text-body-sm text-text-secondary flex items-center gap-2 min-w-0">
              <Scale className="w-4 h-4 text-brand-500 flex-shrink-0" aria-hidden="true" />
              有任务正在竞标 —— 候选员工的 HP 衰减、三轮陈述与终局加权裁决在竞标台完成。
            </p>
            <Link
              to={`/bidding-arena?team=${encodeURIComponent(teamId)}`}
              className="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-caption text-text-secondary hover:bg-elevated transition-colors flex-shrink-0"
            >
              <BookOpen className="w-3 h-3" aria-hidden="true" />
              前往竞标台
            </Link>
          </section>
        )}

        {/* ============ 组建工作组 ============ */}
        <ModalShell
          open={teamModalOpen}
          onClose={() => setTeamModalOpen(false)}
          labelledBy="team-modal-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 overflow-y-auto"
          panelClassName="w-full max-w-lg rounded-[10px] border border-border-default bg-surface shadow-lift my-8"
        >
          <div className="p-6 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <h2 id="team-modal-title" className="text-h4 font-semibold text-text-primary">
                组建工作组
              </h2>
              <button
                type="button"
                onClick={() => setTeamModalOpen(false)}
                aria-label="关闭"
                className="w-8 h-8 flex items-center justify-center rounded-md text-text-tertiary hover:bg-elevated transition-colors"
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label htmlFor="team-name" className="block text-body-sm text-text-secondary mb-1.5">
                  工作组名称
                </label>
                <input
                  id="team-name"
                  type="text"
                  value={teamName}
                  onChange={(e) => setTeamName(e.target.value)}
                  placeholder="例如：大客户交付攻坚组"
                  className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500"
                />
              </div>

              <div>
                <label htmlFor="team-desc" className="block text-body-sm text-text-secondary mb-1.5">
                  协同目标
                </label>
                <textarea
                  id="team-desc"
                  rows={2}
                  value={teamDesc}
                  onChange={(e) => setTeamDesc(e.target.value)}
                  placeholder="该工作组在矩阵组织中承担的交付职责与权责边界…"
                  className="w-full p-3 rounded-md border border-border-default bg-surface text-[14px] leading-6 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500 resize-y"
                />
              </div>

              <div>
                <label
                  htmlFor="team-leader"
                  className="block text-body-sm text-text-secondary mb-1.5"
                >
                  带队数字员工
                </label>
                <select
                  id="team-leader"
                  value={teamLeader}
                  onChange={(e) => {
                    setTeamLeader(e.target.value)
                    setTeamMembers((prev) =>
                      prev.includes(e.target.value) ? prev : [...prev, e.target.value],
                    )
                  }}
                  className="w-full h-9 px-2.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                >
                  <option value="">请选择…</option>
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name} · {p.job_title}（{p.employee_badge}）
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <span className="block text-body-sm text-text-secondary mb-1.5">成员编制</span>
                <div className="max-h-40 overflow-y-auto scrollbar-thin rounded-md border border-border-default divide-y divide-border-subtle">
                  {profiles.length === 0 ? (
                    <p className="px-3 py-3 text-caption text-text-muted">暂无可选数字员工</p>
                  ) : (
                    profiles.map((p) => {
                      const checked = teamMembers.includes(p.id)
                      return (
                        <label
                          key={p.id}
                          className="flex items-center gap-3 px-3 py-2.5 cursor-pointer hover:bg-elevated transition-colors"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) =>
                              setTeamMembers((prev) =>
                                e.target.checked
                                  ? [...prev, p.id]
                                  : prev.filter((id) => id !== p.id),
                              )
                            }
                            className="w-3.5 h-3.5 accent-[#1E3A5F]"
                          />
                          <span className="w-6 h-6 rounded-full border border-border-default bg-elevated flex items-center justify-center font-mono text-[10px] text-text-secondary flex-shrink-0">
                            {p.display_name.slice(0, 1)}
                          </span>
                          <span className="text-body-sm text-text-primary truncate">
                            {p.display_name}
                          </span>
                          <span className="ml-auto font-mono text-caption text-text-muted truncate">
                            {p.employee_badge} · {p.job_title}
                          </span>
                        </label>
                      )
                    })
                  )}
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setTeamModalOpen(false)}
                className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleCreateTeam}
                className="h-9 px-4 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors"
              >
                确认组建
              </button>
            </div>
          </div>
        </ModalShell>

        {/* ============ 下发任务 ============ */}
        <ModalShell
          open={taskModalOpen}
          onClose={() => setTaskModalOpen(false)}
          labelledBy="task-modal-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          panelClassName="w-full max-w-lg rounded-[10px] border border-border-default bg-surface shadow-lift"
        >
          <div className="p-6 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <h2 id="task-modal-title" className="text-h4 font-semibold text-text-primary">
                下发协同任务
              </h2>
              <button
                type="button"
                onClick={() => setTaskModalOpen(false)}
                aria-label="关闭"
                className="w-8 h-8 flex items-center justify-center rounded-md text-text-tertiary hover:bg-elevated transition-colors"
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label htmlFor="task-title" className="block text-body-sm text-text-secondary mb-1.5">
                  任务标题
                </label>
                <input
                  id="task-title"
                  type="text"
                  value={taskTitle}
                  onChange={(e) => setTaskTitle(e.target.value)}
                  placeholder="例如：完成 XX 集团 2026 年度投标方案"
                  className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500"
                />
              </div>
              <div>
                <label htmlFor="task-desc" className="block text-body-sm text-text-secondary mb-1.5">
                  目标输出与验收标准
                </label>
                <textarea
                  id="task-desc"
                  rows={4}
                  value={taskDesc}
                  onChange={(e) => setTaskDesc(e.target.value)}
                  placeholder="阐明交付件构成、引用来源要求与通过标准…"
                  className="w-full p-3 rounded-md border border-border-default bg-surface text-[14px] leading-6 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500 resize-y"
                />
              </div>
              <div>
                <label
                  htmlFor="task-priority"
                  className="block text-body-sm text-text-secondary mb-1.5"
                >
                  优先级（1-5，数值越大越靠前）
                </label>
                <input
                  id="task-priority"
                  type="number"
                  min={1}
                  max={5}
                  value={taskPriority}
                  onChange={(e) => setTaskPriority(Number(e.target.value))}
                  className="w-28 h-9 px-3 rounded-md border border-border-default bg-surface font-mono text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setTaskModalOpen(false)}
                className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleCreateTask}
                className="h-9 px-4 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors"
              >
                投递竞标池
              </button>
            </div>
          </div>
        </ModalShell>

        {/* ============ 黑板撰写 ============ */}
        <ModalShell
          open={composeOpen}
          onClose={() => setComposeOpen(false)}
          labelledBy="bb-modal-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          panelClassName="w-full max-w-lg rounded-[10px] border border-border-default bg-surface shadow-lift"
        >
          <div className="p-6 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="bb-modal-title" className="text-h4 font-semibold text-text-primary">
                  记录团队共识
                </h2>
                <p className="mt-1 text-body-sm text-text-tertiary">
                  以「{team?.leader_profile_id ? profileMap.get(team.leader_profile_id)?.display_name ?? '带队人' : '带队人'}」身份发布。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setComposeOpen(false)}
                aria-label="关闭"
                className="w-8 h-8 flex items-center justify-center rounded-md text-text-tertiary hover:bg-elevated transition-colors"
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
            </div>

            <div className="space-y-3">
              <input
                type="text"
                value={entryTopic}
                onChange={(e) => setEntryTopic(e.target.value)}
                placeholder="主题，例如：合规免责条款第 14 修正案"
                className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500"
              />
              <textarea
                rows={5}
                value={entryContent}
                onChange={(e) => setEntryContent(e.target.value)}
                placeholder="对齐后的口径、结论或需要全员知晓的关键事实…"
                className="w-full p-3 rounded-md border border-border-default bg-surface text-[14px] leading-6 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500 resize-y"
              />
              <label className="flex items-center gap-2 text-body-sm text-text-secondary cursor-pointer">
                <input
                  type="checkbox"
                  checked={entryPinned}
                  onChange={(e) => setEntryPinned(e.target.checked)}
                  className="w-3.5 h-3.5 accent-[#1E3A5F]"
                />
                置顶为关键事实
              </label>
            </div>

            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setComposeOpen(false)}
                className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handlePostEntry}
                disabled={posting || !entryTopic.trim() || !entryContent.trim()}
                className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
              >
                <BookOpen className="w-3.5 h-3.5" aria-hidden="true" />
                {posting ? '广播中…' : '广播至黑板'}
              </button>
            </div>
          </div>
        </ModalShell>
        </div>
      </div>
    </Layout>
  )
}
