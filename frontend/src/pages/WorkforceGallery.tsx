/**
 * WorkforceGallery — 数字员工花名册。
 *
 * 对应蓝图 screens-v2/02-employee-roster.html：回答「我有哪些编制」。
 * 主导区是发丝线名录（12 栏静默表头 + 48px 行高 + 无斑马纹），另提供卡片式矩阵视图。
 * 状态 = 6px 圆点 + 13px 文字；绩效以 HP 细条呈现。
 *
 * 数据来源：/api/v1/workforce-profiles（listWorkforceProfiles / createWorkforceProfile /
 * checkDutyBoundary），见 api/workforceProfiles.ts。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Plus,
  Search,
  RefreshCw,
  LayoutGrid,
  Rows3,
  ShieldCheck,
  Users,
  X,
  MessagesSquare,
} from 'lucide-react'
import { toast } from 'sonner'
import Layout from '@/components/Layout'
import { Drawer } from '@/components/ui/Drawer'
import { ModalShell } from '@/components/ui/ModalShell'
import {
  listWorkforceProfiles,
  createWorkforceProfile,
  updateWorkforceProfile,
  checkDutyBoundary,
  type DutyBoundaries,
  type WorkforceProfile,
  type CreateWorkforceProfilePayload,
  type UpdateWorkforceProfilePayload,
} from '@/api/workforceProfiles'
import { getAgents } from '@/api/agents'
import { formatEmployeeBadge } from '@/utils/employeeBadge'
import { performanceScoreDisplay } from '@/utils/performanceScore'
import {
  DEPARTMENT_FILTER_OPTIONS,
  DEPARTMENTS,
  ALL_DEPARTMENTS_FILTER,
  DEFAULT_DEPARTMENT,
} from '@/utils/departments'

/** 每页条数（对齐原型的 7 行名录） */
const PAGE_SIZE = 7

/** 圆点语义：ok 正常 / warn 观察 / bad 异常 / idle 未知 */
type StatusTone = { label: string; tone: 'ok' | 'warn' | 'bad' | 'idle' }

/** 在岗状态 → 中文标签与圆点语义（覆盖后端全部可能取值，未知值回落到 idle） */
const STATUS_META: Record<WorkforceProfile['employment_status'], StatusTone> = {
  production: { label: '在岗', tone: 'ok' },
  active: { label: '在岗', tone: 'ok' },
  probation: { label: '实习中', tone: 'warn' },
  shadow: { label: '影子考核', tone: 'warn' },
  suspended: { label: '已停职', tone: 'bad' },
  retired: { label: '已停用', tone: 'bad' },
}
const STATUS_FALLBACK: StatusTone = { label: '未知状态', tone: 'idle' }

/** 语调风格预设 */
const TONE_PRESETS = ['专业、结论先行', '亲切、耐心安抚', '简洁、只给要点', '严谨、数据驱动'] as const

/**
 * 「找她聊聊」直达对话。
 *
 * /chat/:agentId 消费的是 agents.id（GET /agents/{id} + /agents/{id}/chat/stream），
 * 而花名册行的主键是 workforce_profiles.id —— 两者不可互换。
 * 因此只有在档案已绑定底层 Agent（profile.agent_id）时才能直达，
 * 未绑定时必须显式禁用并说明原因，不能给一个必然 404 的链接。
 */
function ChatCta({
  profile,
  navigate,
  className = 'at-link at-sm',
  label = '找她聊聊',
}: {
  profile: WorkforceProfile
  navigate: (to: string) => void
  className?: string
  label?: string
}) {
  const agentId = profile.agent_id
  const displayName = profile.display_name

  if (!agentId) {
    return (
      <span
        className={`${className} at-subtle inline-flex items-center gap-1 cursor-not-allowed`}
        title={`${displayName} 尚未绑定底层 Agent，暂不能直接对话`}
        aria-disabled="true"
      >
        <MessagesSquare className="h-3.5 w-3.5" aria-hidden="true" />
        {label}
      </span>
    )
  }

  return (
    <button
      type="button"
      className={`${className} inline-flex items-center gap-1`}
      title={`与 ${displayName} 开始对话`}
      onClick={(e) => {
        e.stopPropagation()
        navigate(`/chat/${agentId}`)
      }}
    >
      <MessagesSquare className="h-3.5 w-3.5" aria-hidden="true" />
      {label}
    </button>
  )
}

const PROBATION_STAGES = ['影子陪伴', '独立评估', '达标审查', '正式转正'] as const

const STAGE_INDEX: Record<WorkforceProfile['employment_status'], number> = {
  shadow: 0,
  probation: 2,
  production: PROBATION_STAGES.length,
  active: PROBATION_STAGES.length,
  // -1 = 考核已被中止（停职 / 停用），不是「四段全部走完」
  suspended: -1,
  retired: -1,
}

/** 考核时间线阶段文案：已中止 / 已走完 / 当前阶段，三者互斥 */
function stageLabel(status: WorkforceProfile['employment_status']): string {
  const idx = STAGE_INDEX[status] ?? 0
  if (idx < 0) return '考核已中止'
  if (idx >= PROBATION_STAGES.length) return '四段考核已走完'
  return `当前阶段：${PROBATION_STAGES[idx]}`
}

/** 新建编制的空白草稿（唯一事实来源，避免创建成功后重置逻辑与初值漂移） */
const EMPTY_DRAFT: CreateWorkforceProfilePayload = {
  display_name: '',
  job_title: '',
  department: DEFAULT_DEPARTMENT,
  tone_style: 'professional',
  duty_boundaries: { allowed: [], forbidden: [] },
  employment_status: 'shadow',
  performance_score: 85,
}

type DutyEditDraft = { job_title: string; duty_boundaries: DutyBoundaries }
export default function WorkforceGallery() {
  const navigate = useNavigate()
  /** 可绑定到底层 Agent 的候选（创建编制时下拉选择，决定「找她聊聊」能否直达对话） */
  const [agentOptions, setAgentOptions] = useState<Array<{ id: string; name: string }>>([])

  const [profiles, setProfiles] = useState<WorkforceProfile[]>([])

  /**
   * 已被其他编制绑定的 Agent 不能再次出现在候选里。
   * 后端 agent_id 只有普通索引、没有唯一约束，create 也不查重：
   * 绑两次会得到两个名字不同、但「找她聊聊」都跳同一个 /chat/:agentId 的假员工。
   */
  const boundAgentIds = useMemo(
    () => new Set(profiles.map((p) => p.agent_id).filter((id): id is string => Boolean(id))),
    [profiles],
  )
  const bindableAgents = useMemo(
    () => agentOptions.filter((a) => !boundAgentIds.has(a.id)),
    [agentOptions, boundAgentIds],
  )
  /** 用户是否点过「确认创建」：内联红字只在此之后出现，避免刚开抽屉就报填错 */
  const [createAttempted, setCreateAttempted] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'internship' | 'production' | 'retired'>('all')
  const [departmentFilter, setDepartmentFilter] = useState<string>(ALL_DEPARTMENTS_FILTER)
  const [selectedProfile, setSelectedProfile] = useState<WorkforceProfile | null>(null)
  const [editDraft, setEditDraft] = useState<DutyEditDraft | null>(null)
  const [editAllowedInput, setEditAllowedInput] = useState('')
  const [editForbiddenInput, setEditForbiddenInput] = useState('')
  const [editAttempted, setEditAttempted] = useState(false)
  const [savingEdit, setSavingEdit] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const [pendingEditExit, setPendingEditExit] = useState<'detail' | 'close' | null>(null)
  const editJobTitleRef = useRef<HTMLInputElement>(null)
  const editButtonRef = useRef<HTMLButtonElement>(null)
  const [view, setView] = useState<'list' | 'grid'>('list')
  const [page, setPage] = useState(1)

  const [showCreate, setShowCreate] = useState(false)
  const [draft, setDraft] = useState<CreateWorkforceProfilePayload>(EMPTY_DRAFT)
  const [allowedInput, setAllowedInput] = useState('')
  const [forbiddenInput, setForbiddenInput] = useState('')
  const [creating, setCreating] = useState(false)

  const isEditingDuties = editDraft !== null
  useEffect(() => {
    if (isEditingDuties) editJobTitleRef.current?.focus()
  }, [isEditingDuties])
  useEffect(() => {
    if (pendingEditExit) document.getElementById('wf-edit-keep')?.focus()
  }, [pendingEditExit])

  const editDirty = Boolean(
    editDraft && selectedProfile && (
      editDraft.job_title !== selectedProfile.job_title ||
      JSON.stringify(editDraft.duty_boundaries.allowed) !== JSON.stringify(selectedProfile.duty_boundaries?.allowed ?? []) ||
      JSON.stringify(editDraft.duty_boundaries.forbidden) !== JSON.stringify(selectedProfile.duty_boundaries?.forbidden ?? []) ||
      editAllowedInput.trim().length > 0 ||
      editForbiddenInput.trim().length > 0
    ),
  )
  useEffect(() => {
    if (!editDirty || savingEdit) return
    const preventUnsavedUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', preventUnsavedUnload)
    return () => window.removeEventListener('beforeunload', preventUnsavedUnload)
  }, [editDirty, savingEdit])

  const allowedList = draft.duty_boundaries?.allowed ?? []
  const forbiddenList = draft.duty_boundaries?.forbidden ?? []
  const canCreate = draft.display_name.trim().length > 0 && draft.job_title.trim().length > 0

  const setBoundaries = (next: { allowed: string[]; forbidden: string[] }) => {
    setDraft({ ...draft, duty_boundaries: next })
  }

  const addBoundary = (kind: 'allowed' | 'forbidden') => {
    const value = (kind === 'allowed' ? allowedInput : forbiddenInput).trim()
    if (!value) return
    setBoundaries(
      kind === 'allowed'
        ? { allowed: [...allowedList, value], forbidden: forbiddenList }
        : { allowed: allowedList, forbidden: [...forbiddenList, value] },
    )
    if (kind === 'allowed') setAllowedInput('')
    else setForbiddenInput('')
  }

  const removeBoundary = (kind: 'allowed' | 'forbidden', index: number) => {
    setBoundaries(
      kind === 'allowed'
        ? { allowed: allowedList.filter((_, i) => i !== index), forbidden: forbiddenList }
        : { allowed: allowedList, forbidden: forbiddenList.filter((_, i) => i !== index) },
    )
  }

  const [probeTarget, setProbeTarget] = useState<WorkforceProfile | null>(null)
  const [probeIntent, setProbeIntent] = useState('')
  const [probeResult, setProbeResult] = useState<{ allowed: boolean; reason: string; matched_boundary?: string } | null>(null)
  const [probing, setProbing] = useState(false)

  const fetchProfiles = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const data = await listWorkforceProfiles()
      setProfiles(data)
    } catch (err) {
      setProfiles([])
      setLoadError(
        `花名册服务暂不可用（${err instanceof Error && err.message ? err.message : '网络异常'}），请重试`,
      )
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    void fetchProfiles()
  }, [fetchProfiles])

  // 绑定候选来自真实的 /agents：没有底层 Agent 就没有对话会话可用
  useEffect(() => {
    let cancelled = false
    getAgents()
      .then((agents) => {
        if (cancelled) return
        setAgentOptions(
          agents.map((a) => ({ id: a.id, name: a.name })),
        )
      })
      .catch(() => {
        if (!cancelled) setAgentOptions([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  const counts = useMemo(() => {
    const c: Record<WorkforceProfile['employment_status'], number> = {
      production: 0,
      active: 0,
      probation: 0,
      shadow: 0,
      suspended: 0,
      retired: 0,
    }
    for (const p of profiles) {
      c[p.employment_status] = (c[p.employment_status] ?? 0) + 1
    }
    return c
  }, [profiles])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return profiles.filter((p) => {
      if (departmentFilter !== ALL_DEPARTMENTS_FILTER && p.department !== departmentFilter) return false
      if (statusFilter === 'production' && p.employment_status !== 'production' && p.employment_status !== 'active') return false
      if (statusFilter === 'retired' && p.employment_status !== 'retired' && p.employment_status !== 'suspended') return false
      if (statusFilter === 'internship' && p.employment_status !== 'shadow' && p.employment_status !== 'probation') return false
      if (!q) return true
      return (
        p.display_name.toLowerCase().includes(q) ||
        p.employee_badge.toLowerCase().includes(q) ||
        p.job_title.toLowerCase().includes(q) ||
        p.department.toLowerCase().includes(q)
      )
    })
  }, [profiles, search, statusFilter, departmentFilter])
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const pageRows = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)
  const scoredProfiles = profiles
    .map((p) => performanceScoreDisplay(p.performance_score).score)
    .filter((score): score is number => score !== null)
  const avgScore = scoredProfiles.length > 0
    ? (scoredProfiles.reduce((sum, score) => sum + score, 0) / scoredProfiles.length).toFixed(1)
    : '未评估'

  // 筛选条件变化后回到第一页，避免停在越界页码
  useEffect(() => {
    setPage(1)
  }, [search, statusFilter, departmentFilter])

  const runProbeForProfile = async (profileId: string) => {
    if (!probeIntent.trim()) return
    setProbing(true)
    try {
      setProbeResult(await checkDutyBoundary(profileId, probeIntent.trim()))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '边界校验失败')
    } finally {
      setProbing(false)
    }
  }
  const handleCreate = async () => {
    setCreateAttempted(true)
    if (!canCreate) {
      toast.error('请填写员工姓名与岗位职务')
      return
    }
    const name = draft.display_name.trim()
    setCreating(true)
    try {
      const created = await createWorkforceProfile({
        ...draft,
        display_name: name,
        job_title: draft.job_title.trim(),
        department: draft.department,
        tone_style: draft.tone_style?.trim() || undefined,
      })
      setShowCreate(false)
      setCreateAttempted(false)
      setAllowedInput('')
      setForbiddenInput('')
      // 直接选中新建编制：用户立刻能看到系统授予的 ATE- 工号与岗位边界
      setSelectedProfile(created)
      void fetchProfiles()
      toast.success(
        created.agent_id
          ? `数字员工「${name}」已创建，工号 ${formatEmployeeBadge(created.employee_badge, created.id)}，可直接发起对话`
          : `数字员工「${name}」已创建，工号 ${formatEmployeeBadge(created.employee_badge, created.id)}；未绑定底层 Agent，暂不能发起对话`,
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '创建失败，请稍后重试')
    } finally {
      setCreating(false)
    }
  }

  const beginDutyEdit = () => {
    if (!selectedProfile) return
    setEditDraft({
      job_title: selectedProfile.job_title,
      duty_boundaries: {
        allowed: [...(selectedProfile.duty_boundaries?.allowed ?? [])],
        forbidden: [...(selectedProfile.duty_boundaries?.forbidden ?? [])],
      },
    })
    setEditAllowedInput('')
    setEditForbiddenInput('')
    setEditAttempted(false)
    setEditError(null)
    setPendingEditExit(null)
  }

  const finishDutyEdit = (destination: 'detail' | 'close') => {
    setEditDraft(null)
    setEditAllowedInput('')
    setEditForbiddenInput('')
    setEditAttempted(false)
    setEditError(null)
    setPendingEditExit(null)
    if (destination === 'close') {
      setSelectedProfile(null)
      setProbeResult(null)
    } else {
      requestAnimationFrame(() => editButtonRef.current?.focus())
    }
  }

  const requestDutyExit = (destination: 'detail' | 'close') => {
    if (savingEdit || pendingEditExit) return
    if (editDirty) {
      setPendingEditExit(destination)
      return
    }
    finishDutyEdit(destination)
  }

  const closeProfileDrawer = () => {
    if (isEditingDuties) requestDutyExit('close')
    else finishDutyEdit('close')
  }

  const appendEditBoundary = (kind: keyof DutyBoundaries) => {
    const value = (kind === 'allowed' ? editAllowedInput : editForbiddenInput).trim()
    if (!value || !editDraft || savingEdit) return
    setEditDraft(current => current ? {
      ...current,
      duty_boundaries: {
        ...current.duty_boundaries,
        [kind]: [...current.duty_boundaries[kind], value],
      },
    } : current)
    if (kind === 'allowed') setEditAllowedInput('')
    else setEditForbiddenInput('')
  }

  const removeEditBoundary = (kind: keyof DutyBoundaries, index: number) => {
    if (savingEdit) return
    setEditDraft(current => current ? {
      ...current,
      duty_boundaries: {
        ...current.duty_boundaries,
        [kind]: current.duty_boundaries[kind].filter((_, itemIndex) => itemIndex !== index),
      },
    } : current)
  }

  const saveDutyEdit = async () => {
    if (!selectedProfile || !editDraft || savingEdit) return
    setEditAttempted(true)
    const jobTitle = editDraft.job_title.trim()
    if (!jobTitle) {
      editJobTitleRef.current?.focus()
      return
    }
    setSavingEdit(true)
    setEditError(null)
    const profileId = selectedProfile.id
    const payload: UpdateWorkforceProfilePayload = {
      job_title: jobTitle,
      duty_boundaries: {
        allowed: [...editDraft.duty_boundaries.allowed, ...(editAllowedInput.trim() ? [editAllowedInput.trim()] : [])],
        forbidden: [...editDraft.duty_boundaries.forbidden, ...(editForbiddenInput.trim() ? [editForbiddenInput.trim()] : [])],
      },
    }
    try {
      const updated = await updateWorkforceProfile(profileId, payload)
      setSelectedProfile(updated)
      setProfiles(current => current.map(profile => profile.id === profileId ? updated : profile))
      finishDutyEdit('detail')
      toast.success('岗位职责已保存')
    } catch {
      setEditError('保存失败，请重试；当前输入已保留。')
      toast.error('岗位职责保存失败，输入已保留')
    } finally {
      setSavingEdit(false)
    }
  }

  const runProbe = async () => {
    if (!probeTarget || !probeIntent.trim()) return
    setProbing(true)
    try {
      setProbeResult(await checkDutyBoundary(probeTarget.id, probeIntent.trim()))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '边界校验失败')
    } finally {
      setProbing(false)
    }
  }

  const selectedScore = performanceScoreDisplay(selectedProfile?.performance_score)

  return (
    <Layout>
      <div className="at-paper min-h-full w-full px-4 py-6 sm:px-6 sm:py-8 lg:px-10 lg:py-8">
        <div className="mx-auto w-full max-w-[1280px] space-y-8">
          {/* 抬头：30px 标题 + 13px 副标题 + 全屏唯一实心主按钮 */}
          <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="at-title">数字员工花名册</h1>
              <p className="at-sm at-muted mt-1">
                在岗 {counts.production + counts.active} 人 · 影子考核 {counts.shadow} 人 · 编制总数{' '}
                {profiles.length}
              </p>
            </div>
            <button
              type="button"
              className="at-btn at-btn-primary"
              onClick={() => {
                setCreateAttempted(false)
                setShowCreate(true)
              }}
              disabled={loading && profiles.length === 0}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              新增数字员工
            </button>
          </header>

          {/* 检索 + 状态过滤 + 视图切换 */}
          <div className="flex flex-col gap-3 sm:gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="relative w-full lg:w-80">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 at-subtle"
                aria-hidden="true"
              />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索工号、姓名、岗位或部门…"
                aria-label="搜索数字员工"
                className="at-input pl-9"
              />
            </div>

            <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:gap-3">
              {/* 部门筛选 */}
              <select
                value={departmentFilter}
                onChange={(e) => setDepartmentFilter(e.target.value)}
                className="at-input !h-10 w-full text-xs px-3 bg-card border-border-hairline sm:w-auto"
                aria-label="按部门筛选"
              >
                {DEPARTMENT_FILTER_OPTIONS.map((dept) => (
                  <option key={dept} value={dept}>
                    {dept}
                  </option>
                ))}
              </select>

              {/* 状态筛选 */}
              <div
                role="group"
                aria-label="按在岗状态过滤"
                className="at-seg -mx-1 flex w-full max-w-full justify-start gap-1 overflow-x-auto px-1 sm:mx-0 sm:w-auto sm:px-0"
              >
                {(
                  [
                    { key: 'all', label: '全部员工' },
                    { key: 'internship', label: '实习考核期' },
                    { key: 'production', label: '正式在岗' },
                    { key: 'retired', label: '已停用' },
                  ] as const
                ).map((opt) => (
                  <button
                    key={opt.key}
                    type="button"
                    role="tab"
                    aria-selected={statusFilter === opt.key}
                    className="at-seg-item !h-10 shrink-0 whitespace-nowrap text-xs"
                    onClick={() => setStatusFilter(opt.key)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <div role="group" aria-label="切换视图" className="at-seg shrink-0 self-start sm:self-auto">
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === 'list'}
                  className="at-seg-item !h-10 shrink-0 whitespace-nowrap px-3"
                  onClick={() => setView('list')}
                >
                  <Rows3 className="h-3.5 w-3.5" aria-hidden="true" />
                  列表
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={view === 'grid'}
                  className="at-seg-item !h-10 shrink-0 whitespace-nowrap px-3"
                  onClick={() => setView('grid')}
                >
                  <LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />
                  卡片
                </button>
              </div>

              <button
                type="button"
                className="at-btn at-btn-quiet"
                onClick={() => void fetchProfiles()}
                disabled={loading}
                aria-label="刷新花名册"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
                刷新
              </button>
            </div>
          </div>

          {loadError ? (
            <div role="alert" className="at-surface-flat flex items-start gap-3 rounded-[10px] p-4">
              <Users className="mt-0.5 h-4 w-4 shrink-0 at-bad" aria-hidden="true" />
              <div className="flex-1">
                <p className="at-sm at-bad">{loadError}</p>
                <button type="button" className="at-link at-sm mt-2" onClick={() => void fetchProfiles()}>
                  重新加载
                </button>
              </div>
            </div>
          ) : loading && profiles.length === 0 ? (
            <p className="at-sm at-subtle py-20 text-center">正在同步花名册数据…</p>
          ) : profiles.length === 0 ? (
            <div className="at-surface-flat rounded-[10px] px-6 py-16 text-center">
              <Users className="mx-auto mb-3 h-8 w-8 at-subtle" aria-hidden="true" />
              <p className="at-body" style={{ color: 'var(--at-ink)' }}>
                还没有数字员工档案
              </p>
              <p className="at-meta at-subtle mt-1">创建第一位员工，填写岗位与职责边界。</p>
              <button
                type="button"
                className="at-btn at-btn-quiet mt-5"
                onClick={() => {
                  setCreateAttempted(false)
                  setShowCreate(true)
                }}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                新增数字员工
              </button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="at-surface-flat rounded-[10px] px-6 py-16 text-center">
              <Users className="mx-auto mb-3 h-8 w-8 at-subtle" aria-hidden="true" />
              <p className="at-body" style={{ color: 'var(--at-ink)' }}>没有符合条件的员工</p>
              <p className="at-meta at-subtle mt-1">清除搜索和筛选后查看全部员工。</p>
              <button
                type="button"
                className="at-btn at-btn-quiet mt-5"
                onClick={() => {
                  setSearch('')
                  setStatusFilter('all')
                  setDepartmentFilter(ALL_DEPARTMENTS_FILTER)
                }}
              >
                清除筛选
              </button>
            </div>
          ) : view === 'list' ? (
            <>
              {/* 名录：12 栏静默表头 + 发丝线分行 */}
              <div className="at-surface-flat overflow-x-auto overflow-y-hidden rounded-[10px]">
                <div className="at-meta at-muted grid min-w-[880px] grid-cols-12 gap-4 px-6 py-3 at-hairline-b">
                  <div className="col-span-3">员工信息</div>
                  <div className="col-span-2">工号</div>
                  <div className="col-span-2">归属部门与岗位</div>
                  <div className="col-span-1">绩效评分</div>
                  <div className="col-span-1 text-right">状态</div>
                  <div className="col-span-3 text-right">快捷操作</div>
                </div>
                <div className="min-w-[880px]">
                  {pageRows.map((p) => {
                    const status = STATUS_META[p.employment_status] ?? STATUS_FALLBACK
                    const score = performanceScoreDisplay(p.performance_score)
                    return (                      <div
                        key={p.id}
                        onClick={() => setSelectedProfile(p)}
                        className="at-row at-hairline-b grid grid-cols-12 items-center gap-4 px-6 py-3.5 last:border-b-0 cursor-pointer"
                      >
                        <div className="col-span-3 flex min-w-0 items-center gap-3">
                          <div className="at-plate">{p.display_name.slice(0, 1)}</div>
                          <span
                            className="truncate text-[15px] font-semibold"
                            style={{ color: 'var(--at-ink)' }}
                          >
                            {p.display_name}
                          </span>
                        </div>
                        <div className="at-mono at-muted col-span-2 truncate">{formatEmployeeBadge(p.employee_badge, p.id)}</div>
                        <div className="at-sm at-muted col-span-2 truncate">
                          {p.job_title} · {p.department}
                        </div>
                        <div className="col-span-1 flex items-center gap-3">
                          <div className="at-hp-track w-[40px]">
                            <div
                              className={`at-hp-fill ${score.tone ? `at-hp-fill-${score.tone}` : ''}`}
                              style={{ width: `${score.width}%` }}
                            />
                          </div>
                          <span className="at-sm font-medium" style={{ color: 'var(--at-ink)' }}>
                            {score.label}
                          </span>
                        </div>
                        <div className="col-span-1 flex items-center justify-end gap-1.5">
                          <span className={`at-dot at-dot-${status.tone}`} aria-hidden="true" />
                          <span className={`at-sm at-${status.tone} font-medium`}>{status.label}</span>
                        </div>
                        <div className="col-span-3 flex items-center justify-end gap-3">
                          <ChatCta profile={p} navigate={navigate} label="找她聊聊" />
                          <Link
                            to={`/workforce/profile/${p.id}`}
                            className="at-link at-sm"
                            onClick={(e) => e.stopPropagation()}
                          >
                            档案
                          </Link>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            </>
          ) : (
            /* 卡片式矩阵：同一份数据的另一种排布 */
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
              {pageRows.map((p) => {
                const status = STATUS_META[p.employment_status] ?? STATUS_FALLBACK
                const score = performanceScoreDisplay(p.performance_score)
                const allowed = p.duty_boundaries?.allowed ?? []
                const forbidden = p.duty_boundaries?.forbidden ?? []
                return (
                  <article
                    key={p.id}
                    onClick={() => setSelectedProfile(p)}
                    className="at-surface flex flex-col overflow-hidden rounded-[10px] cursor-pointer transition-colors hover:border-[var(--at-muted,#6B6B66)]"
                  >
                    <div className="flex items-center justify-between gap-2 px-5 py-3 at-hairline-b">
                      <span className="at-mono at-muted truncate">{formatEmployeeBadge(p.employee_badge, p.id)}</span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        <span className={`at-dot at-dot-${status.tone}`} aria-hidden="true" />
                        <span className={`at-sm at-${status.tone}`}>{status.label}</span>
                      </span>
                    </div>
                    <div className="flex flex-1 flex-col gap-4 px-5 py-4">
                      <div className="flex items-center gap-3">
                        <div className="at-plate">{p.display_name.slice(0, 1)}</div>
                        <div className="min-w-0">
                          <Link
                            to={`/workforce/profile/${p.id}`}
                            className="block truncate text-[15px] font-semibold hover:underline"
                            style={{ color: 'var(--at-ink)' }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            {p.display_name}
                          </Link>
                          <p className="at-meta at-muted truncate">{p.job_title}</p>
                        </div>
                      </div>

                      <div>
                        <div className="mb-1.5 flex items-baseline justify-between">
                          <span className="at-meta at-muted">综合绩效 / HP</span>
                          <span className="at-sm font-medium" style={{ color: 'var(--at-ink)' }}>
                            {score.label}
                          </span>
                        </div>
                        <div className="at-hp-track">
                          <div
                            className={`at-hp-fill ${score.tone ? `at-hp-fill-${score.tone}` : ''}`}
                            style={{ width: `${score.width}%` }}
                          />
                        </div>
                      </div>

                      <dl className="at-meta at-muted space-y-1">
                        <div className="flex justify-between gap-3">
                          <dt className="shrink-0">授权规程</dt>
                          <dd style={{ color: 'var(--at-ink)' }}>{p.authorized_flows?.length ?? 0} 项</dd>
                        </div>
                        <div className="flex justify-between gap-3">
                          <dt className="shrink-0">知识范围</dt>
                          <dd style={{ color: 'var(--at-ink)' }}>{p.accessible_knowledge_buckets?.length ?? 0} 个</dd>
                        </div>
                        <div className="flex justify-between gap-3">
                          <dt className="shrink-0">授权工具</dt>
                          <dd style={{ color: 'var(--at-ink)' }}>{p.authorized_tools?.length ?? 0} 个</dd>
                        </div>
                      </dl>

                      {forbidden.length > 0 && (
                        <p className="at-meta at-subtle truncate">禁：{forbidden.join('、')}</p>
                      )}
                      {allowed.length > 0 && (
                        <p className="at-meta at-subtle truncate">许：{allowed.join('、')}</p>
                      )}
                    </div>
                    <div className="flex items-center justify-between gap-3 px-5 py-3 at-hairline-t">
                      <div className="flex items-center gap-3">
                        <Link
                          to={`/workforce/profile/${p.id}`}
                          className="at-link at-sm"
                          onClick={(e) => e.stopPropagation()}
                        >
                          查看岗位档案
                        </Link>
                        <ChatCta profile={p} navigate={navigate} />
                      </div>
                      <button
                        type="button"
                        className="at-link at-sm flex items-center gap-1"
                        onClick={(e) => {
                          e.stopPropagation()
                          setProbeTarget(p)
                          setProbeIntent('')
                          setProbeResult(null)
                        }}
                      >
                        <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                        边界探针
                      </button>
                    </div>
                  </article>
                )
              })}
            </div>
          )}

          {filtered.length > 0 && (
            /* 底部汇总行：真实计数 + 翻页 */
            <div className="at-sm at-muted flex flex-wrap items-center justify-between gap-3 pt-2 at-hairline-t">
              <span>
                在岗 {counts.production + counts.active} · 影子 {counts.shadow} · 实习{' '}
                {counts.probation} · 停用 {counts.retired + counts.suspended} · 平均绩效{' '}
                {avgScore}
              </span>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <span className="at-meta at-subtle">
                  每页 {PAGE_SIZE} 条 · 筛选出 {filtered.length} / {profiles.length} 人
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="at-btn at-btn-quiet min-h-10"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={currentPage <= 1}
                  >
                    上一页
                  </button>
                  <span className="at-meta at-subtle">
                    {currentPage} / {totalPages}
                  </span>
                  <button
                    type="button"
                    className="at-btn at-btn-quiet min-h-10"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={currentPage >= totalPages}
                  >
                    下一页
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* 新建编制浮层 */}
        <ModalShell
          open={showCreate}
          onClose={() => setShowCreate(false)}
          labelledBy="wf-create-title"
          // 关闭遮罩会静默丢弃整份草稿，编制表单不允许这种误触
          closeOnOverlayClick={false}
          // Escape 同理：一次按键就丢光整份草稿，正是加遮罩守卫要防的那件事
          closeOnEscape={false}
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          panelClassName="at-paper w-full max-w-2xl rounded-[10px] border p-6 shadow-xl max-h-[90vh] overflow-y-auto"
        >
          <div className="mb-4 flex items-center justify-between">
            <h2 id="wf-create-title" className="at-section">
              编译新数字员工
            </h2>
            <button type="button" className="at-btn at-btn-quiet" onClick={() => setShowCreate(false)} aria-label="关闭">
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          <p className="at-meta at-muted mb-5">
            工号由系统按「ATE-年份-部门码-序号」自动授予，无需手填。姓名、岗位为必填项。
          </p>

          <div className="at-sm space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label htmlFor="wf-name" className="at-meta at-muted mb-1 block">
                  员工姓名 <span className="at-bad">*</span>
                </label>
                <input
                  id="wf-name"
                  className="at-input"
                  value={draft.display_name}
                  onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
                  placeholder="如：苏策"
                  maxLength={64}
                  required
                  aria-invalid={createAttempted && draft.display_name.trim().length === 0}
                  aria-describedby="wf-name-error"
                />
                <p id="wf-name-error" className="at-meta at-bad mt-1">
                  {/* 只在「用户已经点过创建」之后才报红：一开抽屉就显示两条红字，
                      等于还没开始输入就先被判定为填错 */}
                  {createAttempted && draft.display_name.trim().length === 0
                    ? '请填写员工姓名'
                    : ''}
                </p>
              </div>
              <div>
                <label htmlFor="wf-title" className="at-meta at-muted mb-1 block">
                  岗位职责职务 <span className="at-bad">*</span>
                </label>
                <input
                  id="wf-title"
                  className="at-input"
                  value={draft.job_title}
                  onChange={(e) => setDraft({ ...draft, job_title: e.target.value })}
                  placeholder="如：售前解决方案架构师"
                  maxLength={64}
                  required
                  aria-invalid={createAttempted && draft.job_title.trim().length === 0}
                  aria-describedby="wf-title-error"
                />
                <p id="wf-title-error" className="at-meta at-bad mt-1">
                  {createAttempted && draft.job_title.trim().length === 0 ? '请填写岗位职责' : ''}
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label htmlFor="wf-dept" className="at-meta at-muted mb-1 block">
                  归属部门
                </label>
                <select
                  id="wf-dept"
                  className="at-input"
                  value={draft.department}
                  onChange={(e) => setDraft({ ...draft, department: e.target.value })}
                >
                  {/* 选项直接复用筛选用的部门表，避免新建后被部门筛选“藏起来” */}
                  {DEPARTMENTS.map((dept) => (
                    <option key={dept} value={dept}>
                      {dept}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="wf-agent" className="at-meta at-muted mb-1 block">
                  关联底层 Agent
                </label>
                <select
                  id="wf-agent"
                  className="at-input"
                  value={draft.agent_id ?? ''}
                  onChange={(e) => setDraft({ ...draft, agent_id: e.target.value || undefined })}
                >
                  <option value="">暂不关联（创建后不可再补绑）</option>
                  {bindableAgents.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
                <p className="at-meta at-subtle mt-1">
                  绑定后卡片上的「找她聊聊」才能直达 /chat 对话；未绑定的编制无法发起对话。
                </p>
              </div>
            </div>

            <div>
              <label htmlFor="wf-tone" className="at-meta at-muted mb-1 block">
                服务语调风格
              </label>
              <input
                id="wf-tone"
                className="at-input"
                value={draft.tone_style ?? ''}
                onChange={(e) => setDraft({ ...draft, tone_style: e.target.value })}
                placeholder="如：专业、耐心、结论先行"
                list="wf-tone-presets"
                maxLength={64}
              />
              <datalist id="wf-tone-presets">
                {TONE_PRESETS.map((t) => (
                  <option key={t} value={t} />
                ))}
              </datalist>
            </div>

            <fieldset className="at-hairline-t pt-4">
              <legend className="at-meta at-muted mb-2">授权允许事项</legend>
              <div className="flex gap-2">
                <input
                  className="at-input"
                  value={allowedInput}
                  onChange={(e) => setAllowedInput(e.target.value)}
                  placeholder="添加允许履约的事项"
                  aria-label="添加允许履约的事项"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      addBoundary('allowed')
                    }
                  }}
                />
                <button
                  type="button"
                  className="at-btn at-btn-quiet shrink-0"
                  onClick={() => addBoundary('allowed')}
                  disabled={allowedInput.trim().length === 0}
                >
                  添加
                </button>
              </div>
              {allowedList.length === 0 ? (
                <p className="at-meta at-subtle mt-2">未设置时按公司通用 SOP 执行。</p>
              ) : (
                <ul className="at-meta at-muted mt-2 space-y-1">
                  {allowedList.map((item, i) => (
                    <li key={`${item}-${i}`} className="flex items-center gap-2">
                      <span className="flex-1 truncate">· {item}</span>
                      <button
                        type="button"
                        className="at-link at-subtle shrink-0"
                        onClick={() => removeBoundary('allowed', i)}
                        aria-label={`移除允许事项：${item}`}
                      >
                        移除
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </fieldset>

            <fieldset className="at-hairline-t pt-4">
              <legend className="at-meta at-muted mb-2">严禁越权事项</legend>
              <div className="flex gap-2">
                <input
                  className="at-input"
                  value={forbiddenInput}
                  onChange={(e) => setForbiddenInput(e.target.value)}
                  placeholder="添加严禁越权的事项"
                  aria-label="添加严禁越权的事项"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      addBoundary('forbidden')
                    }
                  }}
                />
                <button
                  type="button"
                  className="at-btn at-btn-quiet shrink-0"
                  onClick={() => addBoundary('forbidden')}
                  disabled={forbiddenInput.trim().length === 0}
                >
                  添加
                </button>
              </div>
              {forbiddenList.length === 0 ? (
                <p className="at-meta at-subtle mt-2">未设置时不做额外越权拦截。</p>
              ) : (
                <ul className="at-meta at-muted mt-2 space-y-1">
                  {forbiddenList.map((item, i) => (
                    <li key={`${item}-${i}`} className="flex items-center gap-2">
                      <span className="flex-1 truncate">· {item}</span>
                      <button
                        type="button"
                        className="at-link at-subtle shrink-0"
                        onClick={() => removeBoundary('forbidden', i)}
                        aria-label={`移除禁止事项：${item}`}
                      >
                        移除
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </fieldset>
          </div>

          <div className="mt-6 flex items-center justify-between gap-3 pt-4 at-hairline-t">
            <p className="at-meta at-subtle">
              工号由系统自动授予，初始状态为「{STATUS_META.shadow.label}」。
            </p>
            <div className="flex items-center gap-2">
              <button type="button" className="at-btn at-btn-quiet" onClick={() => setShowCreate(false)} disabled={creating}>
                取消
              </button>
              <button
                type="button"
                className="at-btn at-btn-primary"
                onClick={handleCreate}
                disabled={creating || !canCreate}
                aria-busy={creating}
              >
                {creating ? '编译中…' : '确认创建编制'}
              </button>
            </div>
          </div>
        </ModalShell>

        {/* 边界探针浮层：真实调用 check-boundary */}
        <ModalShell
          open={probeTarget !== null}
          onClose={() => setProbeTarget(null)}
          labelledBy="wf-probe-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          panelClassName="at-paper w-full max-w-lg rounded-[10px] border p-6 shadow-xl"
        >
          <div className="mb-4 flex items-center justify-between">
            <h2 id="wf-probe-title" className="at-section">
              权责边界实时探针
            </h2>
            <button type="button" className="at-btn at-btn-quiet" onClick={() => setProbeTarget(null)} aria-label="关闭">
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          {probeTarget && (
            <p className="at-sm at-muted mb-4">
              正在针对 <span style={{ color: 'var(--at-ink)' }}>{probeTarget.display_name}</span>（
              <span className="at-mono">{formatEmployeeBadge(probeTarget.employee_badge, probeTarget.id)}</span> / {probeTarget.job_title}）校验越权防御。
            </p>
          )}

          <label htmlFor="wf-probe-input" className="at-meta at-muted mb-1 block">
            模拟动作意图
          </label>
          <input
            id="wf-probe-input"
            className="at-input"
            value={probeIntent}
            onChange={(e) => setProbeIntent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void runProbe()
            }}
            placeholder="如：直接向外部客户发送约束性报价"
          />

          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="at-btn at-btn-quiet" onClick={() => setProbeTarget(null)}>
              关闭
            </button>
            <button
              type="button"
              className="at-btn at-btn-primary"
              onClick={() => void runProbe()}
              disabled={probing || !probeIntent.trim()}
              aria-busy={probing}
            >
              {probing ? '校验中…' : '执行防线校验'}
            </button>
          </div>

          {probeResult && (
            <div className="mt-4 at-hairline-t pt-4">
              <p className={`at-sm font-medium ${probeResult.allowed ? 'at-ok' : 'at-bad'}`}>
                {probeResult.allowed ? '准予履约 · 合规授权通过' : '拦截阻断 · 触碰职责越权边界'}
              </p>
              <p className="at-meta at-muted mt-1">{probeResult.reason}</p>
              {probeResult.matched_boundary && (
                <p className="at-mono at-muted mt-1">命中规则：{probeResult.matched_boundary}</p>
              )}
            </div>
          )}
        </ModalShell>

        {/* Screen 03 岗位权责档案抽屉 */}
        <Drawer
          open={!!selectedProfile}
          onClose={closeProfileDrawer}
          title={selectedProfile ? `${selectedProfile.display_name} · ${isEditingDuties ? '编辑岗位职责' : '岗位权责档案'}` : '员工档案'}
          description={selectedProfile ? `${formatEmployeeBadge(selectedProfile.employee_badge, selectedProfile.id)} · ${selectedProfile.job_title}` : undefined}
          width={720}
          actions={selectedProfile ? (
            pendingEditExit ? (
              <div key="confirm" className="flex w-full flex-wrap items-center justify-end gap-2" role="alert">
                <span className="at-sm at-muted mr-auto">放弃未保存的职责修改？</span>
                <button id="wf-edit-keep" type="button" className="at-btn at-btn-quiet" onClick={(event) => { event.preventDefault(); setPendingEditExit(null) }}>
                  继续编辑
                </button>
                <button type="button" className="at-btn at-btn-primary" onClick={() => finishDutyEdit(pendingEditExit)}>
                  放弃修改
                </button>
              </div>
            ) : editDraft ? (
              <div key="edit" className="flex w-full flex-wrap items-center justify-end gap-2">
                <button type="button" className="at-btn at-btn-quiet" onClick={() => requestDutyExit('detail')} disabled={savingEdit}>
                  取消
                </button>
                <button type="submit" form="wf-duty-edit-form" className="at-btn at-btn-primary" disabled={savingEdit} aria-busy={savingEdit}>
                  {savingEdit ? '保存中…' : '保存职责'}
                </button>
              </div>
            ) : (
              <div key="detail" className="flex w-full flex-wrap items-center justify-end gap-2">
                <button ref={editButtonRef} type="button" className="at-btn at-btn-quiet" onClick={beginDutyEdit}>编辑职责</button>
                <button type="button" className="at-btn at-btn-primary" onClick={closeProfileDrawer}>完成</button>
              </div>
            )
          ) : undefined}
        >
          {selectedProfile && (editDraft ? (
            <form
              id="wf-duty-edit-form"
              className="space-y-6 py-2 text-text-primary"
              noValidate
              onSubmit={(event) => { event.preventDefault(); void saveDutyEdit() }}
            >
              <p className="at-sm at-muted">明确这个岗位负责什么，以及不能自行处理的事项。</p>
              <div>
                <label htmlFor="wf-edit-title" className="at-meta at-muted mb-1 block">岗位名称 <span className="at-bad">*</span></label>
                <input
                  id="wf-edit-title"
                  ref={editJobTitleRef}
                  className="at-input"
                  value={editDraft.job_title}
                  onChange={(event) => setEditDraft(current => current ? { ...current, job_title: event.target.value } : current)}
                  maxLength={64}
                  disabled={savingEdit || pendingEditExit !== null}
                  aria-invalid={editAttempted && editDraft.job_title.trim().length === 0}
                  aria-describedby="wf-edit-title-error"
                />
                <p id="wf-edit-title-error" className="at-meta at-bad mt-1" role={editAttempted && !editDraft.job_title.trim() ? 'alert' : undefined}>
                  {editAttempted && !editDraft.job_title.trim() ? '请填写岗位名称' : ''}
                </p>
              </div>
              {(['allowed', 'forbidden'] as const).map((kind) => {
                const items = editDraft.duty_boundaries[kind]
                const label = kind === 'allowed' ? '可执行事项' : '禁止越权事项'
                const input = kind === 'allowed' ? editAllowedInput : editForbiddenInput
                return (
                  <fieldset key={kind} className="at-hairline-t pt-4" disabled={savingEdit || pendingEditExit !== null}>
                    <legend className="at-meta at-muted mb-2">{label}</legend>
                    {items.length ? (
                      <ul className="space-y-2 mb-3">
                        {items.map((item, index) => (
                          <li key={`${kind}-${index}`} className="flex items-start justify-between gap-3 at-sm">
                            <span className="min-w-0 break-words">{item}</span>
                            <button type="button" className="at-link shrink-0" onClick={() => removeEditBoundary(kind, index)} aria-label={`移除${label}：${item}`}>
                              移除
                            </button>
                          </li>
                        ))}
                      </ul>
                    ) : <p className="at-meta at-subtle mb-3">未设置；保存时保持空列表。</p>}
                    <div className="flex gap-2">
                      <input
                        className="at-input min-w-0"
                        aria-label={`添加${label}`}
                        value={input}
                        onChange={(event) => kind === 'allowed' ? setEditAllowedInput(event.target.value) : setEditForbiddenInput(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault()
                            appendEditBoundary(kind)
                          }
                        }}
                      />
                      <button type="button" className="at-btn at-btn-quiet shrink-0" onClick={() => appendEditBoundary(kind)} disabled={!input.trim()}>
                        添加
                      </button>
                    </div>
                  </fieldset>
                )
              })}
              {editError && <p className="at-sm at-bad" role="alert">{editError}</p>}
            </form>
          ) : (
            <div className="space-y-6 py-2 text-text-primary">
              {/* Profile Card Header */}
              <div className="flex items-center justify-between pb-4 at-hairline-b">
                <div className="flex items-center gap-3">
                  <div className="at-plate w-10 h-10 text-base font-semibold">
                    {selectedProfile.display_name.slice(0, 1)}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-lg font-semibold text-text-primary">{selectedProfile.display_name}</span>
                    <span className="at-mono text-xs px-2 rounded border border-border-default bg-surface-alt text-text-secondary">
                      {formatEmployeeBadge(selectedProfile.employee_badge, selectedProfile.id)}
                    </span>
                    </div>
                    <p className="at-meta at-muted mt-0.5">
                      {selectedProfile.job_title} · {selectedProfile.department}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`at-dot at-dot-${STATUS_META[selectedProfile.employment_status]?.tone ?? STATUS_FALLBACK.tone}`} />
                  <span className="text-sm font-medium">{STATUS_META[selectedProfile.employment_status]?.label ?? STATUS_FALLBACK.label}</span>
                </div>
              </div>

              {/* 综合绩效 / HP 血量 */}
              <div className="p-4 rounded-[10px] bg-surface-alt/40 border border-border-subtle">
                <div className="flex items-baseline justify-between mb-2">
                  <span className="at-meta at-muted">综合绩效指数 (HP)</span>
                  <div className="flex items-baseline gap-1">
                    <span className="text-2xl font-bold tabular-nums text-text-primary">
                      {selectedScore.label}
                    </span>
                    {selectedScore.score !== null && (
                      <span className="at-meta at-subtle">/ 100</span>
                    )}
                  </div>
                </div>
                <div className="at-hp-track">
                  <div
                    className={`at-hp-fill ${selectedScore.tone ? `at-hp-fill-${selectedScore.tone}` : ''}`}
                    style={{ width: `${selectedScore.width}%` }}
                  />
                </div>
              </div>

              {/* 岗位边界：两栏 (可执行事项 / 禁止越权事项) */}
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold tracking-tight text-text-primary">岗位权责边界</h3>
                  <span className="at-meta at-subtle">实时审计合规中</span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* 可执行事项 */}
                  <div className="p-4 rounded-[10px] border border-border-subtle bg-card">
                    <div className="flex items-center justify-between pb-2 mb-2 border-b border-border-subtle">
                      <span className="text-xs font-semibold text-success tracking-wider">可执行事项</span>
                      <span className="at-meta at-subtle font-mono">{selectedProfile.duty_boundaries?.allowed?.length ?? 0} 条</span>
                    </div>
                    <ul className="space-y-2">
                      {(selectedProfile.duty_boundaries?.allowed ?? []).map((item, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-text-secondary leading-relaxed">
                          <span className="w-1.5 h-1.5 rounded-full bg-success mt-1.5 shrink-0" />
                          <span>{item}</span>
                        </li>
                      ))}
                      {!selectedProfile.duty_boundaries?.allowed?.length && <li className="at-meta at-subtle">尚未设置可执行事项</li>}
                    </ul>
                  </div>

                  {/* 禁止越权事项 */}
                  <div className="p-4 rounded-[10px] border border-border-subtle bg-card">
                    <div className="flex items-center justify-between pb-2 mb-2 border-b border-border-subtle">
                      <span className="text-xs font-semibold text-danger tracking-wider">禁止越权事项</span>
                      <span className="at-meta at-subtle font-mono">{selectedProfile.duty_boundaries?.forbidden?.length ?? 0} 条</span>
                    </div>
                    <ul className="space-y-2">
                      {(selectedProfile.duty_boundaries?.forbidden ?? []).map((item, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-text-secondary leading-relaxed">
                          <span className="w-1.5 h-1.5 rounded-full bg-danger mt-1.5 shrink-0" />
                          <span>{item}</span>
                        </li>
                      ))}
                      {!selectedProfile.duty_boundaries?.forbidden?.length && <li className="at-meta at-subtle">尚未设置禁止事项</li>}
                    </ul>
                  </div>
                </div>
              </div>

              {/* 权责边界动态探针校验 */}
              <div className="p-4 rounded-[10px] border border-border-subtle bg-surface-alt/30">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-text-primary flex items-center gap-1.5">
                    <ShieldCheck className="w-4 h-4 text-brand-500" />
                    权责边界动态探针
                  </span>
                  <span className="at-meta at-subtle">SafeHarness 沙箱探针</span>
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={probeIntent}
                    onChange={(e) => setProbeIntent(e.target.value)}
                    placeholder="输入拟执行动作探针，如：直接修改合同法务条款"
                    className="at-input text-xs"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void runProbeForProfile(selectedProfile.id)
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => void runProbeForProfile(selectedProfile.id)}
                    disabled={probing || !probeIntent.trim()}
                    aria-busy={probing}
                    className="at-btn at-btn-primary shrink-0 text-xs px-3"
                  >
                    {probing ? '校验中…' : '执行探测'}
                  </button>
                </div>
                {probeResult && (
                  <div
                    className="mt-3 rounded-[6px] px-3 py-2 text-xs at-hairline-b"
                    style={{ background: 'var(--at-sunk)' }}
                    role="status"
                  >
                    <div className={`font-semibold ${probeResult.allowed ? 'at-ok' : 'at-bad'}`}>
                      {probeResult.allowed ? '判定放行：在授权边界范围内' : '判定阻断：命中禁止越权规则'}
                    </div>
                    <p className="mt-1 text-text-secondary">{probeResult.reason}</p>
                    {probeResult.matched_boundary && (
                      <p className="mt-1 at-mono text-[11px] text-text-muted">命中规则：{probeResult.matched_boundary}</p>
                    )}
                  </div>
                )}
              </div>

              {/* 关联规程卡、知识范围与授权工具 */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 at-hairline-t pt-4">
                <div className="p-3 rounded border border-border-subtle bg-card">
                  <div className="at-meta at-muted mb-1">授权规程卡</div>
                  <div className="font-semibold text-sm text-text-primary">{selectedProfile.authorized_flows?.length ?? 0} 项规程</div>
                  <div className="mt-2 space-y-1">
                    {(selectedProfile.authorized_flows ?? []).slice(0, 3).map((f, i) => (
                      <div key={i} className="at-mono text-[11px] text-text-secondary truncate">{f}</div>
                    ))}
                  </div>
                </div>

                <div className="p-3 rounded border border-border-subtle bg-card">
                  <div className="at-meta at-muted mb-1">知识范围</div>
                  <div className="font-semibold text-sm text-text-primary">{selectedProfile.accessible_knowledge_buckets?.length ?? 0} 个知识桶</div>
                  <div className="mt-2 space-y-1">
                    {(selectedProfile.accessible_knowledge_buckets ?? []).slice(0, 3).map((b, i) => (
                      <div key={i} className="at-mono text-[11px] text-text-secondary truncate">{b}</div>
                    ))}
                  </div>
                </div>

                <div className="p-3 rounded border border-border-subtle bg-card">
                  <div className="at-meta at-muted mb-1">授权工具 / MCP</div>
                  <div className="font-semibold text-sm text-text-primary">{selectedProfile.authorized_tools?.length ?? 0} 个工具</div>
                  <div className="mt-2 space-y-1">
                    {(selectedProfile.authorized_tools ?? []).slice(0, 3).map((t, i) => (
                      <div key={i} className="at-mono text-[11px] text-text-secondary truncate">{t}</div>
                    ))}
                  </div>
                </div>
              </div>

              {/* 考核时间线：4 步水平轨道 */}
              <div className="at-hairline-t pt-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-text-primary">考核晋升时间线</h3>
                  <span className="at-meta at-subtle">
                    {stageLabel(selectedProfile.employment_status)}
                  </span>
                </div>
                <div className="relative flex items-center justify-between px-4 py-2">
                  <div className="absolute left-6 right-6 top-1/2 -translate-y-1/2 h-[1px] bg-border-hairline z-0" />
                  {PROBATION_STAGES.map((stage, idx) => {
                    const currentIdx = STAGE_INDEX[selectedProfile.employment_status] ?? 0
                    const isDone = idx < currentIdx
                    const isCurrent = idx === currentIdx
                    return (
                      <div key={stage} className="relative z-10 flex flex-col items-center">
                        <span
                          className={`w-2.5 h-2.5 rounded-full border-2 ${
                            isDone
                              ? 'bg-brand-500 border-brand-500'
                              : isCurrent
                              ? 'bg-white border-brand-500'
                              : 'bg-white border-border-hairline'
                          }`}
                        />
                        <span className={`at-meta mt-1.5 ${isCurrent ? 'font-semibold' : 'text-text-muted'}`}>
                          {stage}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* 抽屉底部动作 */}
              <div className="flex items-center justify-between gap-3 pt-4 at-hairline-t">
                <Link
                  to={`/workforce/profile/${selectedProfile.id}`}
                  className="at-btn at-btn-quiet text-xs"
                >
                  打开完整独立档案页
                </Link>
                <ChatCta
                  profile={selectedProfile}
                  navigate={navigate}
                  className="at-btn at-btn-quiet text-xs"
                />
              </div>
            </div>
          ))}
        </Drawer>
      </div>
    </Layout>
  )
}
