/**
 * ToolEcosystem — 工具生态（MCP + Local Runner）（蓝图 09）。
 *
 * 回答的问题：工具哪来、端侧是否在线。
 *
 * 主导区 = MCP 服务列表（选中项 2px 主色左标） + 选中服务的工具契约表
 * 次级区 = 端侧执行器（Local Runner）连接卡片与端侧运行引导
 *
 * 数据全部来自后端既有接口：
 * - GET  /mcp/servers   标准 MCP 客户端已连接的服务器（MCPServerConfig）
 * - GET  /mcp/tools     全部工具契约（MCPToolDefinition）
 * - POST /mcp/call      沙箱试调用
 * - GET  /local-paths   端侧 Runner 授权与探针状态
 * - POST /local-paths/register  端侧运行引导（签发一次性 setup 命令）
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Copy, Play, X } from 'lucide-react'
import { toast } from 'sonner'
import Layout from '@/components/Layout'
import {
  PAPER,
  PrimaryButton,
  SecondaryButton,
  SectionTitle,
  StatusLabel,
  HairlineBox,
  Field,
  EmptyState,
  type StatusTone,
} from '@/components/editorial'
import {
  listMCPServers,
  listMCPTools,
  callMCPTool,
  type MCPServer,
  type MCPTool,
  type MCPToolResult,
  type MCPTransport,
} from '@/api/mcp'
import { listLocalPaths, registerLocalPath, type LocalPathGrant } from '@/api/localPaths'

/** 传输协议 → 展示文案（后端 MCPServerConfig.transport）。 */
const TRANSPORT_LABELS: Record<MCPTransport, string> = {
  stdio: 'stdio 子进程',
  sse: 'SSE 事件流',
  runner_bridge: '端侧 Runner 桥接',
}

/** 端侧探针状态 → 状态点语义（LocalPathGrant.status）。 */
const PROBE_TONE: Record<LocalPathGrant['status'], StatusTone> = {
  connected: 'success',
  pending: 'warning',
  offline: 'subtle',
  revoked: 'danger',
}

/** 端侧探针状态 → 13px 状态文案。 */
const PROBE_LABEL: Record<LocalPathGrant['status'], string> = {
  connected: '在线',
  pending: '待连接',
  offline: '离线',
  revoked: '已撤销',
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return '未知错误'
}

/** MCP 执行结果（后端 MCPToolResult）收敛。 */
function parseToolResult(value: unknown): MCPToolResult {
  const raw = (typeof value === 'object' && value !== null ? value : {}) as Partial<MCPToolResult>
  return {
    success: raw.success === true,
    data: raw.data,
    error: typeof raw.error === 'string' ? raw.error : null,
    execution_time_ms: typeof raw.execution_time_ms === 'number' ? raw.execution_time_ms : 0,
  }
}

export default function ToolEcosystem() {
  const [servers, setServers] = useState<MCPServer[]>([])
  const [tools, setTools] = useState<MCPTool[]>([])
  const [grants, setGrants] = useState<LocalPathGrant[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedServerId, setSelectedServerId] = useState('')

  // 沙箱试调用抽屉
  const [callOpen, setCallOpen] = useState(false)
  const [callToolName, setCallToolName] = useState('')
  const [callArgs, setCallArgs] = useState('{}')
  const [calling, setCalling] = useState(false)
  const [callResult, setCallResult] = useState<MCPToolResult | null>(null)

  // 端侧运行引导
  const [runnerPath, setRunnerPath] = useState('')
  const [runnerScope, setRunnerScope] = useState<'read' | 'read_write'>('read')
  const [registering, setRegistering] = useState(false)
  const [setupCommand, setSetupCommand] = useState<string>('')

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [serverList, toolList, grantList] = await Promise.all([
        listMCPServers().catch(() => [] as MCPServer[]),
        listMCPTools().catch(() => [] as MCPTool[]),
        listLocalPaths().catch(() => [] as LocalPathGrant[]),
      ])
      setServers(serverList || [])
      setTools(toolList || [])
      setGrants(grantList || [])
      setSelectedServerId((prev) =>
        prev && (serverList || []).some((s) => s.server_id === prev)
          ? prev
          : serverList?.[0]?.server_id ?? '',
      )
    } catch (err: unknown) {
      toast.error('获取工具生态数据失败：' + errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const selectedServer = servers.find((s) => s.server_id === selectedServerId) ?? null

  const toolsByServer = useMemo(() => {
    const map = new Map<string, MCPTool[]>()
    for (const tool of tools) {
      const bucket = map.get(tool.server_id)
      if (bucket) bucket.push(tool)
      else map.set(tool.server_id, [tool])
    }
    return map
  }, [tools])

  const selectedTools = selectedServerId ? toolsByServer.get(selectedServerId) ?? [] : []
  const probeCount = grants.filter((g) => g.status === 'connected').length

  const openCall = (tool: MCPTool | null) => {
    setCallResult(null)
    setCallToolName(tool?.name ?? selectedTools[0]?.name ?? '')
    // 以契约中的必填参数生成一份最小入参骨架，避免凭空编造取值。
    const required = tool?.inputSchema?.required ?? []
    const skeleton: Record<string, string> = {}
    for (const key of required) skeleton[key] = ''
    setCallArgs(JSON.stringify(tool ? skeleton : {}, null, 2))
    setCallOpen(true)
  }

  const handleCall = async () => {
    if (!selectedServer) {
      toast.error('请先选择 MCP 服务')
      return
    }
    if (!callToolName.trim()) {
      toast.error('请填写工具名')
      return
    }
    let args: Record<string, unknown>
    try {
      const parsed: unknown = JSON.parse(callArgs || '{}')
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        throw new Error('入参必须是 JSON 对象')
      }
      args = parsed as Record<string, unknown>
    } catch (err: unknown) {
      toast.error('入参 JSON 解析失败：' + errorMessage(err))
      return
    }

    setCalling(true)
    setCallResult(null)
    try {
      const raw: unknown = await callMCPTool(selectedServer.server_id, callToolName.trim(), args)
      const result = parseToolResult(raw)
      setCallResult(result)
      if (result.success) toast.success('沙箱试调用成功')
      else toast.error('试调用失败：' + (result.error ?? '未知错误'))
    } catch (err: unknown) {
      toast.error('试调用请求失败：' + errorMessage(err))
    } finally {
      setCalling(false)
    }
  }

  const handleRegisterRunner = async () => {
    if (!runnerPath.trim()) {
      toast.error('请填写端侧目录绝对路径')
      return
    }
    setRegistering(true)
    try {
      const res = await registerLocalPath({
        local_path: runnerPath.trim(),
        scope: runnerScope,
      })
      setSetupCommand(res.setup_command)
      setGrants((prev) => [res.grant, ...prev])
      toast.success('已签发一次性连接命令，请在本机执行')
    } catch (err: unknown) {
      toast.error('签发连接命令失败：' + errorMessage(err))
    } finally {
      setRegistering(false)
    }
  }

  return (
    <Layout fluid>
      <div className="flex-1 overflow-y-auto" style={{ background: PAPER.canvas }}>
        <div className="mx-auto w-full max-w-[1280px] px-6 py-8 md:px-10 md:py-10">
          {/* ============ 页头 ============ */}
          <header className="flex flex-col gap-4 border-b pb-5 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-[30px] font-bold leading-[38px] tracking-tight" style={{ color: PAPER.ink }}>
                工具生态
              </h1>
              <p className="mt-1.5 text-[13px] leading-5" style={{ color: PAPER.muted }}>
                {servers.length} 个 MCP 服务 · {tools.length} 个工具契约 · {probeCount} 台端侧探针在线
              </p>
            </div>
            <PrimaryButton onClick={() => openCall(null)} disabled={servers.length === 0}>
              <Play className="h-4 w-4" aria-hidden="true" />
              沙箱试调用
            </PrimaryButton>
          </header>

          <div className="mt-8 grid grid-cols-12 items-start gap-8">
            {/* ============ 主导区左：MCP 服务列表 ============ */}
            <section className="col-span-12 lg:col-span-5">
              <HairlineBox>
                <div
                  className="flex items-center justify-between border-b px-4 py-3"
                  style={{ borderColor: PAPER.hair }}
                >
                  <h2 className="text-[17px] font-semibold leading-6" style={{ color: PAPER.ink }}>
                    MCP 服务
                  </h2>
                  <span className="font-mono text-[12px] tracking-wider" style={{ color: PAPER.subtle }}>
                    COUNT: {String(servers.length).padStart(2, '0')}
                  </span>
                </div>
                {loading ? (
                  <EmptyState text="正在加载 MCP 服务…" />
                ) : servers.length === 0 ? (
                  <EmptyState text="当前环境尚未注册任何 MCP 服务。" />
                ) : (
                  <div className="divide-y" style={{ borderColor: PAPER.hair }}>
                    {servers.map((server) => {
                      const active = server.server_id === selectedServerId
                      const count = toolsByServer.get(server.server_id)?.length ?? 0
                      return (
                        <button
                          key={server.server_id}
                          type="button"
                          onClick={() => setSelectedServerId(server.server_id)}
                          className="flex w-full items-center justify-between gap-3 border-l-2 px-4 py-3.5 text-left transition-colors hover:bg-[#F4F4F3]"
                          style={{
                            borderColor: PAPER.hair,
                            borderLeftColor: active ? PAPER.primary : 'transparent',
                            background: active ? PAPER.alt : undefined,
                          }}
                        >
                          <span className="min-w-0">
                            <span
                              className="block text-[14px] font-medium"
                              style={{ color: active ? PAPER.ink : PAPER.muted }}
                            >
                              {server.name}
                            </span>
                            <span className="mt-0.5 block font-mono text-[12px]" style={{ color: PAPER.muted }}>
                              {TRANSPORT_LABELS[server.transport]} · {count} 个工具 ·{' '}
                              {server.timeout_seconds}s 超时
                            </span>
                          </span>
                          <span className="shrink-0">
                            <StatusLabel tone={server.is_active ? 'success' : 'subtle'}>
                              {server.is_active ? '在线' : '停用'}
                            </StatusLabel>
                          </span>
                        </button>
                      )
                    })}
                  </div>
                )}
              </HairlineBox>

              {selectedServer && (
                <p className="mt-3 font-mono text-[12px] leading-5" style={{ color: PAPER.subtle }}>
                  server_id: {selectedServer.server_id}
                  {selectedServer.url ? ` · endpoint: ${selectedServer.url}` : ''}
                  {selectedServer.command ? ` · command: ${selectedServer.command}` : ''}
                </p>
              )}
            </section>

            {/* ============ 主导区右：工具清单 + 端侧执行器 ============ */}
            <div className="col-span-12 flex flex-col gap-8 lg:col-span-7">
              <section className="space-y-4">
                <SectionTitle
                  title="工具清单"
                  meta={selectedServer ? `${selectedTools.length} TOOLS` : 'NO SERVICE'}
                />
                <HairlineBox>
                  {!selectedServer ? (
                    <EmptyState text="请先在左侧选择一个 MCP 服务。" />
                  ) : selectedTools.length === 0 ? (
                    <EmptyState text="该服务暂未暴露工具契约。" />
                  ) : (
                    <>
                      <div
                        className="grid grid-cols-12 px-4 py-2.5 text-[12px]"
                        style={{ background: PAPER.alt, color: PAPER.muted }}
                      >
                        <div className="col-span-5">工具名</div>
                        <div className="col-span-5">说明</div>
                        <div className="col-span-2 text-right">必填</div>
                      </div>
                      <div className="divide-y" style={{ borderColor: PAPER.hair }}>
                        {selectedTools.map((tool) => (
                          <button
                            key={tool.name}
                            type="button"
                            onClick={() => openCall(tool)}
                            title="在沙箱中试调用该工具"
                            className="grid w-full grid-cols-12 items-center px-4 py-3.5 text-left transition-colors hover:bg-[#F4F4F3]"
                          >
                            <span className="col-span-5 font-mono text-[13px]" style={{ color: PAPER.ink }}>
                              {tool.name}
                            </span>
                            <span className="col-span-5 pr-3 text-[13px]" style={{ color: PAPER.muted }}>
                              {tool.description || '—'}
                            </span>
                            <span className="col-span-2 text-right text-[13px]" style={{ color: PAPER.muted }}>
                              {(tool.inputSchema?.required ?? []).length} 项
                            </span>
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </HairlineBox>
              </section>

              {/* 端侧执行器（Local Runner） */}
              <section className="space-y-4">
                <SectionTitle title="端侧执行器" meta={`PROBES ONLINE: ${probeCount}`} />
                <HairlineBox>
                  {grants.length === 0 ? (
                    <EmptyState text="尚未授权任何端侧执行器。文件读写与受控脚本必须在员工本机的 Local Runner 内执行。" />
                  ) : (
                    <div className="divide-y" style={{ borderColor: PAPER.hair }}>
                      {grants.map((grant) => (
                        <div
                          key={grant.id}
                          className="flex flex-wrap items-center justify-between gap-3 px-4 py-3.5"
                          style={{ borderColor: PAPER.hair }}
                        >
                          <div className="min-w-0">
                            <div className="text-[14px] font-medium" style={{ color: PAPER.ink }}>
                              {grant.label || grant.local_path}
                            </div>
                            <div className="mt-0.5 font-mono text-[12px]" style={{ color: PAPER.subtle }}>
                              {grant.scope === 'read_write' ? '读写' : '只读'} ·{' '}
                              {grant.runner_id ?? 'runner 未认领'}
                            </div>
                          </div>
                          <StatusLabel tone={PROBE_TONE[grant.status]}>{PROBE_LABEL[grant.status]}</StatusLabel>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* 端侧运行引导 */}
                  <div className="border-t px-4 py-4" style={{ borderColor: PAPER.hair, background: PAPER.alt }}>
                    <p className="mb-3 text-[13px] font-medium" style={{ color: PAPER.ink }}>
                      端侧运行引导
                    </p>
                    <div className="grid grid-cols-12 gap-3">
                      <div className="col-span-12 sm:col-span-7">
                        <Field
                          label="本机目录绝对路径"
                          mono
                          value={runnerPath}
                          onChange={setRunnerPath}
                          placeholder="D:\\项目交付"
                        />
                      </div>
                      <div className="col-span-12 sm:col-span-5">
                        <span className="mb-1 block text-[12px]" style={{ color: PAPER.muted }}>
                          授权范围
                        </span>
                        <select
                          value={runnerScope}
                          onChange={(e) => setRunnerScope(e.target.value === 'read_write' ? 'read_write' : 'read')}
                          className="w-full rounded-[6px] px-3 py-2 text-[14px] outline-none"
                          style={{ background: PAPER.card, border: `1px solid ${PAPER.hair}`, color: PAPER.ink }}
                        >
                          <option value="read">只读</option>
                          <option value="read_write">读写</option>
                        </select>
                      </div>
                    </div>
                    <div className="mt-3">
                      <SecondaryButton onClick={handleRegisterRunner} disabled={registering}>
                        {registering ? '签发中…' : '签发一次性连接命令'}
                      </SecondaryButton>
                    </div>
                    {setupCommand && (
                      <div
                        className="mt-3 flex items-start justify-between gap-3 rounded-[6px] p-3"
                        style={{ background: PAPER.card, border: `1px solid ${PAPER.hair}` }}
                      >
                        <code className="min-w-0 flex-1 break-all font-mono text-[12px]" style={{ color: PAPER.ink }}>
                          {setupCommand}
                        </code>
                        <button
                          type="button"
                          aria-label="复制连接命令"
                          onClick={() => {
                            void navigator.clipboard?.writeText(setupCommand)
                            toast.success('连接命令已复制')
                          }}
                          className="shrink-0 rounded-[6px] p-1"
                          style={{ color: PAPER.muted }}
                        >
                          <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                        </button>
                      </div>
                    )}
                  </div>
                </HairlineBox>
              </section>
            </div>
          </div>

          {/* ============ 页脚说明 ============ */}
          <p className="mt-8 border-t pt-4 text-[12px]" style={{ borderColor: PAPER.hair, color: PAPER.subtle }}>
            试调用直接命中真实执行端，由后端按服务的 timeout_seconds 强制中断；所有凭据对称加密落库，接口不回传明文。
          </p>
        </div>
      </div>

      {/* ============ 抽屉：沙箱试调用 ============ */}
      {callOpen && (
        <SideDrawer
          title="沙箱试调用"
          onClose={() => setCallOpen(false)}
          description="调用真实 MCP 执行端，用于在接入前确认工具契约与返回结构。"
        >
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="mb-1 block text-[12px]" style={{ color: PAPER.muted }}>
                  MCP 服务
                </span>
                <select
                  value={selectedServerId}
                  onChange={(e) => setSelectedServerId(e.target.value)}
                  className="w-full rounded-[6px] px-3 py-2 text-[14px] outline-none"
                  style={{ background: PAPER.card, border: `1px solid ${PAPER.hair}`, color: PAPER.ink }}
                >
                  {servers.map((s) => (
                    <option key={s.server_id} value={s.server_id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-[12px]" style={{ color: PAPER.muted }}>
                  工具
                </span>
                <select
                  value={callToolName}
                  onChange={(e) => setCallToolName(e.target.value)}
                  className="w-full rounded-[6px] px-3 py-2 text-[14px] outline-none"
                  style={{ background: PAPER.card, border: `1px solid ${PAPER.hair}`, color: PAPER.ink }}
                >
                  {selectedTools.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="block">
              <span className="mb-1 block text-[12px]" style={{ color: PAPER.muted }}>
                入参（JSON）
              </span>
              <textarea
                value={callArgs}
                onChange={(e) => setCallArgs(e.target.value)}
                rows={8}
                className="w-full resize-y rounded-[6px] px-3 py-2 font-mono text-[13px] leading-5 outline-none"
                style={{ background: PAPER.card, border: `1px solid ${PAPER.hair}`, color: PAPER.ink }}
              />
            </label>

            <div className="flex gap-2">
              <PrimaryButton onClick={handleCall} disabled={calling}>
                {calling ? '执行中…' : '执行'}
              </PrimaryButton>
              <SecondaryButton onClick={() => setCallOpen(false)}>关闭</SecondaryButton>
            </div>

            {callResult && (
              <div className="border-t pt-4" style={{ borderColor: PAPER.hair }}>
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[13px] font-medium" style={{ color: PAPER.ink }}>
                    执行结果
                  </span>
                  <span className="flex items-center gap-3">
                    <span className="font-mono text-[12px]" style={{ color: PAPER.muted }}>
                      {callResult.execution_time_ms} ms
                    </span>
                    <StatusLabel tone={callResult.success ? 'success' : 'danger'}>
                      {callResult.success ? '成功' : '失败'}
                    </StatusLabel>
                  </span>
                </div>
                <pre
                  className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-[6px] p-3 font-mono text-[12px] leading-5"
                  style={{ background: PAPER.alt, border: `1px solid ${PAPER.hair}`, color: PAPER.ink }}
                >
                  {callResult.success
                    ? JSON.stringify(callResult.data, null, 2)
                    : callResult.error || '无错误详情'}
                </pre>
              </div>
            )}
          </div>
        </SideDrawer>
      )}
    </Layout>
  )
}

/** 右侧抽屉：遮罩 + 浮层阴影 0 12px 32px rgba(0,0,0,.10)。 */
function SideDrawer({
  title,
  description,
  onClose,
  children,
}: {
  title: string
  description?: string
  onClose: () => void
  children: ReactNode
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0"
        style={{ background: 'rgba(11,11,11,0.32)' }}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        role="dialog"
        aria-label={title}
        className="relative flex h-full w-full max-w-[480px] flex-col overflow-y-auto"
        style={{ background: PAPER.card, boxShadow: '0 12px 32px rgba(0,0,0,0.10)' }}
      >
        <div
          className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b px-6 py-4"
          style={{ borderColor: PAPER.hair, background: PAPER.card }}
        >
          <div className="min-w-0">
            <h2 className="text-[17px] font-semibold leading-6" style={{ color: PAPER.ink }}>
              {title}
            </h2>
            {description && (
              <p className="mt-1 text-[12px] leading-5" style={{ color: PAPER.muted }}>
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="rounded-[6px] p-1"
            style={{ color: PAPER.muted }}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <div className="flex-1 px-6 py-5">{children}</div>
      </aside>
    </div>
  )
}
