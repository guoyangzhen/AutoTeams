/**
 * BiddingArena — 岗位竞标与验收台（三轮竞标裁决 · 加权对比 · 履约报告）。
 *
 * 设计基线：AutoTeams UI 重设计蓝图 v2「克制 · 编辑式」
 * - 主导区为「候选人竞标对比」数据表：行高 48px、仅底部发丝线、无斑马纹
 * - HP 用 1-2px 细线计量，状态一律 6px 圆点 + 13px 文字
 * - 全屏仅 1 枚实心主按钮（确认中标）
 *
 * 数据来源严格对齐后端 /api/v1/teams（见 api/teamMatrix.ts）：
 * bid / award / deliver / review 均走真实接口，HP 取接口结算返回值。
 * 后端当前未开放竞标记录查询端点，因此对比表中的 HP 与打分列仅呈现
 * 本次会话内经接口结算的真实轮次，未提交轮次的候选人显示「—」。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Check,
  X,
  Scale,
  Send,
  FileCheck2,
  RefreshCw,
  CornerDownRight,
  AlertTriangle,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { ModalShell } from '@/components/ui/ModalShell'
import {
  listWorkgroupTeams,
  listMatrixTasks,
  submitCandidateBid,
  awardTask,
  deliverTask,
  reviewTask,
  type MatrixTask,
  type WorkgroupTeam,
} from '@/api/teamMatrix'
import { listWorkforceProfiles, type WorkforceProfile } from '@/api/workforceProfiles'

const BID_ROUNDS = 3
const HP_WEIGHT = 0.7
const PERF_WEIGHT = 0.3

/** 工号统一 ATE- 前缀 */
function formatBadge(badge: string | null | undefined, fallbackId?: string): string {
  if (!badge) return fallbackId ? `ATE-${fallbackId.slice(0, 4).toUpperCase()}` : 'ATE-2026-001'
  if (badge.startsWith('ATE-')) return badge
  return `ATE-${badge}`
}

/** 一条已由后端结算的竞标轮次。 */
interface SettledBid {
  key: string
  taskId: string
  candidateProfileId: string
  round: number
  score: number
  hp: number
  statement: string
  awarded: boolean
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待分派',
  bidding: '竞标中',
  in_progress: '执行中',
  review: '待验收',
  done: '已验收',
  rework: '返工中',
  escalated: '已升级',
}

function StatusDot({ tone, label }: { tone: 'success' | 'warning' | 'brand' | 'muted'; label: string }) {
  const color =
    tone === 'success'
      ? 'bg-success'
      : tone === 'warning'
        ? 'bg-warning'
        : tone === 'brand'
          ? 'bg-brand-500'
          : 'bg-text-muted'
  return (
    <span className="inline-flex items-center gap-2 text-body-sm text-text-tertiary whitespace-nowrap">
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${color}`} aria-hidden="true" />
      {label}
    </span>
  )
}

/** 1-2px 细线计量条（HP / 绩效），无数值刻度。 */
function Meter({ value, lead }: { value: number | null; lead?: boolean }) {
  if (value === null) {
    return <span className="font-mono text-caption text-text-muted">—</span>
  }
  const pct = Math.max(0, Math.min(100, value))
  return (
    <span className="inline-flex items-center gap-2.5">
      <span className="w-20 h-[2px] rounded-full bg-elevated overflow-hidden">
        <span
          className={`block h-full ${lead ? 'bg-brand-500' : 'bg-text-primary'}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className={`font-mono text-caption ${lead ? 'text-text-primary font-semibold' : 'text-text-primary'}`}>
        {pct.toFixed(0)}%
      </span>
    </span>
  )
}

export default function BiddingArena() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [teams, setTeams] = useState<WorkgroupTeam[]>([])
  const [profiles, setProfiles] = useState<WorkforceProfile[]>([])
  const [tasks, setTasks] = useState<MatrixTask[]>([])
  const [bids, setBids] = useState<SettledBid[]>([])
  const [loading, setLoading] = useState(true)

  const [bidOpen, setBidOpen] = useState(false)
  const [bidCandidate, setBidCandidate] = useState('')
  const [bidRound, setBidRound] = useState(1)
  const [bidStatement, setBidStatement] = useState('')
  const [bidScore, setBidScore] = useState(8.5)
  const [bidRationale, setBidRationale] = useState('')
  const [bidding, setBidding] = useState(false)

  const [deliverOpen, setDeliverOpen] = useState(false)
  const [deliverSummary, setDeliverSummary] = useState('')
  const [deliverArtifacts, setDeliverArtifacts] = useState('')
  const [delivering, setDelivering] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  /** 任务列表接口不返回 review_feedback，此处保留本次会话提交的验收结论用于回显。 */
  const [localReview, setLocalReview] = useState<Record<string, unknown> | null>(null)

  const teamId = searchParams.get('team') ?? ''
  const taskId = searchParams.get('task') ?? ''

  const profileMap = useMemo(() => new Map(profiles.map((p) => [p.id, p])), [profiles])
  const team = useMemo(() => teams.find((t) => t.id === teamId) ?? null, [teams, teamId])
  const task = useMemo(() => tasks.find((t) => t.id === taskId) ?? null, [tasks, taskId])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([
      listWorkgroupTeams().catch(() => [] as WorkgroupTeam[]),
      listWorkforceProfiles().catch(() => [] as WorkforceProfile[]),
    ])
      .then(([teamList, profileList]) => {
        if (cancelled) return
        setTeams(teamList ?? [])
        setProfiles(profileList ?? [])
        if (!teamId && (teamList ?? []).length > 0) {
          const next = new URLSearchParams(searchParams)
          next.set('team', (teamList ?? [])[0].id)
          setSearchParams(next, { replace: true })
        }
      })
      .catch((err: Error) => {
        if (!cancelled) toast.error(`加载竞标台数据失败：${err.message || '未知错误'}`)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadTasks = useCallback(async (targetTeamId: string) => {
    if (!targetTeamId) {
      setTasks([])
      return
    }
    const list = await listMatrixTasks(targetTeamId).catch(() => [] as MatrixTask[])
    setTasks(list ?? [])
  }, [])

  useEffect(() => {
    void loadTasks(teamId)
  }, [teamId, loadTasks])

  // 切换任务时清空本会话的轮次记录与验收结论
  useEffect(() => {
    setBids([])
    setLocalReview(null)
  }, [taskId])

  // 任务选择：默认取该组内第一个未收口任务
  useEffect(() => {
    if (!tasks.length) return
    if (taskId && tasks.some((t) => t.id === taskId)) return
    const first = tasks.find((t) => t.status === 'bidding') ?? tasks.find((t) => t.status === 'pending') ?? tasks[0]
    const next = new URLSearchParams(searchParams)
    next.set('team', teamId)
    next.set('task', first.id)
    setSearchParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks, teamId])

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next)
  }

  const taskBids = useMemo(() => bids.filter((b) => b.taskId === taskId), [bids, taskId])
  const lastRound = useMemo(
    () => (taskBids.length > 0 ? Math.max(...taskBids.map((b) => b.round)) : 1),
    [taskBids],
  )
  const activeRound = Math.min(lastRound, BID_ROUNDS)

  /** 每个候选人的最新结算轮次（HP 取后端返回的 current_hp）。 */
  const settledByCandidate = useMemo(() => {
    const map = new Map<string, SettledBid>()
    for (const bid of taskBids) {
      const prev = map.get(bid.candidateProfileId)
      if (!prev || bid.round >= prev.round) map.set(bid.candidateProfileId, bid)
    }
    return map
  }, [taskBids])

  const candidates = useMemo(() => {
    const ids = team?.member_profile_ids ?? []
    return ids
      .map((id) => profileMap.get(id))
      .filter((p): p is WorkforceProfile => Boolean(p))
  }, [team, profileMap])

  /** 终局加权裁决：FinalScore = HP × 0.7 + 绩效 × 0.3（对齐后端 BiddingEngine）。 */
  const rows = useMemo(() => {
    return candidates.map((profile) => {
      const settled = settledByCandidate.get(profile.id) ?? null
      const perf = Number(profile.performance_score ?? 0)
      const hp = settled ? settled.hp : null
      const composite = hp === null ? null : hp * HP_WEIGHT + perf * PERF_WEIGHT
      return {
        profile,
        settled,
        perf,
        hp,
        composite,
        eliminated: hp !== null && hp <= 0,
        isAssignee: task?.assignee_profile_id === profile.id,
      }
    })
  }, [candidates, settledByCandidate, task])

  const leaderId = useMemo(() => {
    const scored = rows.filter((r) => r.composite !== null && !r.eliminated)
    if (scored.length === 0) return null
    return scored.reduce((best, r) => ((r.composite ?? 0) > (best.composite ?? 0) ? r : best)).profile.id
  }, [rows])

  const handleSubmitBid = async () => {
    if (!teamId || !taskId) return
    if (!bidCandidate) {
      toast.error('请选择竞标候选员工')
      return
    }
    if (!bidStatement.trim()) {
      toast.error('请填写本轮方案陈述')
      return
    }
    setBidding(true)
    try {
      const result = await submitCandidateBid(teamId, taskId, {
        candidate_profile_id: bidCandidate,
        bid_round: bidRound,
        statement: bidStatement.trim(),
        score: bidScore,
        score_rationale: bidRationale.trim() || undefined,
      })
      setBids((prev) => [
        ...prev,
        {
          key: `${result.id}`,
          taskId,
          candidateProfileId: bidCandidate,
          round: result.round,
          score: bidScore,
          hp: result.current_hp,
          statement: result.statement,
          awarded: false,
        },
      ])
      setBidOpen(false)
      setBidStatement('')
      setBidRationale('')
      await loadTasks(teamId)
      toast.success(
        result.current_hp <= 0
          ? `第 ${result.round} 轮已结算，HP 归零 —— 该候选人退出竞价`
          : `第 ${result.round} 轮已结算，剩余 HP ${result.current_hp}`,
      )
    } catch (err) {
      toast.error(`竞标提交失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setBidding(false)
    }
  }

  const handleAward = async (profileId: string) => {
    if (!teamId || !taskId) return
    try {
      await awardTask(teamId, taskId, profileId)
      setBids((prev) => prev.map((b) => ({ ...b, awarded: b.candidateProfileId === profileId })))
      const name = profileMap.get(profileId)?.display_name ?? '该数字员工'
      toast.success(`定标完成，任务已指派给「${name}」`)
      await loadTasks(teamId)
    } catch (err) {
      toast.error(`定标失败：${(err as Error).message || '未知错误'}`)
    }
  }

  const handleDeliver = async () => {
    if (!teamId || !taskId) return
    if (!deliverSummary.trim()) {
      toast.error('请填写履约摘要')
      return
    }
    setDelivering(true)
    try {
      await deliverTask(teamId, taskId, {
        summary: deliverSummary.trim(),
        artifacts: deliverArtifacts
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean),
        submitted_by: task?.assignee_profile_id ?? null,
      })
      setDeliverOpen(false)
      setDeliverSummary('')
      setDeliverArtifacts('')
      await loadTasks(teamId)
      toast.success('履约报告已提交，转入 Leader 验收')
    } catch (err) {
      toast.error(`提交失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setDelivering(false)
    }
  }

  const handleReview = async (approved: boolean) => {
    if (!teamId || !taskId) return
    setReviewing(true)
    const verdict = approved
      ? { verdict: '通过', note: '交付物与前置标准一致，予以采纳。' }
      : { verdict: '退回', note: '交付物存在缺口，退回返工后重新提交。' }
    try {
      await reviewTask(teamId, taskId, { approved, feedback: verdict })
      setLocalReview(verdict)
      await loadTasks(teamId)
      toast.success(approved ? '验收通过，任务已收口' : '已退回返工')
    } catch (err) {
      toast.error(`验收失败：${(err as Error).message || '未知错误'}`)
    } finally {
      setReviewing(false)
    }
  }

  const report = task?.deliverable_report ?? null
  const feedback = task?.review_feedback ?? localReview
  const reportSummary =
    report && typeof report.summary === 'string' ? report.summary : null
  const reportArtifacts =
    report && Array.isArray(report.artifacts) ? (report.artifacts as unknown[]) : []
  const reportRest = report
    ? Object.entries(report).filter(([k]) => k !== 'summary' && k !== 'artifacts')
    : []

  return (
    <Layout>
      <div className="at-paper min-h-full w-full px-6 md:px-10 py-8">
        <div className="w-full max-w-[1280px] mx-auto space-y-8">
        <header className="flex items-start justify-between gap-6 flex-wrap">
          <div className="min-w-0">
            <nav className="flex items-center gap-2 text-caption text-text-tertiary">
              <span>生产</span>
              <span className="text-text-muted">/</span>
              <span>团队协同</span>
              <span className="text-text-muted">/</span>
              <span className="text-text-primary">岗位竞标</span>
            </nav>
            <h1 className="mt-2 text-[30px] leading-9 font-bold tracking-tight text-text-primary">
              {task?.title ?? '竞标台'}
            </h1>
            <div className="mt-2.5 flex items-center flex-wrap gap-2.5 text-body-sm text-text-tertiary">
              <span className="text-text-primary font-medium">
                第 {activeRound}/{BID_ROUNDS} 轮
              </span>
              <span className="w-1 h-1 rounded-full bg-border-default" />
              <span className="font-mono text-caption">{candidates.length} 名候选员工</span>
              <span className="w-1 h-1 rounded-full bg-border-default" />
              <span className="font-mono text-caption text-text-tertiary border border-border-default rounded px-2 py-0.5">
                综合得分 = HP × {HP_WEIGHT} + 绩效 × {PERF_WEIGHT}
              </span>
              {task && <StatusDot tone={task.status === 'review' ? 'warning' : 'brand'} label={STATUS_LABEL[task.status] ?? task.status} />}
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0 pt-1">
            <button
              type="button"
              onClick={() => setBidOpen(true)}
              disabled={!taskId}
              className="h-9 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors disabled:opacity-50"
            >
              <Scale className="w-3.5 h-3.5" aria-hidden="true" />
              记录竞标轮次
            </button>
            <button
              type="button"
              onClick={() => {
                if (leaderId) void handleAward(leaderId)
                else toast.error('尚无已结算的竞标轮次，无法裁决中标')
              }}
              disabled={!leaderId || !taskId}
              className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
            >
              <Check className="w-3.5 h-3.5" aria-hidden="true" />
              确认中标
            </button>
          </div>
        </header>

        {/* ============ 2. 任务与工作组选择 ============ */}
        <div className="flex items-end gap-4 flex-wrap pb-6 border-b border-border-subtle">
          <div>
            <label htmlFor="arena-team" className="block text-caption text-text-tertiary mb-1.5">
              工作组
            </label>
            <select
              id="arena-team"
              value={teamId}
              onChange={(e) => {
                setBids([])
                setParam('team', e.target.value)
              }}
              className="h-9 min-w-[180px] px-2.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
            >
              {teams.length === 0 ? <option value="">暂无工作组</option> : null}
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1 min-w-[260px]">
            <label htmlFor="arena-task" className="block text-caption text-text-tertiary mb-1.5">
              竞标任务
            </label>
            <select
              id="arena-task"
              value={taskId}
              onChange={(e) => setParam('task', e.target.value)}
              className="w-full h-9 px-2.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
            >
              {tasks.length === 0 ? <option value="">暂无任务</option> : null}
              {tasks.map((t) => (
                <option key={t.id} value={t.id}>
                  [{STATUS_LABEL[t.status] ?? t.status}] {t.title}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            onClick={() => {
              if (teamId) void loadTasks(teamId)
            }}
            className="h-9 w-9 inline-flex items-center justify-center rounded-md border border-border-default bg-surface text-text-tertiary hover:bg-elevated hover:text-text-primary transition-colors"
            aria-label="刷新任务"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          </button>
        </div>

        {/* ============ 3. 候选人竞标对比 ============ */}
        <section className="space-y-4">
          <div className="flex items-baseline justify-between gap-4 pb-3 border-b border-border-subtle flex-wrap">
            <div className="flex items-baseline gap-3">
              <h2 className="text-h4 font-semibold text-text-primary">候选人竞标对比</h2>
              <span className="text-caption text-text-tertiary">
                共 {candidates.length} 名候选员工参选
              </span>
            </div>
            {leaderId && (
              <span className="flex items-center gap-2 text-caption text-text-tertiary">
                <span className="w-2 h-2 rounded-full bg-brand-500" aria-hidden="true" />
                当前推荐中标席位：{profileMap.get(leaderId)?.display_name ?? '—'}
              </span>
            )}
          </div>

          <div className="rounded-[2px] border border-border-default bg-surface overflow-x-auto">
            <table className="w-full text-left border-collapse min-w-[860px]">
              <thead>
                <tr className="h-12 border-b border-border-default bg-white text-[12px] text-text-tertiary">
                  <th className="pl-6 pr-4 text-caption font-medium uppercase tracking-wider text-text-tertiary w-1/4">
                    候选员工
                  </th>
                  <th className="px-4 text-caption font-medium uppercase tracking-wider text-text-tertiary w-1/5">
                    HP 计算算力
                  </th>
                  <th className="px-4 text-caption font-medium uppercase tracking-wider text-text-tertiary w-2/5">
                    最新陈述
                  </th>
                  <th className="px-4 text-right text-caption font-medium uppercase tracking-wider text-text-tertiary w-24">
                    TL 打分
                  </th>
                  <th className="pr-6 pl-4 text-right text-caption font-medium uppercase tracking-wider text-text-tertiary w-28">
                    综合得分
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-6 py-10 text-center text-body-sm text-text-muted">
                      {teamId ? '该工作组暂无成员编制' : '请先选择工作组'}
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => {
                    const lead = row.profile.id === leaderId
                    const muted = row.eliminated
                    return (
                      <tr
                        key={row.profile.id}
                        className={`h-[54px] transition-colors hover:bg-elevated/40 ${muted ? 'opacity-60' : ''}`}
                      >
                        <td className="pl-6 pr-4">
                          <div className="flex items-center gap-3">
                            <span className="relative w-8 h-8 rounded-full bg-[#EDEEF2] text-[#374151] flex items-center justify-center text-xs font-medium flex-shrink-0 select-none">
                              {row.profile.display_name.slice(0, 1)}
                              {lead ? (
                                <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-brand-500" />
                              ) : null}
                            </span>
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-[14px] font-semibold text-text-primary truncate">
                                  {row.profile.display_name}
                                </span>
                                {lead && (
                                  <span className="inline-flex items-center gap-1 text-[12px] text-brand-500 font-medium">
                                    <span className="w-1.5 h-1.5 rounded-full bg-brand-500" aria-hidden="true" />
                                    领跑
                                  </span>
                                )}
                                {row.isAssignee && (
                                  <span className="inline-flex items-center gap-1 text-[12px] text-success font-medium">
                                    <span className="w-1.5 h-1.5 rounded-full bg-success" aria-hidden="true" />
                                    中选
                                  </span>
                                )}
                                {row.eliminated && (
                                  <span className="inline-flex items-center gap-1 text-[12px] text-text-muted">
                                    <span className="w-1.5 h-1.5 rounded-full bg-text-muted" aria-hidden="true" />
                                    HP 归零
                                  </span>
                                )}
                              </div>
                              <div className="font-mono text-caption text-text-tertiary truncate">
                                {formatBadge(row.profile.employee_badge, row.profile.id)} · {row.profile.job_title}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="px-4">
                          <Meter value={row.hp} lead={lead} />
                        </td>
                        <td className="px-4 text-body-sm text-text-tertiary">
                          <span className="line-clamp-2">
                            {row.settled
                              ? `R${row.settled.round}｜${row.settled.statement}`
                              : '尚未提交竞标陈述'}
                          </span>
                        </td>
                        <td className="px-4 text-right font-mono text-body-sm text-text-primary">
                          {row.settled ? row.settled.score.toFixed(1) : '--'}
                        </td>
                        <td
                          className={`pr-6 pl-4 text-right ${
                            lead ? 'text-h4 font-bold text-text-primary' : 'text-h4 font-semibold text-text-primary'
                          }`}
                        >
                          {row.composite !== null ? row.composite.toFixed(1) : '--'}
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>

          <p className="text-caption text-text-muted">
            评分与 HP 取自后端 BiddingEngine 结算结果（HP_r = HP_(r-1) − (10 − 打分) × 3）；后端暂未开放竞标记录查询端点，故未提交轮次的候选人显示「—」。
          </p>
        </section>

        {/* ============ 4. 交付物与前置验收 ============ */}
        <section className="space-y-4">
          <div className="flex items-center justify-between gap-4 pb-3 border-b border-border-subtle flex-wrap">
            <h2 className="text-h4 font-semibold text-text-primary">
              交付物与前置验收
              <span className="ml-2 text-body-sm font-normal text-text-tertiary">
                {report ? '已提交报告' : '暂无报告'}
              </span>
            </h2>
            <div className="flex items-center gap-2">
              <span className="text-caption text-text-tertiary">前置标准关联黑板共识协议</span>
              <button
                type="button"
                onClick={() => setDeliverOpen(true)}
                disabled={!taskId}
                className="h-8 px-2.5 inline-flex items-center gap-1 rounded-md border border-border-default bg-surface text-caption text-text-secondary hover:bg-elevated transition-colors disabled:opacity-50"
              >
                <FileCheck2 className="w-3 h-3" aria-hidden="true" />
                提交履约报告
              </button>
            </div>
          </div>

          {!report ? (
            <p className="text-body-sm text-text-muted py-4">
              {task?.status === 'in_progress'
                ? '交付人尚未提交履约报告 —— 提交后进入 Leader 验收环节。'
                : '任务定标并进入执行后，交付人会在这里提交结构化履约报告。'}
            </p>
          ) : (
            <div className="divide-y divide-border-subtle border-y border-border-subtle">
              {reportSummary && (
                <div className="py-4 px-1">
                  <p className="text-caption text-text-tertiary mb-1.5">履约摘要</p>
                  <p className="text-[14px] leading-6 text-text-primary">{reportSummary}</p>
                </div>
              )}
              {reportArtifacts.map((item, idx) => (
                <div
                  key={`artifact-${idx}`}
                  className="py-3.5 flex items-center justify-between gap-4 px-1 group hover:bg-elevated/30 transition-colors"
                >
                  <div className="flex items-center gap-3 text-body-sm min-w-0">
                    <span className="font-mono text-caption text-text-tertiary border border-border-default px-1.5 py-0.5 rounded-[2px] bg-surface flex-shrink-0">
                      DOC-{String(idx + 1).padStart(2, '0')}
                    </span>
                    <span className="text-text-primary font-medium truncate">
                      {typeof item === 'string' ? item : JSON.stringify(item)}
                    </span>
                  </div>
                  <span className="text-caption text-success flex items-center gap-1.5 flex-shrink-0">
                    <span className="w-[5px] h-[5px] rounded-full bg-success" aria-hidden="true" />
                    待 Leader 验收
                  </span>
                </div>
              ))}
              {reportRest.map(([key, value]) => (
                <div key={key} className="py-3.5 px-1 flex items-start gap-3 text-body-sm">
                  <span className="text-text-tertiary w-40 flex-shrink-0">{key}</span>
                  <span className="font-mono text-caption text-text-primary break-all">
                    {typeof value === 'object' && value !== null
                      ? JSON.stringify(value)
                      : String(value)}
                  </span>
                </div>
              ))}
              <div className="py-3.5 px-1 flex items-center justify-between gap-4">
                <span className="text-body-sm text-text-tertiary">Leader 验收结论</span>
                <span className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => void handleReview(false)}
                    disabled={reviewing || !taskId}
                    className="h-8 px-3 inline-flex items-center gap-1 rounded-md border border-border-default bg-surface text-caption text-text-secondary hover:bg-elevated transition-colors disabled:opacity-50"
                  >
                    <X className="w-3 h-3" aria-hidden="true" />
                    退回整改
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleReview(true)}
                    disabled={reviewing || !taskId}
                    className="h-8 px-3 inline-flex items-center gap-1 rounded-md bg-brand-500 text-white text-caption font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
                  >
                    <Check className="w-3 h-3" aria-hidden="true" />
                    采纳收口
                  </button>
                </span>
              </div>
            </div>
          )}

          {feedback && Object.keys(feedback).length > 0 && (
            <div className="rounded-[10px] border border-border-default bg-elevated/50 px-5 py-4">
              <p className="text-caption text-text-tertiary mb-1.5">最近一次验收留痕</p>
              <p className="text-body-sm text-text-primary">
                {Object.entries(feedback)
                  .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
                  .join('　·　')}
              </p>
            </div>
          )}
        </section>

        {/* ============ 5. 轮次流水与留痕 ============ */}
        <section className="space-y-3">
          <div className="flex items-center justify-between gap-4 pb-3 border-b border-border-subtle flex-wrap">
            <h2 className="text-h4 font-semibold text-text-primary">竞标轮次流水</h2>
            <span className="text-caption text-text-tertiary">
              已结算 {taskBids.length} 轮 · 任务 ID {taskId || '—'}
            </span>
          </div>

          {taskBids.length === 0 ? (
            <p className="text-body-sm text-text-muted py-3">
              尚无已结算的竞标轮次 —— 点击「记录竞标轮次」提交候选员工的量化陈述。
            </p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
              {taskBids
                .slice()
                .sort((a, b) => a.round - b.round)
                .map((bid) => (
                  <div key={bid.key} className="flex gap-3 py-1">
                    <CornerDownRight
                      className="w-3.5 h-3.5 text-text-muted flex-shrink-0 mt-1"
                      aria-hidden="true"
                    />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono text-caption text-text-tertiary">
                          ROUND {bid.round}
                        </span>
                        <span className="text-body-sm font-medium text-text-primary">
                          {profileMap.get(bid.candidateProfileId)?.display_name ?? bid.candidateProfileId}
                        </span>
                        <span className="font-mono text-caption text-text-tertiary">
                          打分 {bid.score.toFixed(1)} · HP {bid.hp}
                        </span>
                        {bid.hp <= 0 && (
                          <span className="inline-flex items-center gap-1 text-caption text-warning">
                            <AlertTriangle className="w-3 h-3" aria-hidden="true" />
                            HP 归零退出
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-body-sm leading-6 text-text-secondary">
                        {bid.statement}
                      </p>
                    </div>
                  </div>
                ))}
            </div>
          )}

          <footer className="pt-5 mt-2 border-t border-border-subtle flex items-center justify-between gap-4 text-caption text-text-muted flex-wrap">
            <span>竞标记录已随任务版本留痕，可在 Trace 观测页回放</span>
            <span className="font-mono">
              TASK_VERSION: {task?.status ?? '—'} · WEIGHT HP {HP_WEIGHT} / PERF {PERF_WEIGHT}
            </span>
          </footer>
        </section>

        {/* ============ 竞标轮次提交 ============ */}
        <ModalShell
          open={bidOpen}
          onClose={() => setBidOpen(false)}
          labelledBy="bid-modal-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          panelClassName="w-full max-w-xl rounded-[10px] border border-border-default bg-surface shadow-lift"
        >
          <div className="p-6 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="bid-modal-title" className="text-h4 font-semibold text-text-primary">
                  记录竞标轮次
                </h2>
                <p className="mt-1 text-body-sm text-text-tertiary">
                  打分 0-10，引擎按 HP_r = HP_(r-1) − (10 − 打分) × 3 实时结算算力。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setBidOpen(false)}
                aria-label="关闭"
                className="w-8 h-8 flex items-center justify-center rounded-md text-text-tertiary hover:bg-elevated transition-colors"
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="bid-candidate" className="block text-body-sm text-text-secondary mb-1.5">
                  竞标数字员工
                </label>
                <select
                  id="bid-candidate"
                  value={bidCandidate}
                  onChange={(e) => setBidCandidate(e.target.value)}
                  className="w-full h-9 px-2.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                >
                  <option value="">请选择…</option>
                  {candidates.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name}（{p.employee_badge}）
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="bid-round" className="block text-body-sm text-text-secondary mb-1.5">
                  竞标轮次
                </label>
                <select
                  id="bid-round"
                  value={bidRound}
                  onChange={(e) => setBidRound(Number(e.target.value))}
                  className="w-full h-9 px-2.5 rounded-md border border-border-default bg-surface text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                >
                  {Array.from({ length: BID_ROUNDS }, (_, i) => i + 1).map((r) => (
                    <option key={r} value={r}>
                      第 {r} 轮
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label htmlFor="bid-statement" className="block text-body-sm text-text-secondary mb-1.5">
                方案陈述
              </label>
              <textarea
                id="bid-statement"
                rows={3}
                value={bidStatement}
                onChange={(e) => setBidStatement(e.target.value)}
                placeholder="例如：已解析往期中标标书结构并完成初稿大纲，可分三阶段交付。"
                className="w-full p-3 rounded-md border border-border-default bg-surface text-[14px] leading-6 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500 resize-y"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="bid-score" className="block text-body-sm text-text-secondary mb-1.5">
                  Leader 打分（0-10）
                </label>
                <input
                  id="bid-score"
                  type="number"
                  min={0}
                  max={10}
                  step={0.5}
                  value={bidScore}
                  onChange={(e) => setBidScore(Number(e.target.value))}
                  className="w-full h-9 px-3 rounded-md border border-border-default bg-surface font-mono text-body-sm text-text-primary focus:outline-none focus:border-brand-500"
                />
              </div>
              <div>
                <label htmlFor="bid-rationale" className="block text-body-sm text-text-secondary mb-1.5">
                  评分依据
                </label>
                <input
                  id="bid-rationale"
                  type="text"
                  value={bidRationale}
                  onChange={(e) => setBidRationale(e.target.value)}
                  placeholder="技术匹配度高，工期偏紧"
                  className="w-full h-9 px-3 rounded-md border border-border-default bg-surface text-body-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setBidOpen(false)}
                className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleSubmitBid}
                disabled={bidding || !bidCandidate || !bidStatement.trim()}
                className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
              >
                <Send className="w-3.5 h-3.5" aria-hidden="true" />
                {bidding ? '结算中…' : '提交并结算'}
              </button>
            </div>
          </div>
        </ModalShell>

        {/* ============ 履约报告提交 ============ */}
        <ModalShell
          open={deliverOpen}
          onClose={() => setDeliverOpen(false)}
          labelledBy="deliver-modal-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          panelClassName="w-full max-w-xl rounded-[10px] border border-border-default bg-surface shadow-lift"
        >
          <div className="p-6 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <h2 id="deliver-modal-title" className="text-h4 font-semibold text-text-primary">
                提交履约报告
              </h2>
              <button
                type="button"
                onClick={() => setDeliverOpen(false)}
                aria-label="关闭"
                className="w-8 h-8 flex items-center justify-center rounded-md text-text-tertiary hover:bg-elevated transition-colors"
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label
                  htmlFor="deliver-summary"
                  className="block text-body-sm text-text-secondary mb-1.5"
                >
                  履约摘要
                </label>
                <textarea
                  id="deliver-summary"
                  rows={3}
                  value={deliverSummary}
                  onChange={(e) => setDeliverSummary(e.target.value)}
                  placeholder="用一段话说明交付结论、覆盖范围与遗留风险。"
                  className="w-full p-3 rounded-md border border-border-default bg-surface text-[14px] leading-6 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500 resize-y"
                />
              </div>
              <div>
                <label
                  htmlFor="deliver-artifacts"
                  className="block text-body-sm text-text-secondary mb-1.5"
                >
                  交付件清单（每行一项）
                </label>
                <textarea
                  id="deliver-artifacts"
                  rows={4}
                  value={deliverArtifacts}
                  onChange={(e) => setDeliverArtifacts(e.target.value)}
                  placeholder={'结构化验收报告\n标杆案例索引与技术白皮书匹配表'}
                  className="w-full p-3 rounded-md border border-border-default bg-surface text-[14px] leading-6 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500 resize-y"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setDeliverOpen(false)}
                className="h-9 px-4 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleDeliver}
                disabled={delivering || !deliverSummary.trim()}
                className="h-9 px-4 inline-flex items-center gap-1.5 rounded-md bg-brand-500 text-white text-body-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
              >
                <FileCheck2 className="w-3.5 h-3.5" aria-hidden="true" />
                {delivering ? '提交中…' : '提交验收'}
              </button>
            </div>
          </div>
        </ModalShell>
        </div>
      </div>
    </Layout>
  )
}
