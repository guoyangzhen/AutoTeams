/**
 * ExternalAgentsPage —「Agent 接入」核心工作空间（/agents）。
 *
 * 面向中小企业老板 / 经理 / 技术负责人：把本地的 Codex、Claude Code、任意 MCP
 * Agent 与 AutoTeams 端侧执行器收编进同一个工作台，统一查看运行时连接状态、
 * 分派任务、处置高危操作门禁。
 *
 * 数据全部来自真实后端，无任何兜底假数据：
 * - GET  /external-agents · POST /{id}/{connect,disconnect,dispatch}  外部本地 Agent
 * - GET  /mcp/servers · /mcp/tools · POST /mcp/call                     MCP 运行时
 * - GET  /runner/v2/runners · /tasks · /challenges                       端侧执行器与任务
 * - POST /runner/v2/tasks · POST /challenges/{id}/confirm                 物理任务分派与二次核准
 * - GET  /local-paths                                                    本地目录授权
 * - GET  /connectors/accounts                                            客户消息触发源
 */
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Bot,
  Cable,
  ClipboardCopy,
  Gauge,
  MessageSquare,
  Play,
  Plug,
  Power,
  RefreshCw,
  Send,
  ShieldAlert,
  Terminal,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { PageHeader } from '@/components/ui/PageHeader'
import { SectionPanel } from '@/components/ui/SectionPanel'
import { EmptyState } from '@/components/ui/EmptyState'
import { MetricGrid, type Metric } from '@/components/ui/MetricGrid'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Spinner } from '@/components/ui/Spinner'
import {
  listExternalAgents,
  connectExternalAgent,
  disconnectExternalAgent,
  dispatchTaskToAgent,
  type ExternalAgent,
  type ExternalAgentStatus,
  type ExternalTaskReceipt,
} from '@/api/externalAgents'
import { listMCPServers, listMCPTools, callMCPTool, type MCPServer, type MCPTool } from '@/api/mcp'
import {
  listPhysicalRunners,
  listPhysicalTasks,
  listTwoFactorChallenges,
  dispatchPhysicalTask,
  confirmTwoFactor,
  type PhysicalChannel,
  type PhysicalStep,
} from '@/api/runnerV2'
import { listLocalPaths, revokeLocalPath } from '@/api/localPaths'
import { listChannelAccounts } from '@/api/connectors'

// ============================================================
// 接入模板：Codex / Claude Code / 自定义 MCP Agent
// 模板是接入说明，登记状态由后端 MCP 服务器列表实时推导。
// ============================================================

interface ConnectionTemplate {
  key: string
  name: string
  vendor: string
  icon: LucideIcon
  transport: string
  /** 与后端 MCP 服务器 command / url 匹配的特征串 */
  matchers: string[]
  configHint: Array<[string, string]>
  note: string
}

const CONNECTION_TEMPLATES: ConnectionTemplate[] = [
  {
    key: 'codex',
    name: 'OpenAI Codex',
    vendor: '本地 CLI',
    icon: Terminal,
    transport: 'MCP · stdio',
    matchers: ['codex'],
    configHint: [
      ['command', 'codex'],
      ['transport', 'stdio'],
      ['args', 'mcp'],
    ],
    note: '把 Codex 暴露的工具映射为数字员工可用工具，代码类任务直接交给它执行。',
  },
  {
    key: 'claude-code',
    name: 'Claude Code',
    vendor: 'Anthropic',
    icon: Terminal,
    transport: 'MCP · stdio',
    matchers: ['claude'],
    configHint: [
      ['command', 'claude'],
      ['transport', 'stdio'],
      ['args', 'mcp'],
    ],
    note: '技术方案与代码分析交给 Claude Code，由销售 / 客服数字员工整合成客户方案。',
  },
  {
    key: 'custom-mcp',
    name: '自研 / 任意 MCP Agent',
    vendor: 'HTTP · SSE',
    icon: Cable,
    transport: 'MCP · sse',
    matchers: ['http://', 'https://', 'ws://', 'sse'],
    configHint: [
      ['transport', 'sse'],
      ['url', 'https://your-agent/sse'],
    ],
    note: '任何兼容 MCP 协议的服务都能接入，工具契约自动同步到工具目录。',
  },
]

/** 依据后端 MCP 服务器列表推导某模板是否已登记接入。 */
function isTemplateRegistered(template: ConnectionTemplate, servers: MCPServer[]): boolean {
  return servers.some((s) => {
    const haystack = [s.command ?? '', s.url ?? '', ...(s.args ?? [])].join(' ').toLowerCase()
    return template.matchers.some((m) => haystack.includes(m))
  })
}

// ============================================================
// 通用小组件
// ============================================================

function StatusDot({ online }: { online: boolean }) {
  return (
    <span
      className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${online ? 'bg-[#1F7A4D]' : 'bg-[#B23A2F]'}`}
      aria-hidden="true"
    />
  )
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        } catch {
          toast.error('复制失败，请手动选中配置内容')
        }
      }}
      className="inline-flex items-center gap-1 text-[11px] text-text-tertiary hover:text-text-primary transition-colors"
    >
      <ClipboardCopy className="w-3 h-3" aria-hidden="true" />
      {copied ? '已复制' : label}
    </button>
  )
}

/**
 * 从 axios 错误中取出后端 `detail` 文案。
 * FastAPI 的错误体为 `{ detail: ... }`，此处只接受字符串形态；
 * 非字符串时返回 undefined，交由调用方给出兜底提示。
 */
function errorDetail(err: unknown): string | undefined {
  if (typeof err !== 'object' || err === null || !('response' in err)) return undefined
  const response = (err as { response?: { data?: { detail?: unknown } } }).response
  return typeof response?.data?.detail === 'string' ? response.data.detail : undefined
}


// ============================================================
// 页面主体
// ============================================================

export default function ExternalAgentsPage() {
  const queryClient = useQueryClient()

  const serversQuery = useQuery({ queryKey: ['mcp', 'servers'], queryFn: listMCPServers })
  const toolsQuery = useQuery({ queryKey: ['mcp', 'tools'], queryFn: listMCPTools })
  const runnersQuery = useQuery({
    queryKey: ['runner-v2', 'runners'],
    queryFn: listPhysicalRunners,
    refetchInterval: 15_000,
  })
  const tasksQuery = useQuery({
    queryKey: ['runner-v2', 'tasks'],
    queryFn: listPhysicalTasks,
    refetchInterval: 15_000,
  })
  const challengesQuery = useQuery({
    queryKey: ['runner-v2', 'challenges'],
    queryFn: listTwoFactorChallenges,
    refetchInterval: 15_000,
  })
  const grantsQuery = useQuery({ queryKey: ['local-paths'], queryFn: listLocalPaths })
  const externalAgentsQuery = useQuery({
    queryKey: ['external-agents'],
    queryFn: listExternalAgents,
    refetchInterval: 20_000,
  })

  const channelsQuery = useQuery({
    queryKey: ['connectors', 'accounts'],
    queryFn: listChannelAccounts,
  })

  const servers = useMemo(() => serversQuery.data ?? [], [serversQuery.data])
  const tools = useMemo(() => toolsQuery.data ?? [], [toolsQuery.data])
  const runners = useMemo(() => runnersQuery.data ?? [], [runnersQuery.data])
  const tasks = useMemo(() => tasksQuery.data ?? [], [tasksQuery.data])
  const challenges = useMemo(() => challengesQuery.data ?? [], [challengesQuery.data])
  const grants = useMemo(() => grantsQuery.data ?? [], [grantsQuery.data])
  const channelAccounts = useMemo(() => channelsQuery.data ?? [], [channelsQuery.data])
  const externalAgents = useMemo(
    () => externalAgentsQuery.data ?? [],
    [externalAgentsQuery.data],
  )
  const connectedExternalAgents = externalAgents.filter(
    (a) => a.status === 'connected' || a.status === 'busy',
  )


  const onlineRunners = runners.filter((r) => r.online)
  const pendingChallenges = challenges.filter(
    (c) => c.state === 'pending_endpoint_confirmation' || c.state === 'pending_device_code',
  )
  const runningTasks = tasks.filter(
    (t) => t.state === 'dispatched' || t.state === 'executing' || t.state === 'awaiting_2fa',
  )

  const metrics: Metric[] = [
    {
      key: 'external',
      icon: Cable,
      value: `${connectedExternalAgents.length}/${externalAgents.length}`,
      label: '外部 Agent 已连接',
      tone: connectedExternalAgents.length > 0 ? 'success' : 'warning',
    },
    { key: 'runtime', icon: Plug, value: servers.length, label: 'MCP 运行时连接', tone: 'brand' },
    {
      key: 'runners',
      icon: Bot,
      value: `${onlineRunners.length}/${runners.length}`,
      label: '端侧执行器在线',
      tone: onlineRunners.length > 0 ? 'success' : 'warning',
    },
    { key: 'tools', icon: Wrench, value: tools.length, label: '可用外部工具', tone: 'info' },
    {
      key: 'pending',
      icon: ShieldAlert,
      value: pendingChallenges.length,
      label: '待二次核准高危操作',
      tone: pendingChallenges.length > 0 ? 'error' : 'success',
    },
    {
      key: 'channels',
      icon: MessageSquare,
      value: channelAccounts.length,
      label: '客户消息接入渠道',
      tone: channelAccounts.length > 0 ? 'success' : 'warning',
    },
    { key: 'running', icon: Gauge, value: runningTasks.length, label: '进行中外部任务', tone: 'info' },
  ]

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['mcp'] })
    void queryClient.invalidateQueries({ queryKey: ['runner-v2'] })
    void queryClient.invalidateQueries({ queryKey: ['local-paths'] })
    void queryClient.invalidateQueries({ queryKey: ['external-agents'] })
  }

  return (
    <Layout>
      <div className="mx-auto w-full max-w-[1280px] px-10 py-10">
        <PageHeader
          title="Agent 接入"
          subtitle="把本地的 Codex、Claude Code、任意 MCP Agent 与端侧执行器收编进同一个工作台"
          actions={
            <Button variant="secondary" size="sm" onClick={refreshAll}>
              <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
              刷新状态
            </Button>
          }
        />

        <MetricGrid metrics={metrics} columns={3} className="mb-8" />
        <ExternalAgentSection
          agents={externalAgents}
          pending={externalAgentsQuery.isPending}
          error={externalAgentsQuery.isError}
          onRefresh={refreshAll}
        />


        <div className="grid grid-cols-12 gap-8">
          <div className="col-span-12 lg:col-span-7 space-y-8">
            <SectionPanel
              title="本地 Agent 接入模板"
              description="MCP 协议优先：工具契约自动映射为数字员工可用工具"
              icon={<Plug className="w-4 h-4" />}
              bodyClassName="px-5 py-4 space-y-3"
            >
              {CONNECTION_TEMPLATES.map((tpl) => {
                const registered = isTemplateRegistered(tpl, servers)
                return (
                  <div key={tpl.key} className="border border-border-default rounded-[6px] p-4 bg-surface">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-start gap-3 min-w-0">
                        <tpl.icon
                          className="w-4 h-4 mt-0.5 text-brand-500 flex-shrink-0"
                          aria-hidden="true"
                        />
                        <div className="min-w-0">
                          <p className="text-[13px] font-medium text-text-primary">
                            {tpl.name}
                            <span className="ml-2 text-[11px] font-normal text-text-tertiary">
                              {tpl.vendor} · {tpl.transport}
                            </span>
                          </p>
                          <p className="text-[12px] text-text-tertiary mt-1 leading-relaxed">
                            {tpl.note}
                          </p>
                        </div>
                      </div>
                      <span
                        className={`inline-flex items-center gap-1.5 text-[11px] flex-shrink-0 px-2 py-1 rounded-[4px] ${
                          registered ? 'bg-success/10 text-success' : 'bg-elevated text-text-tertiary'
                        }`}
                      >
                        <StatusDot online={registered} />
                        {registered ? '已登记接入' : '未接入'}
                      </span>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {tpl.configHint.map(([k, v]) => (
                        <code
                          key={k}
                          className="text-[11px] font-mono bg-elevated text-text-secondary px-2 py-1 rounded-[4px]"
                        >
                          {k}={v}
                        </code>
                      ))}
                      <CopyButton
                        text={tpl.configHint.map(([k, v]) => `${k}=${v}`).join('\n')}
                        label="复制接入配置"
                      />
                    </div>
                  </div>
                )
              })}
              <p className="text-[11px] text-text-tertiary leading-relaxed">
                接入状态由服务端 MCP 服务器列表实时推导；新增 MCP 服务器需在服务端
                AGOTEAMS_MCP_SERVERS 配置中登记后刷新本页。
              </p>
            </SectionPanel>

            <SectionPanel
              title="运行时连接"
              description="MCP 服务器 · 端侧执行器 · 本地目录授权"
              icon={<Cable className="w-4 h-4" />}
              bodyClassName="px-5 py-4 space-y-5"
            >
              <div>
                <h4 className="text-[12px] font-semibold text-text-secondary mb-2">MCP 服务器</h4>
                {serversQuery.isPending ? (
                  <p className="text-[12px] text-text-tertiary">加载中…</p>
                ) : serversQuery.isError ? (
                  <p className="text-[12px] text-error">MCP 服务器列表加载失败，请稍后重试</p>
                ) : servers.length === 0 ? (
                  <p className="text-[12px] text-text-tertiary">
                    尚未登记任何 MCP 服务器，数字员工暂无外部工具可调用。
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {servers.map((s) => (
                      <li
                        key={s.server_id}
                        className="flex items-center justify-between border border-border-default rounded-[6px] px-3 py-2.5"
                      >
                        <div className="min-w-0">
                          <p className="text-[13px] text-text-primary font-medium truncate">{s.name}</p>
                          <p className="text-[11px] font-mono text-text-tertiary truncate">
                            {s.transport}
                            {s.command ? ` · ${s.command}` : ''}
                            {s.url ? ` · ${s.url}` : ''}
                          </p>
                        </div>
                        <span className="flex items-center gap-1.5 text-[11px] text-text-tertiary flex-shrink-0 ml-3">
                          <StatusDot online={s.is_active} />
                          {s.is_active ? '运行中' : '已停用'}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h4 className="text-[12px] font-semibold text-text-secondary mb-2">端侧执行器</h4>
                {runnersQuery.isPending ? (
                  <p className="text-[12px] text-text-tertiary">加载中…</p>
                ) : runnersQuery.isError ? (
                  <p className="text-[12px] text-error">端侧执行器列表加载失败，请稍后重试</p>
                ) : runners.length === 0 ? (
                  <p className="text-[12px] text-text-tertiary">
                    暂无端侧执行器接入，浏览器 / 桌面类任务暂时无法下发。
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {runners.map((r) => (
                      <li
                        key={r.runner_id}
                        className="flex items-center justify-between border border-border-default rounded-[6px] px-3 py-2.5"
                      >
                        <div className="min-w-0">
                          <p className="text-[13px] text-text-primary font-medium truncate">
                            {r.runner_id}
                          </p>
                          <p className="text-[11px] font-mono text-text-tertiary truncate">
                            {r.platform} · v{r.version} · 最近心跳 {r.last_seen}
                          </p>
                        </div>
                        <span className="flex items-center gap-1.5 text-[11px] text-text-tertiary flex-shrink-0 ml-3">
                          <StatusDot online={r.online} />
                          {r.online ? '在线' : '离线'}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h4 className="text-[12px] font-semibold text-text-secondary mb-2">
                  本地目录授权
                </h4>
                {grantsQuery.isPending ? (
                  <p className="text-[12px] text-text-tertiary">加载中…</p>
                ) : grantsQuery.isError ? (
                  <p className="text-[12px] text-error">本地目录授权加载失败，请稍后重试</p>
                ) : grants.length === 0 ? (
                  <p className="text-[12px] text-text-tertiary">
                    尚未授权任何本地目录，外部 Agent 无法读取本机文件。
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {grants.map((g) => (
                      <li
                        key={g.id}
                        className="flex items-center justify-between gap-3 border border-border-default rounded-[6px] px-3 py-2.5"
                      >
                        <div className="min-w-0">
                          <p className="text-[13px] text-text-primary font-medium truncate">
                            {g.label || g.local_path}
                          </p>
                          <p className="text-[11px] font-mono text-text-tertiary truncate">
                            {g.local_path} · {g.scope}
                          </p>
                        </div>
                        <span className="flex items-center gap-2 flex-shrink-0">
                          <span className="flex items-center gap-1.5 text-[11px] text-text-tertiary">
                            <StatusDot online={g.status === 'connected'} />
                            {g.status}
                          </span>
                          <RevokeGrantButton grantId={g.id} />
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </SectionPanel>
          </div>

          <div className="col-span-12 lg:col-span-5 space-y-8">
            <ExternalTaskDelegation agents={externalAgents} />
            <DispatchCard runners={runners} runningCount={runningTasks.length} onDispatched={refreshAll} />

            <SectionPanel
              title="外部工具目录"
              description="MCP 工具契约，可直接试调用"
              icon={<Wrench className="w-4 h-4" />}
              bodyClassName="px-5 py-4"
            >
              <ToolConsole servers={servers} tools={tools} />
            </SectionPanel>
          </div>
        </div>

        <section className="mt-8">
          <SectionPanel
            title="高危操作二次核准"
            description="涉及本机数据写入、删除等高危指令时，端侧执行器会挂起等待人工核准"
            icon={<ShieldAlert className="w-4 h-4" />}
            bodyClassName="px-5 py-4"
          >
            {challengesQuery.isPending ? (
              <p className="text-[12px] text-text-tertiary">加载中…</p>
            ) : challengesQuery.isError ? (
              <p className="text-[12px] text-error">高危操作记录加载失败，请稍后重试</p>
            ) : challenges.length === 0 ? (
              <p className="text-[12px] text-text-tertiary">
                暂无高危操作记录，所有下发任务均已通过护栏裁决。
              </p>
            ) : (
              <ul className="space-y-2">
                {challenges.map((c) => (
                  <li
                    key={c.challenge_id}
                    className="flex items-center justify-between gap-3 border border-border-default rounded-[6px] px-3 py-2.5"
                  >
                    <div className="min-w-0">
                      <p className="text-[13px] text-text-primary truncate">{c.reason}</p>
                      <p className="text-[11px] font-mono text-text-tertiary truncate">
                        任务 {c.task_id} · 状态 {c.state} · 到期{' '}
                        {new Date(c.expires_at).toLocaleString()}
                      </p>
                    </div>
                    <ChallengeActions
                      challengeId={c.challenge_id}
                      decidable={
                        c.state === 'pending_endpoint_confirmation' ||
                        c.state === 'pending_device_code'
                      }
                      onResolved={refreshAll}
                    />
                  </li>
                ))}
              </ul>
            )}
          </SectionPanel>
        </section>
      </div>
    </Layout>
  )
}

// ============================================================
// 子组件
// ============================================================

function RevokeGrantButton({ grantId }: { grantId: string }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () => revokeLocalPath(grantId),
    onSuccess: () => {
      toast.success('已撤销本地目录授权')
      void queryClient.invalidateQueries({ queryKey: ['local-paths'] })
    },
    onError: () => toast.error('撤销失败，请确认当前账号是否拥有该授权'),
  })

  return (
    <button
      type="button"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      className="text-[11px] text-text-tertiary hover:text-error transition-colors disabled:opacity-50"
    >
      撤销
    </button>
  )
}

function ChallengeActions({
  challengeId,
  decidable,
  onResolved,
}: {
  challengeId: string
  decidable: boolean
  onResolved: () => void
}) {
  const [pending, setPending] = useState(false)

  const decide = async (decision: 'approve' | 'reject') => {
    setPending(true)
    try {
      await confirmTwoFactor(challengeId, decision)
      toast.success(decision === 'approve' ? '已核准放行' : '已阻断该高危操作')
      onResolved()
    } catch {
      toast.error('操作失败，请稍后重试')
    } finally {
      setPending(false)
    }
  }

  if (!decidable) {
    return <span className="text-[11px] text-text-tertiary flex-shrink-0">无需处理</span>
  }

  return (
    <div className="flex items-center gap-2 flex-shrink-0">
      <Button variant="secondary" size="sm" disabled={pending} onClick={() => void decide('reject')}>
        阻断
      </Button>
      <Button size="sm" disabled={pending} onClick={() => void decide('approve')}>
        核准放行
      </Button>
    </div>
  )
}

// ============================================================
// 外部本地 Agent（Codex / Claude Code / MCP）—— 真实 /external-agents 端点
// ============================================================

const EXTERNAL_STATUS_META: Record<
  ExternalAgentStatus,
  { label: string; online: boolean; className: string }
> = {
  connected: { label: '已连接', online: true, className: 'bg-success/10 text-success' },
  busy: { label: '执行中', online: true, className: 'bg-info/10 text-info' },
  detected: { label: '已发现待连接', online: false, className: 'bg-elevated text-text-tertiary' },
  offline: { label: '已断开', online: false, className: 'bg-elevated text-text-tertiary' },
}

function ExternalAgentSection({
  agents,
  pending,
  error,
  onRefresh,
}: {
  agents: ExternalAgent[]
  pending: boolean
  error: boolean
  onRefresh: () => void
}) {
  const connectable = agents.filter(
    (a) => a.status === 'offline' || a.status === 'detected',
  )

  return (
    <section className="mb-8">
      <SectionPanel
        title="外部 Agent 清单"
        description="服务端 Local Agent Bridge 侦测到的本地 Agent，可连接、断开并分派子任务"
        icon={<Cable className="w-4 h-4" />}
        action={
          <Button variant="secondary" size="sm" onClick={onRefresh} disabled={pending}>
            <RefreshCw
              className={`w-3.5 h-3.5 ${pending ? 'animate-spin' : ''}`}
              aria-hidden="true"
            />
            刷新
          </Button>
        }
        bodyClassName="px-5 py-4"
      >
        {pending ? (
          <div className="flex items-center justify-center gap-2 py-10" role="status">
            <Spinner size="sm" />
            <span className="text-[13px] text-text-tertiary">正在读取外部 Agent 清单…</span>
          </div>
        ) : error ? (
          <EmptyState
            icon={Plug}
            title="外部 Agent 清单加载失败"
            description="请确认服务端已合入 /api/v1/external-agents 路由且当前账号具备访问权限，然后重试。"
            action={{ label: '重新加载', onClick: onRefresh }}
          />
        ) : agents.length === 0 ? (
          <EmptyState
            icon={Plug}
            title="本机还没有发现外部 Agent"
            description="请先在员工电脑上安装并启动 Codex CLI 或 Claude Code CLI，再回到本页点击刷新；AutoTeams 会自动侦测并列入清单。"
            action={{ label: '重新侦测', onClick: onRefresh }}
          />
        ) : (
          <>
            <ul className="grid grid-cols-1 lg:grid-cols-3 gap-3">
              {agents.map((agent) => (
                <ExternalAgentCard key={agent.id} agent={agent} />
              ))}
            </ul>
            <p className="text-[11px] text-text-tertiary mt-3">
              共 {agents.length} 个外部 Agent，其中 {agents.length - connectable.length} 个处于可用状态。
            </p>
          </>
        )}
      </SectionPanel>
    </section>
  )
}

function ExternalAgentCard({ agent }: { agent: ExternalAgent }) {
  const queryClient = useQueryClient()
  const meta = EXTERNAL_STATUS_META[agent.status] ?? EXTERNAL_STATUS_META.offline

  const connect = useMutation({
    mutationFn: () => connectExternalAgent(agent.id),
    onSuccess: (res) => {
      toast.success(res.message || `已连接 ${agent.name}`)
      void queryClient.invalidateQueries({ queryKey: ['external-agents'] })
    },
    onError: (err: unknown) => {
      toast.error(errorDetail(err) ?? `连接 ${agent.name} 失败`)
    },
  })

  const disconnect = useMutation({
    mutationFn: () => disconnectExternalAgent(agent.id),
    onSuccess: (res) => {
      toast.success(res.message || `已断开 ${agent.name}`)
      void queryClient.invalidateQueries({ queryKey: ['external-agents'] })
    },
    onError: (err: unknown) => {
      toast.error(errorDetail(err) ?? `断开 ${agent.name} 失败`)
    },
  })

  const connected = agent.status === 'connected' || agent.status === 'busy'
  const busy = connect.isPending || disconnect.isPending

  return (
    <li className="border border-border-default rounded-[6px] p-4 bg-surface flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-text-primary truncate">{agent.name}</p>
          <p className="text-[11px] font-mono text-text-tertiary truncate">
            {agent.type}
            {agent.command_path ? ` · ${agent.command_path}` : ''}
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 text-[11px] flex-shrink-0 px-2 py-1 rounded-[4px] ${meta.className}`}
        >
          <StatusDot online={meta.online} />
          {meta.label}
        </span>
      </div>
      <p className="text-[12px] text-text-tertiary mt-2 leading-relaxed flex-1">{agent.description}</p>
      {agent.capabilities.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {agent.capabilities.map((cap) => (
            <span
              key={cap}
              className="text-[10px] font-mono text-text-tertiary bg-elevated px-1.5 py-0.5 rounded-[2px]"
            >
              {cap}
            </span>
          ))}
        </div>
      )}
      <div className="mt-3 flex items-center gap-2">
        {connected ? (
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={() => disconnect.mutate()}
          >
            {disconnect.isPending ? (
              <Spinner size="sm" />
            ) : (
              <Power className="w-3.5 h-3.5" aria-hidden="true" />
            )}
            {disconnect.isPending ? '断开中…' : '断开连接'}
          </Button>
        ) : (
          <Button size="sm" disabled={busy} onClick={() => connect.mutate()}>
            {connect.isPending ? (
              <Spinner size="sm" />
            ) : (
              <Plug className="w-3.5 h-3.5" aria-hidden="true" />
            )}
            {connect.isPending ? '连接中…' : '连接 Agent'}
          </Button>
        )}
      </div>
    </li>
  )
}

function ExternalTaskDelegation({ agents }: { agents: ExternalAgent[] }) {
  const connectable = agents.filter(
    (a) => a.status === 'offline' || a.status === 'detected',
  )
  const [agentId, setAgentId] = useState('')
  const [prompt, setPrompt] = useState('')
  const [contextText, setContextText] = useState('')
  const [timeoutSeconds, setTimeoutSeconds] = useState(60)
  const [receipt, setReceipt] = useState<ExternalTaskReceipt | null>(null)

  const effectiveAgentId = agentId || agents[0]?.id || ''
  const selectedAgent = agents.find((a) => a.id === effectiveAgentId)

  // 切换目标 Agent 时清空上一条回执，避免误读为新任务的结果
  useEffect(() => {
    setReceipt(null)
  }, [effectiveAgentId])

  const dispatch = useMutation({
    mutationFn: () => {
      let context: Record<string, unknown> | null = null
      if (contextText.trim().length > 0) {
        try {
          context = JSON.parse(contextText) as Record<string, unknown>
        } catch {
          throw new Error('CONTEXT_JSON_INVALID')
        }
      }
      return dispatchTaskToAgent(effectiveAgentId, {
        prompt: prompt.trim(),
        context,
        timeout_seconds: timeoutSeconds,
      })
    },
    onSuccess: (res) => {
      setReceipt(res)
      toast.success(`已分派给 ${selectedAgent?.name ?? effectiveAgentId}，回执 ${res.task_id}`)
    },
    onError: (err: unknown) => {
      if (err instanceof Error && err.message === 'CONTEXT_JSON_INVALID') {
        toast.error('上下文不是合法 JSON，请检查格式或留空')
        return
      }
      toast.error(errorDetail(err) ?? '任务分派失败，请确认该 Agent 已连接')
    },
  })

  if (agents.length === 0) return null

  return (
    <SectionPanel
      title="任务委托"
      description="把子任务直接交给外部 Agent 执行，回执由服务端 Local Agent Bridge 返回"
      icon={<Send className="w-4 h-4" />}
      bodyClassName="px-5 py-4 space-y-3"
    >
      {connectable.length === agents.length ? (
        <EmptyState
          icon={Power}
          title="所有外部 Agent 都未连接"
          description="先在上方清单里点击「连接 Agent」，连接成功后才能分派子任务。"
        />
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (effectiveAgentId.length > 0 && prompt.trim().length > 0) dispatch.mutate()
          }}
        >
          <label className="block">
            <span className="block text-sm font-medium text-text-secondary mb-1.5">目标 Agent</span>
            <select
              value={effectiveAgentId}
              onChange={(e) => setAgentId(e.target.value)}
              className="w-full px-3 py-2 bg-elevated text-text-primary border border-border-default rounded-lg text-body focus:outline-none focus:border-brand-500"
            >
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}（{EXTERNAL_STATUS_META[a.status]?.label ?? a.status}）
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="block text-sm font-medium text-text-secondary mb-1.5">任务说明</span>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="例：为订单审核模块补全基于 AST 的单元测试，并输出覆盖率缺口清单。"
              className="w-full px-3 py-2 bg-elevated text-text-primary border border-border-default rounded-lg text-body placeholder:text-text-tertiary focus:outline-none focus:border-brand-500"
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="超时（秒）"
              type="number"
              min={1}
              max={600}
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value) || 60)}
            />
            <label className="block">
              <span className="block text-sm font-medium text-text-secondary mb-1.5">上下文 JSON</span>
              <input
                value={contextText}
                onChange={(e) => setContextText(e.target.value)}
                placeholder="可选，如 {&quot;repo&quot;: &quot;erp&quot;}"
                className="w-full px-3 py-2 bg-elevated text-text-primary border border-border-default rounded-lg font-mono text-[12px]"
              />
            </label>
          </div>
          <Button
            type="submit"
            disabled={effectiveAgentId.length === 0 || prompt.trim().length === 0 || dispatch.isPending}
          >
            {dispatch.isPending ? (
              <Spinner size="sm" />
            ) : (
              <Send className="w-3.5 h-3.5" aria-hidden="true" />
            )}
            {dispatch.isPending ? '分派中…' : '分派任务'}
          </Button>
          {receipt && (
            <div className="border border-border-default rounded-[6px] p-3 bg-elevated space-y-1.5">
              <p className="text-[11px] font-mono text-text-tertiary">
                {receipt.task_id} · {receipt.status} · {receipt.duration_ms}ms
              </p>
              <p className="text-[12px] text-text-secondary leading-relaxed">{receipt.result}</p>
            </div>
          )}
        </form>
      )}
    </SectionPanel>
  )
}

function DispatchCard({
  runners,
  runningCount,
  onDispatched,
}: {
  runners: Array<{ runner_id: string; online: boolean }>
  runningCount: number
  onDispatched: () => void
}) {
  const onlineRunners = runners.filter((r) => r.online)
  const [runnerId, setRunnerId] = useState('')
  const [channel, setChannel] = useState<PhysicalChannel>('browser_action')
  const [op, setOp] = useState('click')
  const [target, setTarget] = useState('')
  const [text, setText] = useState('')
  const [risk, setRisk] = useState<'normal' | 'high'>('normal')

  const effectiveRunnerId = runnerId || onlineRunners[0]?.runner_id || ''

  const mutation = useMutation({
    mutationFn: () => {
      const step: PhysicalStep = {
        op: op.trim(),
        target: target.trim() || null,
        text: text.trim() || null,
        risk,
      }
      return dispatchPhysicalTask({ channel, runner_id: effectiveRunnerId, steps: [step] })
    },
    onSuccess: (task) => {
      toast.success(`任务已受理：${task.verdict.reason}`)
      setText('')
      onDispatched()
    },
    onError: (err: unknown) => {
      toast.error(errorDetail(err) ?? '任务下发失败，请检查执行器是否在线')
    },
  })

  return (
    <SectionPanel
      title="端侧物理任务分派"
      description="把浏览器 / 桌面操作交给端侧执行器，护栏实时裁决"
      icon={<Gauge className="w-4 h-4" />}
      bodyClassName="px-5 py-4 space-y-3"
    >
      {onlineRunners.length === 0 ? (
        <EmptyState
          icon={Bot}
          title="暂无可用端侧执行器"
          description="需要先在公司电脑上启动 AutoTeams 端侧执行器（心跳正常后才会出现在这里），才能下发物理任务。"
        />
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (effectiveRunnerId.length > 0 && op.trim().length > 0) mutation.mutate()
          }}
        >
          <Input
            label="执行器"
            value={effectiveRunnerId}
            onChange={(e) => setRunnerId(e.target.value)}
            hint={
              onlineRunners.length > 1
                ? `在线节点：${onlineRunners.map((r) => r.runner_id).join('、')}`
                : undefined
            }
          />
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="block text-sm font-medium text-text-secondary mb-1.5">通道</span>
              <select
                value={channel}
                onChange={(e) => setChannel(e.target.value as PhysicalChannel)}
                className="w-full px-3 py-2 bg-elevated text-text-primary border border-border-default rounded-lg text-body focus:outline-none focus:border-brand-500"
              >
                <option value="browser_action">浏览器操作</option>
                <option value="desktop_accessibility">桌面辅助功能</option>
              </select>
            </label>
            <Input label="指令 op" value={op} onChange={(e) => setOp(e.target.value)} />
          </div>
          <Input
            label="目标 target"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            hint="CSS 选择器或无障碍节点路径"
          />
          <Input
            label="录入文本 text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            hint="严禁填写账号密码，请使用凭据机引用"
          />
          <label className="flex items-center gap-2 text-[12px] text-text-secondary">
            <input
              type="checkbox"
              checked={risk === 'high'}
              onChange={(e) => setRisk(e.target.checked ? 'high' : 'normal')}
            />
            标记为高危（触发二次核准门禁）
          </label>
          <Button
            type="submit"
            disabled={effectiveRunnerId.length === 0 || op.trim().length === 0 || mutation.isPending}
          >
            <Play className="w-3.5 h-3.5" aria-hidden="true" />
            {mutation.isPending ? '下发中…' : '下发任务'}
          </Button>
          <p className="text-[11px] text-text-tertiary">当前进行中任务 {runningCount} 项</p>
        </form>
      )}
    </SectionPanel>
  )
}

function ToolConsole({ servers, tools }: { servers: MCPServer[]; tools: MCPTool[] }) {
  const [serverId, setServerId] = useState('')
  const [toolName, setToolName] = useState('')
  const [argsText, setArgsText] = useState('{}')
  const [result, setResult] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [callError, setCallError] = useState<string | null>(null)

  const effectiveServerId = serverId || servers[0]?.server_id || ''
  const availableTools = tools.filter((t) => !effectiveServerId || t.server_id === effectiveServerId)
  const effectiveTool = toolName || availableTools[0]?.name || ''

  // 端侧工具的必填参数（device_id / relative_path）随工具切换而变：
  // 切换工具时按契约重建入参骨架，避免残留上一个工具的字段。
  const requiredArgs = useMemo(() => {
    const tool = tools.find((t) => t.name === effectiveTool)
    return tool?.inputSchema?.required ?? []
  }, [tools, effectiveTool])

  // 入参骨架由契约推导，effect 只负责把它写进输入框。
  // 字符串相等时 effect 不会重跑，因此查询刷新不会抹掉用户已填的值。
  const argsSkeleton = useMemo(() => {
    const skeleton: Record<string, string> = {}
    for (const key of requiredArgs) skeleton[key] = ''
    return JSON.stringify(skeleton, null, 2)
  }, [requiredArgs])

  useEffect(() => {
    setArgsText(argsSkeleton)
  }, [argsSkeleton])

  if (tools.length === 0) {
    return (
      <EmptyState
        icon={Wrench}
        title="暂无可用外部工具"
        description="接入 Codex / Claude Code 等 MCP Agent 后，它们暴露的工具会出现在这里，供数字员工直接调用。"
      />
    )
  }

  const invoke = async () => {
    let args: Record<string, unknown>
    try {
      args = JSON.parse(argsText) as Record<string, unknown>
    } catch {
      toast.error('参数不是合法 JSON')
      return
    }
    setPending(true)
    setResult(null)
    setCallError(null)
    try {
      const res = await callMCPTool(effectiveServerId, effectiveTool, args)
      setResult(JSON.stringify(res, null, 2))
      if (res.success) {
        toast.success('工具调用成功')
      } else {
        // 「端侧执行器未接入 / 未实现」是能力缺失，不能按成功展示。
        setCallError(res.error ?? '工具返回失败')
        toast.error('工具返回失败')
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : '工具调用失败，请检查运行时连接'
      setCallError(message)
      toast.error(message)
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {availableTools.map((t) => (
          <button
            key={`${t.server_id}-${t.name}`}
            type="button"
            onClick={() => setToolName(t.name)}
            className={`text-[11px] font-mono px-2 py-1 rounded-[4px] border transition-colors ${
              effectiveTool === t.name
                ? 'border-brand-500 text-brand-500 bg-brand-50'
                : 'border-border-default text-text-tertiary hover:text-text-primary'
            }`}
          >
            {t.name}
          </button>
        ))}
      </div>
      {servers.length > 1 && (
        <select
          value={effectiveServerId}
          onChange={(e) => setServerId(e.target.value)}
          aria-label="MCP 服务器"
          className="w-full px-3 py-2 bg-elevated text-text-primary border border-border-default rounded-lg text-body"
        >
          {servers.map((s) => (
            <option key={s.server_id} value={s.server_id}>
              {s.name}
            </option>
          ))}
        </select>
      )}
      <textarea
        value={argsText}
        onChange={(e) => setArgsText(e.target.value)}
        rows={3}
        aria-label="工具参数"
        className="w-full px-3 py-2 bg-elevated text-text-primary border border-border-default rounded-lg font-mono text-[12px]"
      />
      <Button
        variant="secondary"
        size="sm"
        disabled={pending || effectiveTool.length === 0}
        onClick={() => void invoke()}
      >
        试调用 {effectiveTool}
      </Button>
      {callError && (
        <p className="text-[11px] text-text-error border border-border-default bg-elevated rounded-[4px] px-2 py-1.5">
          {callError}
        </p>
      )}
      {result && (
        <pre className="text-[11px] font-mono bg-elevated text-text-secondary p-3 rounded-[4px] overflow-x-auto max-h-48">
          {result}
        </pre>
      )}
    </div>
  )
}
