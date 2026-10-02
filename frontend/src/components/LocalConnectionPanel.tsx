/**
 * LocalConnectionPanel — 本地连接卡片（协作工作台与本地工具桥接）
 *
 * 功能：
 * - 展示本地守护进程（Runner）连接状态与已授权路径列表。
 * - 注册本地路径授权：输入本地文件夹路径 + 授权范围，生成一次性 setup 命令。
 * - 撤销授权（含断线后的重新授权入口）。
 *
 * 一次性配对语义（backend/app/services/runner_session.py::claim_grant + mark_connected）：
 * 令牌在服务端「认领」claim 时即被消费作废，但授权状态要等到 mark_connected 才从
 * pending 变成 connected。服务端会一并返回 claimed（可选字段，旧版服务端不返回），
 * 所以 claimed=true 时即便状态仍是 pending 也必须立刻清除命令与令牌，并显示「连接中」。
 * claimed 缺失时 pending 仍只能算候选状态：服务端可能在下次刷新前改写（见 localConnectionState.ts）。
 * claimed / connected / offline / revoked / 过期是「凭据确已作废」的证据，此时把命令与
 * 令牌从状态里永久清除，而不是仅隐藏，避免陈旧列表数据把凭据又变回可见。
 *
 * 能力边界：Runner 仅执行授权目录内的 list/read/write/delete 文件操作，
 * 不提供终端命令或 Agentic CLI 的远程执行。
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import {
  Monitor, Copy, Check, Plus, Trash2, Terminal, FolderOpen,
  Loader2, Radio, ExternalLink, ShieldCheck, ShieldAlert, X,
} from 'lucide-react'
import {
  listLocalPaths, registerLocalPath, revokeLocalPath, expandLocalTools,
  type LocalPathGrant, type RegisterLocalPathResult,
} from '@/api/localPaths'
import { evaluateSetupCommand, parseSetupExpiry, type SetupCommandState } from './localConnectionState'

/** 轮询间隔：需要跟踪连接状态时定期刷新。 */
const POLL_INTERVAL_MS = 5000

/** setup 命令失效后的提示文案（凭据已从状态中永久清除时展示）。 */
const SETUP_COMMAND_GONE: Record<string, string> = {
  claimed: '本机已认领该授权，一次性命令已失效、不可重跑。连接通常会在稍后变为「已连接」；'
    + '若长时间没有变化，说明连接未建立成功，请撤销此授权后重新「添加」生成新命令。',
  revoked: '此授权已撤销，一次性命令已失效。',
  expired: '一次性配对命令已过期，请重新生成一条新命令。',
}


// ============================================================
// 类型
// ============================================================

/** 连接状态徽章：claimed 为真但仍是 pending 时是「连接中」，不是「待连接」。 */
function StatusBadge({ grant }: { grant: LocalPathGrant }) {
  const key = grant.status === 'pending' && grant.claimed === true ? 'connecting' : grant.status
  const map = {
    connecting: { label: '连接中', cls: 'bg-sky-50 text-sky-600 border-sky-200', icon: Loader2 },
    connected: { label: '已连接', cls: 'bg-emerald-50 text-emerald-600 border-emerald-200', icon: ShieldCheck },
    pending: { label: '待连接', cls: 'bg-amber-50 text-amber-600 border-amber-200', icon: Radio },
    offline: { label: '已离线', cls: 'bg-slate-100 text-slate-500 border-slate-200', icon: Monitor },
    revoked: { label: '已撤销', cls: 'bg-red-50 text-red-500 border-red-200', icon: ShieldAlert },
  }[key]
  const Icon = map.icon
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border ${map.cls}`}>
      <Icon className="w-3 h-3" aria-hidden="true" />
      {map.label}
    </span>
  )
}

/** 单项授权卡片 */
function GrantCard({
  grant,
  onRevoke,
  mutationPending,
}: {
  grant: LocalPathGrant
  onRevoke: (id: string) => void
  mutationPending: boolean
}) {
  return (
    <div className="border border-border-default rounded-md p-2.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-text-primary truncate flex-1">
          {grant.label || grant.local_path}
        </p>
        <StatusBadge grant={grant} />
      </div>
      <p className="text-xs text-text-tertiary truncate font-mono" title={grant.local_path}>
        {grant.local_path}
      </p>
      {/* §4.4 本地已就绪工具（徽章） */}
      {grant.status === 'connected' && grant.tool_manifest && (
        <div className="flex flex-wrap gap-1">
          {expandLocalTools(grant.tool_manifest).map((tool) => (
            <span
              key={`${tool.group}-${tool.name}`}
              className="inline-flex items-center gap-0.5 text-[11px] px-1.5 py-0.5 rounded-full border bg-slate-50 text-slate-600 border-slate-200"
              title="受限文件操作"
            >
              {tool.label}
            </span>
          ))}
        </div>
      )}
      {/* 断线恢复：令牌已被消费，无法用旧命令重连 */}
      {grant.status === 'offline' && (
        <p className="text-[11px] text-amber-600 leading-relaxed">
          连接已断开且一次性命令不可复用。撤销此授权后重新「添加」，生成新命令即可恢复。
        </p>
      )}
      {/* 已认领但尚未连上：连接正在建立；若卡住只能撤销后重新生成命令 */}
      {grant.status === 'pending' && grant.claimed === true && (
        <p className="text-[11px] text-sky-600 leading-relaxed">
          本机已认领此授权，正在建立连接，一次性命令已被消费、不可重跑。
          若长时间停留在「连接中」，请撤销此授权后重新「添加」生成新命令。
        </p>
      )}
      <div className="flex items-center justify-between">
        <span className="text-xs text-text-muted">
          {grant.scope === 'read_write' ? '读写文件' : '只读文件'}
        </span>
        <button
          onClick={() => onRevoke(grant.id)}
          disabled={mutationPending}
          className="inline-flex items-center gap-0.5 text-xs text-text-tertiary hover:text-red-500 transition-colors disabled:opacity-50"
        >
          <Trash2 className="w-3 h-3" aria-hidden="true" />
          撤销
        </button>
      </div>
    </div>
  )
}

// ============================================================
// 主组件
// ============================================================

export default function LocalConnectionPanel() {
  const [grants, setGrants] = useState<LocalPathGrant[]>([])
  const [loading, setLoading] = useState(true)
  const [path, setPath] = useState('')
  const [scope, setScope] = useState<'read' | 'read_write'>('read_write')
  const [label, setLabel] = useState('')
  const [registering, setRegistering] = useState(false)
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [notice, setNotice] = useState('')
  const [command, setCommand] = useState<RegisterLocalPathResult | null>(null)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')
  /** 凭据作废原因：命令被从状态里清除后，用它继续向用户解释。 */
  const [setupGone, setSetupGone] = useState<SetupCommandState | null>(null)
  const [showForm, setShowForm] = useState(false)
  // State disables controls after render; the ref also rejects a second action in the same tick.
  const mutationPendingRef = useRef(false)
  // 刷新采用「单飞 + 排队 + 代号」三重约束：
  // - inFlightRef：同一时刻只有一个 list 请求，避免竞态；
  // - queuedRef：刷新期间被再次请求（轮询/变更后）会排队补发一次，不会被静默跳过；
  // - requestGen/appliedGen：每次请求带代号，只有最新「适用」的结果才允许落地，
  //   变更成功时把 appliedGen 推到当前代号，使变更前发出的旧响应自动作废
  //   （否则旧列表会抹掉刚注册的授权，或把已撤销的行复活）。
  const inFlightRef = useRef(false)
  const queuedRef = useRef(false)
  const requestGenRef = useRef(0)
  const appliedGenRef = useRef(0)

  const refresh = useCallback(async () => {
    if (inFlightRef.current) {
      // 已有请求在飞：不丢弃这次诉求，排队补发一次。
      queuedRef.current = true
      return
    }
    inFlightRef.current = true
    const gen = ++requestGenRef.current
    try {
      const data = await listLocalPaths()
      if (gen > appliedGenRef.current) {
        appliedGenRef.current = gen
        setGrants(data)
        setListError('')
      }
    } catch (err) {
      if (gen > appliedGenRef.current) {
        appliedGenRef.current = gen
        // 保留上一次成功获取的授权列表：一次网络抖动不该抹掉用户可见的授权状态。
        // 但列表已陈旧，必须把失败本身显示出来，否则用户会把陈旧的「已连接」当现状。
        setListError(
          `连接状态刷新失败（${err instanceof Error ? err.message : '网络错误'}），`
          + '下列状态为上次成功获取的结果，可能已过期。',
        )
      }
    } finally {
      inFlightRef.current = false
      setLoading(false)
      if (queuedRef.current) {
        queuedRef.current = false
        void refresh()
      }
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 存在未撤销的授权（pending 或 connected）时持续轮询：
  // connected 的守护进程掉线后状态会转为 offline，只轮询 pending 会让掉线永远不可见；
  // 还持有待认领命令时也保持轮询，以便认领结果及时反映。
  const needsPolling = grants.some((g) => g.status !== 'revoked') || command !== null
  useEffect(() => {
    if (!needsPolling) return
    const timer = setInterval(() => {
      void refresh()
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [needsPolling, refresh])

  const commandGrant = useMemo(
    () => (command ? grants.find((g) => g.id === command.grant.id) : undefined),
    [command, grants],
  )
  const setupState = useMemo(
    () => (command ? evaluateSetupCommand(commandGrant, command.setup_token_expires_at) : null),
    [command, commandGrant],
  )

  /** 凭据作废：把命令与令牌从状态里永久清除（仅隐藏会被陈旧数据「复活」）。 */
  const discardCredential = useCallback((gone: SetupCommandState | null) => {
    setCommand(null)
    setCopied(false)
    setCopyError('')
    setSetupGone(gone)
  }, [])

  useEffect(() => {
    // unknown 只是「当前列表查不到」，不构成作废证据（刷新竞态也可能如此）。
    if (!setupState || setupState.kind === 'candidate' || setupState.kind === 'unknown') return
    discardCredential(setupState)
  }, [setupState, discardCredential])

  // 过期定时器：独立于轮询。轮询失败或标签页休眠都不会让过期命令继续可复制。
  const expiresAt = useMemo(
    () => (command ? parseSetupExpiry(command.setup_token_expires_at) : null),
    [command],
  )
  useEffect(() => {
    if (expiresAt === null) return
    const remaining = expiresAt - Date.now()
    if (remaining <= 0) return
    // setTimeout 上限约 24.8 天，10 分钟 TTL 不会触顶，仍做防御性截断。
    const timer = setTimeout(() => {
      discardCredential({ kind: 'expired' })
    }, Math.min(remaining, 2_147_483_647))
    return () => clearTimeout(timer)
  }, [expiresAt, discardCredential])

  const handleRegister = async () => {
    if (mutationPendingRef.current) return
    const trimmedPath = path.trim()
    if (!trimmedPath) {
      setError('请输入本地文件夹路径')
      return
    }
    mutationPendingRef.current = true
    setError('')
    setNotice('')
    // 注册一开始就把上一次的凭据清掉：它可能已过期或已被认领，不能继续留着。
    discardCredential(null)
    setRegistering(true)
    try {
      const result = await registerLocalPath({
        local_path: trimmedPath,
        scope,
        label: label.trim() || undefined,
      })
      // 变更成功：作废变更前发出的列表响应，并立刻把新授权落到本地状态，
      // 避免在途旧列表（不含该授权）覆盖掉刚创建的一次性命令。
      appliedGenRef.current = requestGenRef.current
      setGrants((prev) => [result.grant, ...prev.filter((g) => g.id !== result.grant.id)])
      setSetupGone(null)
      setCommand(result)
      setNotice('授权已创建。请在本机 PowerShell 运行下方一次性连接命令（命令只能使用一次）。')
      setPath('')
      setLabel('')
      setShowForm(false)
      void refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '注册失败，请重试')
    } finally {
      mutationPendingRef.current = false
      setRegistering(false)
    }
  }

  const handleRevoke = async (id: string) => {
    if (mutationPendingRef.current) return
    mutationPendingRef.current = true
    setRevokingId(id)
    setError('')
    try {
      await revokeLocalPath(id)
      // 同上：变更成功后立刻本地移除，并作废在途旧列表，防止已撤销的行被复活。
      appliedGenRef.current = requestGenRef.current
      setGrants((prev) => prev.filter((g) => g.id !== id))
      if (command?.grant.id === id) discardCredential({ kind: 'revoked' })
      void refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '撤销失败')
    } finally {
      mutationPendingRef.current = false
      setRevokingId(null)
    }
  }

  const handleCopy = async () => {
    if (!command) return
    // 复制瞬间的兜底判定：标签页休眠 / 轮询失败时，定时器可能还没跑，
    // 但 Date.now() 已是真实时间，过期令牌绝不能被复制出去。
    const expiry = parseSetupExpiry(command.setup_token_expires_at)
    if (expiry === null || expiry <= Date.now()) {
      discardCredential({ kind: 'expired' })
      return
    }
    try {
      await navigator.clipboard.writeText(command.setup_command)
      setCopyError('')
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // 剪贴板失败必须显式告知，否则用户会以为命令已复制而在终端里粘贴出空内容。
      setCopyError('复制失败，请手动选中上方命令文本复制。')
      setCopied(false)
    }
  }


  const connectedCount = grants.filter((g) => g.status === 'connected').length

  return (
    <div className="border-t border-border-subtle bg-surface">
      {/* 标题：桌面端右侧栏内置顶常驻，确保连接状态始终可见（移动端抽屉已有头部，无需置顶） */}
      <div className="lg:sticky lg:top-0 z-10 bg-surface px-3 py-3 border-b border-border-subtle flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Monitor className="w-4 h-4 text-brand-500" aria-hidden="true" />
            <span className="text-sm font-semibold text-text-primary">本地连接</span>
          </div>
          <div className="flex items-center gap-2">
            {connectedCount > 0 ? (
              <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
                {connectedCount} 台在线
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs text-text-muted">
                <span className="w-1.5 h-1.5 rounded-full bg-slate-300" aria-hidden="true" />
                未连接
              </span>
            )}
            <button
              onClick={() => setShowForm((v) => !v)}
              disabled={registering || revokingId !== null}
              className="inline-flex items-center gap-0.5 text-xs text-brand-600 hover:text-brand-700 transition-colors"
              aria-expanded={showForm}
              aria-label={showForm ? '取消添加本地连接' : '添加本地连接'}
            >
              {showForm ? (
                <X className="w-3.5 h-3.5" aria-hidden="true" />
              ) : (
                <Plus className="w-3.5 h-3.5" aria-hidden="true" />
              )}
              {showForm ? '取消' : '添加'}
            </button>
          </div>
        </div>

        <div className="p-3">
        {/* 简短说明 */}
        <p className="text-xs text-text-muted mb-2">
          授权本机文件夹，让 AI 员工在本地直接读写文件；不提供终端命令执行。
        </p>

        {/* 错误 / 提示 */}
        {error && (
          <p className="text-xs text-red-500 bg-red-50 border border-red-100 rounded-md px-2 py-1.5 mb-2">
            {error}
          </p>
        )}
        {listError && (
          <p className="text-xs text-red-500 bg-red-50 border border-red-100 rounded-md px-2 py-1.5 mb-2">
            {listError}
          </p>
        )}
        {notice && (
          <p className="text-xs text-emerald-600 bg-emerald-50 border border-emerald-100 rounded-md px-2 py-1.5 mb-2">
            {notice}
          </p>
        )}
        {setupGone && SETUP_COMMAND_GONE[setupGone.kind] && (
          <p className="text-xs text-amber-600 bg-amber-50 border border-amber-100 rounded-md px-2 py-1.5 mb-2" role="status">
            {SETUP_COMMAND_GONE[setupGone.kind]}
          </p>
        )}

        {/* 注册表单 */}
        {showForm && (
          <div className="border border-brand-200 bg-brand-50/40 rounded-md p-2.5 space-y-2 mb-2">
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">
                本地文件夹路径
              </label>
              <div className="flex items-center gap-1.5 bg-elevated border border-border-default rounded-md px-2 py-1.5 focus-within:border-brand-500 focus-within:ring-1 focus-within:ring-brand-500/15">
                <FolderOpen className="w-3.5 h-3.5 text-text-muted" aria-hidden="true" />
                <input
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder="如 D:\项目\销售资料"
                  className="flex-1 bg-transparent text-xs text-text-primary placeholder:text-text-muted focus:outline-none"
                />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">
                授权范围
              </label>
              <div className="flex gap-1.5">
                {([
                  { value: 'read_write', label: '读写文件' },
                  { value: 'read', label: '仅只读' },
                ] as const).map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setScope(opt.value)}
                    className={`flex-1 text-xs rounded-md py-1.5 border transition-colors ${
                      scope === opt.value
                        ? 'bg-brand-500 text-white border-brand-500'
                        : 'bg-elevated text-text-secondary border-border-default hover:border-brand-300'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">
                名称（可选）
              </label>
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="如 销售资料库"
                className="w-full bg-elevated border border-border-default rounded-md px-2 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500/15"
              />
            </div>
            <button
              onClick={handleRegister}
              disabled={registering || revokingId !== null}
              className="w-full inline-flex items-center justify-center gap-1.5 bg-brand-500 text-white rounded-md py-2 text-xs font-medium hover:bg-brand-600 transition-colors disabled:opacity-50"
            >
              {registering ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
              ) : (

                <Terminal className="w-3.5 h-3.5" aria-hidden="true" />
              )}
              {registering ? '生成中…' : '生成连接命令'}
            </button>
          </div>
        )}

        {/* 连接命令：仅在令牌仍处于候选可用状态时展示，避免复制已作废的凭据 */}
        {command && setupState?.kind === 'candidate' && (
          <div className="border border-border-default rounded-md p-2.5 mb-2 bg-slate-50">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs font-medium text-text-secondary">在本机运行以下命令</span>
              <button
                onClick={handleCopy}
                className="inline-flex items-center gap-1 text-xs text-brand-600 hover:text-brand-700"
              >
                {copied ? (
                  <Check className="w-3 h-3 text-emerald-500" aria-hidden="true" />
                ) : (
                  <Copy className="w-3 h-3" aria-hidden="true" />
                )}
                {copied ? '已复制' : '复制'}
              </button>
            </div>
            <code className="block text-[11px] leading-relaxed text-text-primary font-mono break-all bg-white border border-border-subtle rounded px-2 py-1.5 select-all">
              {command.setup_command}
            </code>
            <p className="text-[11px] text-text-muted mt-1.5">
              在本机 PowerShell 中粘贴运行。令牌由服务端在「认领」时消费作废，认领前若传输失败可直接重试同一条命令；
              一旦被认领，命令就无法重跑，连接断开也需要重新授权。
            </p>
            <p className="text-[11px] text-text-muted mt-1">
              提示：服务端确认「已认领」后，该授权会变为「连接中」，这条命令也会立即被收回；
              在此之前，「待连接」仍无法区分「还没运行过」与「刚被认领、连接尚未建立」。
              若命令已运行却长时间停留在「待连接」，请撤销该授权后重新「添加」生成新命令。
            </p>
            {copyError && (
              <p className="text-[11px] text-red-500 mt-1" role="alert">
                {copyError}
              </p>
            )}
          </div>
        )}

        {/* 授权列表 */}
        {loading ? (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="w-4 h-4 animate-spin text-text-muted" aria-hidden="true" />
          </div>
        ) : grants.length === 0 ? (
          <div className="text-center py-4">
            <Monitor className="w-6 h-6 text-text-muted mx-auto mb-1.5" aria-hidden="true" />
            <p className="text-xs text-text-tertiary">还没有本地授权</p>
            <p className="text-xs text-text-muted mt-0.5">点击上方「添加」开始</p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {grants.map((grant) => (
              <GrantCard
                key={grant.id}
                grant={grant}
                onRevoke={handleRevoke}
                mutationPending={registering || revokingId !== null}
              />
            ))}
          </div>
        )}

        {/* 使用引导 */}
        <a
          href="#local-connection-guide"
          className="flex items-center gap-1 text-xs text-brand-600 hover:text-brand-700 mt-2"
        >
          <ExternalLink className="w-3 h-3" aria-hidden="true" />
          查看使用指南
        </a>
      </div>
    </div>
  )
}
