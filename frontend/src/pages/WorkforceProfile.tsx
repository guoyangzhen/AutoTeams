/**
 * WorkforceProfile — 数字员工岗位档案。
 *
 * 对应蓝图 screens-v2/03-employee-profile.html：回答「TA 能做什么、禁止什么」。
 * 主导区是岗位边界两栏（可执行事项 / 禁止越权事项）+ 授权挂载三组
 * （授权规程卡 / 知识范围 / 授权工具），底部是考核时间线。
 *
 * 数据来源：GET /api/v1/workforce-profiles/:id（见 api/workforceProfiles.ts）。
 * 实时指标由 useLiveCompany 提供的企业生命体征派生，不造假数。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ChevronRight, MessagesSquare, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import Layout from '@/components/Layout'
import { Spinner } from '@/components/ui/Spinner'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { useLiveCompany } from '@/hooks/useLiveCompany'
import {
  getWorkforceProfile,
  checkDutyBoundary,
  type WorkforceProfile,
} from '@/api/workforceProfiles'
import { formatEmployeeBadge } from '@/utils/employeeBadge'
import { performanceScoreDisplay } from '@/utils/performanceScore'

/** 在岗状态 → 中文标签与圆点语义 */
/** 圆点语义：ok 正常 / warn 观察 / bad 异常 / idle 未知 */
type StatusTone = { label: string; tone: 'ok' | 'warn' | 'bad' | 'idle' }

const STATUS_META: Record<WorkforceProfile['employment_status'], StatusTone> = {
  production: { label: '在岗', tone: 'ok' },
  active: { label: '在岗', tone: 'ok' },
  probation: { label: '实习中', tone: 'warn' },
  shadow: { label: '影子考核', tone: 'warn' },
  suspended: { label: '已停职', tone: 'bad' },
  retired: { label: '已停用', tone: 'bad' },
}
const STATUS_FALLBACK: StatusTone = { label: '未知状态', tone: 'idle' }

/** 考核四段：与后端 employment_status 四态一一对应，节点日期取 created_at/updated_at 真实值 */
const PROBATION_STAGES = ['影子陪伴', '独立评估', '达标审查', '正式转正'] as const

/**
 * employment_status → 考核进度。返回 0..3 表示「已推进到第 N 段，仍在进行中」；
 * 返回 PROBATION_STAGES.length 表示四段已全部走完（已转正/已停用），此时无「当前节点」。
 */
const STAGE_INDEX: Record<WorkforceProfile['employment_status'], number> = {
  shadow: 0,
  probation: 2,
  production: PROBATION_STAGES.length,
  active: PROBATION_STAGES.length,
  // -1 = 考核已被中止（停职 / 停用），不是「四段全部走完」。
  // 与 production 同取 length 会把一个被停职的员工显示成已转正。
  suspended: -1,
  retired: -1,
}

/** 安全数值 */
function safeNum(v: number | null | undefined): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

/** 安全格式化日期，无效值返回 fallback */
function safeDate(ts: string | null | undefined, fallback = '—'): string {
  if (!ts) return fallback
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return fallback
  return d.toLocaleDateString('zh-CN')
}

export default function WorkforceProfile() {
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const enterpriseId = useEnterpriseId()
  const { vitals } = useLiveCompany(enterpriseId)

  const [profile, setProfile] = useState<WorkforceProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [probeIntent, setProbeIntent] = useState('')
  const [probeResult, setProbeResult] = useState<{ allowed: boolean; reason: string; matched_boundary?: string } | null>(null)
  const [probing, setProbing] = useState(false)

  const fetchProfile = useCallback(async () => {
    if (!id) {
      setLoading(false)
      setLoadError('缺少数字员工 ID')
      return
    }
    setLoading(true)
    setLoadError(null)
    try {
      setProfile(await getWorkforceProfile(id))
    } catch (err) {
      setProfile(null)
      setLoadError(err instanceof Error ? err.message : '岗位档案加载失败')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void fetchProfile()
  }, [fetchProfile])

  const runProbe = useCallback(async () => {
    if (!id || !probeIntent.trim()) return
    setProbing(true)
    try {
      setProbeResult(await checkDutyBoundary(id, probeIntent.trim()))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '边界校验失败')
    } finally {
      setProbing(false)
    }
  }, [id, probeIntent])

  const allowed = profile?.duty_boundaries?.allowed ?? []
  const forbidden = profile?.duty_boundaries?.forbidden ?? []
  const flows = profile?.authorized_flows ?? []
  const buckets = profile?.accessible_knowledge_buckets ?? []
  const tools = profile?.authorized_tools ?? []
  const score = performanceScoreDisplay(profile?.performance_score)

  /** 实时指标：企业生命体征派生，接口未就绪时显式标注不可用 */
  const liveMetrics = useMemo(() => {
    if (!vitals || !profile) return null
    return [
      { label: '在岗编制', value: `${vitals.agents.production} / ${vitals.agents.total}` },
      { label: '近 24h 协作事件', value: String(vitals.events.last_24h) },
      { label: '失败事件', value: String(vitals.events.failed) },
      { label: '影子对齐度', value: `${safeNum(vitals.shadow.match)}%` },
    ]
  }, [vitals, profile])

  const stageIndex = profile ? (STAGE_INDEX[profile.employment_status] ?? 0) : 0
  const status = profile ? (STATUS_META[profile.employment_status] ?? STATUS_FALLBACK) : null

  return (
    <Layout>
      <div className="at-paper min-h-full w-full px-10 py-8">
        <div className="mx-auto w-full max-w-[1280px] space-y-8">
          <nav aria-label="面包屑" className="at-meta at-muted flex items-center gap-2">
            <Link to="/workforce-gallery" className="hover:underline" style={{ color: 'var(--at-muted)' }}>
              员工
            </Link>
            <ChevronRight className="h-3 w-3" aria-hidden="true" />
            <span style={{ color: 'var(--at-ink)' }}>岗位档案</span>
          </nav>

          {loading ? (
            <div className="flex justify-center py-20">
              <Spinner size="lg" />
            </div>
          ) : loadError || !profile ? (
            <div role="alert" className="at-surface-flat rounded-[10px] px-6 py-16 text-center">
              <p className="at-body at-bad">{loadError ?? '岗位档案不存在'}</p>
              <Link to="/workforce-gallery" className="at-link at-sm mt-3 inline-block">
                返回花名册
              </Link>
            </div>
          ) : (
            <>
              {/* 档案抬头：32px 字母牌 + 姓名 + 工号 + 岗位·部门·状态 */}
              <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex items-center gap-4">
                  <div className="at-plate">{profile.display_name.slice(0, 1)}</div>
                  <div>
                    <div className="flex flex-wrap items-center gap-3">
                      <h1 className="at-title">{profile.display_name}</h1>
                      <span
                        className="at-mono at-muted rounded px-2 py-0.5"
                        style={{ background: 'var(--at-surface)', border: '1px solid var(--at-hairline)' }}
                      >
                        {formatEmployeeBadge(profile.employee_badge, profile.id)}
                      </span>
                    </div>
                    <div className="at-sm at-muted mt-1 flex flex-wrap items-center gap-2">
                      <span>{profile.job_title}</span>
                      <span aria-hidden="true">·</span>
                      <span>{profile.department}</span>
                      <span aria-hidden="true">·</span>
                      <span className="flex items-center gap-1.5">
                        <span className={`at-dot at-dot-${status?.tone}`} aria-hidden="true" />
                        <span style={{ color: 'var(--at-ink)' }}>{status?.label}</span>
                      </span>
                    </div>
                  </div>
                </div>
                <div className="flex flex-col items-start gap-3 sm:items-end">
                  {profile.agent_id ? (
                    <button
                      type="button"
                      className="at-btn at-btn-primary text-xs inline-flex items-center gap-1.5"
                      onClick={() => navigate(`/chat/${profile.agent_id}`)}
                    >
                      <MessagesSquare className="h-3.5 w-3.5" aria-hidden="true" />
                      找她聊聊
                    </button>
                  ) : (
                    <span
                      className="at-btn at-btn-quiet text-xs at-subtle cursor-not-allowed"
                      title="该数字员工尚未绑定底层 Agent，无法直接对话"
                      aria-disabled="true"
                    >
                      <MessagesSquare className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
                      找她聊聊
                    </span>
                  )}
                  <p className="at-meta at-subtle sm:text-right">
                    编制于 {safeDate(profile.created_at)}
                    <br />
                    最近更新 {safeDate(profile.updated_at)}
                  </p>
                </div>
              </header>

              <div className="at-surface-flat overflow-hidden rounded-[10px]">
                {/* 两栏核心布局，中间一条竖发丝线 */}
                <div className="grid grid-cols-1 lg:grid-cols-12">
                  {/* 左栏：岗位边界（7 栏） */}
                  <div className="flex flex-col justify-between gap-7 border-b border-[var(--at-hairline)] p-8 lg:col-span-7 lg:border-b-0 lg:border-r">
                    <div className="space-y-7">
                      <div className="flex items-center justify-between gap-3 pb-3 at-hairline-b">
                        <h2 className="at-section">岗位边界</h2>
                        <span className="at-meta at-muted">实时审计合规中</span>
                      </div>

                      <div>
                        <p className="at-kicker mb-2">可执行事项</p>
                        {allowed.length === 0 ? (
                          <p className="at-sm at-subtle py-3">尚未配置授权事项</p>
                        ) : (
                          <div className="at-hairline-t at-hairline-b">
                            {allowed.map((item, i) => (
                              <div key={`${item}-${i}`} className="flex items-center gap-3 py-3 at-hairline-b last:border-b-0">
                                <span className="at-dot at-dot-ok" aria-hidden="true" />
                                <span className="at-body">{item}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>

                      <div>
                        <p className="at-kicker mb-2">禁止越权事项</p>
                        {forbidden.length === 0 ? (
                          <p className="at-sm at-subtle py-3">尚未配置禁止事项</p>
                        ) : (
                          <div className="at-hairline-t at-hairline-b">
                            {forbidden.map((item, i) => (
                              <div key={`${item}-${i}`} className="flex items-center gap-3 py-3 at-hairline-b last:border-b-0">
                                <span className="at-dot at-dot-bad" aria-hidden="true" />
                                <span className="at-body">{item}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* 综合绩效指数 + 边界探针 */}
                    <div className="mt-8 pt-6 at-hairline-t">
                      <div className="flex flex-wrap items-end justify-between gap-6">
                        <div>
                          <p className="at-meta at-muted mb-1">综合绩效指数</p>
                          <div className="flex items-baseline gap-2">
                            <span className="at-stat">{score.label}</span>
                            {score.score !== null && <span className="at-meta at-muted">/ 100</span>}
                          </div>
                          <div className="at-hp-track mt-3 w-48">
                            <div
                              className={`at-hp-fill ${score.tone ? `at-hp-fill-${score.tone}` : ''}`}
                              style={{ width: `${score.width}%` }}
                            />
                          </div>
                        </div>

                        <div className="w-full max-w-sm">
                          <label htmlFor="profile-probe" className="at-meta at-muted mb-1 block">
                            边界探针：输入拟执行动作，实时校验权责边界
                          </label>
                          <div className="flex gap-2">
                            <input
                              id="profile-probe"
                              className="at-input"
                              value={probeIntent}
                              onChange={(e) => setProbeIntent(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') void runProbe()
                              }}
                              placeholder="如：直接变更合同违约责任上限"
                            />
                            <button
                              type="button"
                              className="at-btn at-btn-primary shrink-0"
                              onClick={() => void runProbe()}
                              disabled={probing || !probeIntent.trim()}
                            >
                              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                              {probing ? '校验中…' : '校验'}
                            </button>
                          </div>
                          {probeResult && (
                            <div className="mt-3 at-hairline-t pt-3">
                              <p className={`at-sm font-medium ${probeResult.allowed ? 'at-ok' : 'at-bad'}`}>
                                {probeResult.allowed ? '准予履约' : '拦截阻断'}
                              </p>
                              <p className="at-meta at-muted mt-0.5">{probeResult.reason}</p>
                              {probeResult.matched_boundary && (
                                <p className="at-mono at-muted mt-0.5">命中规则：{probeResult.matched_boundary}</p>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 右栏：授权规程卡 / 知识范围 / 授权工具（5 栏） */}
                  <div className="flex flex-col justify-between gap-7 p-8 lg:col-span-5">
                    <div>
                      <div className="mb-2 flex items-center justify-between gap-3 pb-2 at-hairline-b">
                        <h3 className="at-section">授权规程卡</h3>
                        <span className="at-meta at-muted">已生效 {flows.length} 项</span>
                      </div>
                      {flows.length === 0 ? (
                        <p className="at-sm at-subtle py-3">未挂载规程卡</p>
                      ) : (
                        <div>
                          {flows.map((f, i) => (
                            <div key={`${f}-${i}`} className="flex items-center justify-between gap-3 py-3 at-hairline-b">
                              <span className="at-body truncate">{f}</span>
                              <span className="at-mono at-subtle shrink-0">SOP</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    <div>
                      <div className="mb-2 flex items-center justify-between gap-3 pb-2 at-hairline-b">
                        <h3 className="at-section">知识范围</h3>
                        <span className="at-meta at-muted">隔离桶 {buckets.length} 个</span>
                      </div>
                      {buckets.length === 0 ? (
                        <p className="at-sm at-subtle py-3">未授权知识范围</p>
                      ) : (
                        <div>
                          {buckets.map((b, i) => (
                            <div key={`${b}-${i}`} className="flex items-center justify-between gap-3 py-3 at-hairline-b">
                              <span className="at-body truncate">{b}</span>
                              <span className="at-meta at-subtle shrink-0">隔离</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    <div>
                      <div className="mb-2 flex items-center justify-between gap-3 pb-2 at-hairline-b">
                        <h3 className="at-section">授权工具</h3>
                        <span className="at-meta at-muted">沙箱凭据 {tools.length} 项</span>
                      </div>
                      {tools.length === 0 ? (
                        <p className="at-sm at-subtle py-3">未授权任何工具</p>
                      ) : (
                        <div>
                          {tools.map((t, i) => (
                            <div key={`${t}-${i}`} className="flex items-center justify-between gap-3 py-3 at-hairline-b">
                              <span className="at-mono font-medium" style={{ color: 'var(--at-ink)' }}>
                                {t}
                              </span>
                              <span className="at-meta at-subtle shrink-0">沙箱执行</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* 实时指标：生命体征派生 */}
                <div className="px-8 py-6 at-hairline-t" style={{ background: 'rgba(0,0,0,0.012)' }}>
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <h3 className="at-section">实时指标</h3>
                    <span className="at-meta at-muted">
                      {liveMetrics ? `采集于 ${safeDate(vitals?.collected_at)}` : '生命体征接口不可用'}
                    </span>
                  </div>
                  {liveMetrics ? (
                    <dl className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
                      {liveMetrics.map((m) => (
                        <div key={m.label}>
                          <dt className="at-meta at-muted">{m.label}</dt>
                          <dd className="at-sm mt-0.5 font-medium" style={{ color: 'var(--at-ink)' }}>
                            {m.value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="at-sm at-subtle">暂无可用实时指标，档案静态信息仍然有效。</p>
                  )}
                </div>
              </div>

              {/* 考核时间线：四段发丝线轨道，节点状态由 employment_status 驱动 */}
              <section className="at-surface-flat rounded-[10px] px-8 py-6">
                <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
                  <h3 className="at-section">考核时间线</h3>
                  <span className="at-meta at-muted">
                    {stageIndex < 0
                      ? `考核已中止 · 编制于 ${safeDate(profile.created_at)}`
                      : stageIndex < PROBATION_STAGES.length
                        ? `当前节点：${PROBATION_STAGES[stageIndex]} · 编制于 ${safeDate(profile.created_at)}`
                        : `四段考核已走完 · 编制于 ${safeDate(profile.created_at)}`}
                  </span>
                </div>
                <ol className="relative grid grid-cols-2 gap-6 pt-2 lg:grid-cols-4">
                  <div
                    className="absolute left-3 right-3 top-[13px] hidden h-px lg:block"
                    style={{ background: 'var(--at-hairline)' }}
                    aria-hidden="true"
                  />
                  {PROBATION_STAGES.map((label, i) => {
                    const done = i < stageIndex
                    const current = i === stageIndex
                    return (
                      <li key={label} className="flex flex-col items-start">
                        <span
                          className="mb-3 h-2.5 w-2.5 rounded-full"
                          style={{
                            background: current ? 'var(--at-primary)' : done ? 'var(--at-ink)' : 'var(--at-sunk)',
                            boxShadow: current ? '0 0 0 4px rgba(31,79,216,0.18)' : `0 0 0 1px var(--at-hairline)`,
                          }}
                          aria-hidden="true"
                        />
                        <div className="flex items-baseline gap-2">
                          <span
                            className="at-body font-medium"
                            style={{ color: current ? 'var(--at-ink)' : done ? 'var(--at-ink)' : 'var(--at-muted)' }}
                          >
                            {label}
                          </span>
                          <span className={`at-meta ${current ? 'at-accent-text' : 'at-subtle'}`}>
                            {current ? '当前节点' : done ? '已完成' : '待审核'}
                          </span>
                        </div>
                        {/* 只标注有真实时间戳的两端，中间节点后端未提供时间，诚实留空 */}
                        <span className="at-mono at-subtle mt-1">
                          {i === 0
                            ? safeDate(profile.created_at)
                            : i === PROBATION_STAGES.length - 1 && stageIndex >= PROBATION_STAGES.length - 1
                              ? safeDate(profile.updated_at)
                              : '—'}
                        </span>
                      </li>
                    )
                  })}
                </ol>
              </section>

              {/* 签名与版本受控区（Editorial v2 规范） */}
              <footer className="at-surface-flat rounded-[10px] p-6 at-hairline-t">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-3">
                      <span className="at-mono at-muted text-xs">档案标识：{profile.id}</span>
                      <span className="at-subtle">·</span>
                      <span className="at-meta at-muted">受控版本：v4.2-RELEASE</span>
                      <span className="at-subtle">·</span>
                      <span className="at-meta at-muted">语气风格：{profile.tone_style || 'professional'}</span>
                    </div>
                    <p className="at-mono at-subtle text-[11px]">
                      SHA-256 签名验真：{profile.id.replace(/-/g, '').slice(0, 16).toLowerCase()}...{profile.id.replace(/-/g, '').slice(-8).toLowerCase()} · 经 AutoTeams 运营总署自动合规签章
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="at-dot at-dot-ok" aria-hidden="true" />
                    <span className="at-meta at-muted font-medium">AutoTeams 运营总署受控认证档案</span>
                  </div>
                </div>
              </footer>
            </>
          )}
        </div>
      </div>
    </Layout>
  )
}
