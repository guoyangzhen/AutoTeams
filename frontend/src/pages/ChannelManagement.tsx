/**
 * ChannelManagement — 全渠道接入中枢（蓝图 08）。
 *
 * 回答的问题：员工在企微 / 飞书上线了吗。
 *
 * 主导区 = 渠道列表（发丝线行，无卡片包裹）
 * 次级区 = 路由规则 + SOP 保护窗（灰度观测） + 防重放缓存状态
 *
 * 数据全部来自后端既有接口，无任何前端造数：
 * - GET/POST/PUT/DELETE /connectors/accounts   渠道接入配置（凭据对称加密落库）
 * - POST /connectors/simulate/inbound           回调链路调试（等价于真实 Webhook 入站）
 * - POST /connectors/bind/generate              跨渠道一次性身份绑定码
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Plus, X, Copy } from 'lucide-react'
import { toast } from 'sonner'
import Layout from '@/components/Layout'
import {
  PAPER,
  PrimaryButton,
  SecondaryButton,
  TextLink,
  SectionTitle,
  StatusLabel,
  HairlineBox,
  Field,
  EmptyState,
  type StatusTone,
} from '@/components/editorial'
import {
  listChannelAccounts,
  createChannelAccount,
  updateChannelAccount,
  deleteChannelAccount,
  generateBindToken,
  simulateInboundMessage,
  type ChannelAccountItem,
} from '@/api/connectors'
import { listWorkforceProfiles, type WorkforceProfile } from '@/api/workforceProfiles'

/** 网关分发结果（后端 GatewayDispatchResult）。 */
interface DispatchResult {
  is_command: boolean
  command_response: string | null
  dispatched_profile_id: string | null
  dispatched_profile_name: string | null
  dispatched_profile_badge: string | null
  is_sop_protected: boolean
  active_flow_id: string | null
  should_execute_flow: boolean
  system_notice: string | null
}

/** 一次回调测试的完整观测记录（页面内会话级）。 */
interface DispatchObservation {
  seq: number
  at: string
  accountName: string
  messageId: string
  replayed: boolean
  result: DispatchResult
}

/** 渠道类型 → 展示名 + 传输说明 + 回调前缀。 */
interface ChannelMeta {
  name: string
  transport: string
  webhook: 'wecom' | 'feishu' | 'generic'
}

const CHANNEL_LABELS: Record<string, ChannelMeta> = {
  wecom_bot: { name: '企业微信', transport: 'Webhook 回调', webhook: 'wecom' },
  feishu_app: { name: '飞书', transport: '事件订阅', webhook: 'feishu' },
  dingtalk_bot: { name: '钉钉', transport: '机器人回调', webhook: 'generic' },
  generic_webhook: { name: '开放平台', transport: 'HTTP 回调签名', webhook: 'generic' },
}

/** 新建渠道时可选的类型（与后端 ChannelAccountCreate 文档一致）。 */
const CREATABLE_TYPES = ['wecom_bot', 'feishu_app', 'dingtalk_bot', 'generic_webhook'] as const

/** 网关去重命中时回执中的固定文案（后端 gateway.py 幂等分支）。 */
const REPLAY_MARK = '忽略重复消息'

/** 未知异常 → 可读文案。 */
function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return '未知错误'
}

/** 将后端返回的未知负载收敛为 GatewayDispatchResult 形状。 */
function parseDispatchResult(value: unknown): DispatchResult {
  const raw = (typeof value === 'object' && value !== null ? value : {}) as Partial<DispatchResult>
  const str = (v: unknown): string | null => (typeof v === 'string' ? v : null)
  return {
    is_command: raw.is_command === true,
    command_response: str(raw.command_response),
    dispatched_profile_id: str(raw.dispatched_profile_id),
    dispatched_profile_name: str(raw.dispatched_profile_name),
    dispatched_profile_badge: str(raw.dispatched_profile_badge),
    is_sop_protected: raw.is_sop_protected === true,
    active_flow_id: str(raw.active_flow_id),
    should_execute_flow: raw.should_execute_flow === true,
    system_notice: str(raw.system_notice),
  }
}

/** 渠道账号状态 → 状态点语义。 */
function accountTone(account: ChannelAccountItem): StatusTone {
  if (!account.is_active) return 'subtle'
  if (account.status === 'error') return 'danger'
  if (account.status === 'connected') return 'success'
  if (account.status === 'disabled') return 'subtle'
  return 'warning'
}

/** 渠道账号状态 → 13px 状态文案。 */
function accountLabel(account: ChannelAccountItem): string {
  if (!account.is_active) return '已停用'
  if (account.status === 'connected') return '在线'
  if (account.status === 'error') return '阻断'
  if (account.status === 'disabled') return '已停用'
  return '待联通'
}

/** 渠道 → 回调路径（与后端 routers 对齐）。 */
function webhookPath(account: ChannelAccountItem): string {
  const kind = CHANNEL_LABELS[account.channel_type]?.webhook ?? 'generic'
  return `/api/v1/connectors/${kind}/webhook/${account.id}`
}

const EMPTY_FORM = {
  name: '',
  description: '',
  wecomCorpId: '',
  wecomCorpSecret: '',
  wecomAgentId: '',
  wecomToken: '',
  wecomAesKey: '',
  feishuAppId: '',
  feishuAppSecret: '',
  feishuVerificationToken: '',
  feishuEncryptKey: '',
}

export default function ChannelManagement() {
  const [accounts, setAccounts] = useState<ChannelAccountItem[]>([])
  const [profiles, setProfiles] = useState<WorkforceProfile[]>([])
  const [loading, setLoading] = useState(true)

  // 身份绑定码
  const [bindToken, setBindToken] = useState<{ bind_token: string; instruction: string } | null>(null)

  // 接入 / 配置抽屉
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<ChannelAccountItem | null>(null)
  const [channelType, setChannelType] = useState<string>('wecom_bot')
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [mountedProfiles, setMountedProfiles] = useState<string[]>([])
  const [defaultProfile, setDefaultProfile] = useState('')
  const [saving, setSaving] = useState(false)

  // 回调测试抽屉
  const [testOpen, setTestOpen] = useState(false)
  const [testAccountId, setTestAccountId] = useState('')
  const [testUserId, setTestUserId] = useState('user_ext_8801')
  const [testUserName, setTestUserName] = useState('张明')
  const [testContent, setTestContent] = useState('帮我核对这份投标方案的资质要求')
  const [testMessageId, setTestMessageId] = useState('')
  const [testing, setTesting] = useState(false)
  const [observations, setObservations] = useState<DispatchObservation[]>([])

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [accs, profs] = await Promise.all([
        listChannelAccounts().catch(() => [] as ChannelAccountItem[]),
        listWorkforceProfiles().catch(() => [] as WorkforceProfile[]),
      ])
      setAccounts(accs || [])
      setProfiles(profs || [])
    } catch (err: unknown) {
      toast.error('获取全渠道接入数据失败：' + errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const profileMap = useMemo(
    () => new Map<string, WorkforceProfile>(profiles.map((p) => [p.id, p])),
    [profiles],
  )

  const onlineCount = accounts.filter((a) => a.is_active && a.status === 'connected').length
  const testAccount = accounts.find((a) => a.id === testAccountId) ?? null

  // ============================================================
  // 动作
  // ============================================================

  const openCreate = () => {
    setEditing(null)
    setChannelType('wecom_bot')
    setForm({ ...EMPTY_FORM })
    setMountedProfiles([])
    setDefaultProfile('')
    setFormOpen(true)
  }

  const openEdit = (account: ChannelAccountItem) => {
    setEditing(account)
    setChannelType(account.channel_type)
    // 凭据为对称加密落库且列表接口不回传明文，这里只回填非敏感项。
    setForm({ ...EMPTY_FORM, name: account.name, description: account.description || '' })
    setMountedProfiles(account.mounted_profile_ids || [])
    setDefaultProfile(account.default_profile_id || '')
    setFormOpen(true)
  }

  const handleGenerateBindToken = async () => {
    try {
      const res = await generateBindToken()
      setBindToken({ bind_token: res.bind_token, instruction: res.instruction })
    } catch (err: unknown) {
      toast.error('生成绑定码失败：' + errorMessage(err))
    }
  }

  const handleSaveAccount = async () => {
    if (!form.name.trim()) {
      toast.error('请填写渠道名称')
      return
    }
    const credentials: Record<string, string> =
      channelType === 'feishu_app'
        ? {
            app_id: form.feishuAppId.trim(),
            app_secret: form.feishuAppSecret.trim(),
            verification_token: form.feishuVerificationToken.trim(),
            encrypt_key: form.feishuEncryptKey.trim(),
          }
        : {
            corp_id: form.wecomCorpId.trim(),
            corp_secret: form.wecomCorpSecret.trim(),
            agent_id: form.wecomAgentId.trim(),
            token: form.wecomToken.trim(),
            encoding_aes_key: form.wecomAesKey.trim(),
          }
    const hasCredential = Object.values(credentials).some((v) => v.length > 0)

    setSaving(true)
    try {
      if (editing) {
        await updateChannelAccount(editing.id, {
          name: form.name.trim(),
          description: form.description.trim() || undefined,
          credentials: hasCredential ? credentials : undefined,
          mounted_profile_ids: mountedProfiles,
          default_profile_id: defaultProfile || undefined,
        })
        toast.success('渠道配置已更新')
      } else {
        await createChannelAccount({
          channel_type: channelType,
          name: form.name.trim(),
          description: form.description.trim() || undefined,
          credentials,
          mounted_profile_ids: mountedProfiles,
          default_profile_id: defaultProfile || undefined,
        })
        toast.success('渠道接入配置已创建')
      }
      setFormOpen(false)
      await fetchData()
    } catch (err: unknown) {
      toast.error('保存失败：' + errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const handleDeactivate = async (account: ChannelAccountItem) => {
    try {
      await deleteChannelAccount(account.id)
      toast.success(`渠道「${account.name}」已注销`)
      setFormOpen(false)
      await fetchData()
    } catch (err: unknown) {
      toast.error('注销失败：' + errorMessage(err))
    }
  }

  /**
   * 发送一条模拟入站消息。replay=true 时复用同一 message_id，
   * 用于验证网关的幂等去重（防重放）缓存是否生效。
   */
  const sendTestMessage = async (replay: boolean) => {
    if (!testAccount) {
      toast.error('请先选择渠道账号')
      return
    }
    if (!testContent.trim()) {
      toast.error('请输入回调消息内容')
      return
    }
    const messageId = testMessageId.trim() || `msg-${Date.now()}`
    if (!replay) setTestMessageId(messageId)

    setTesting(true)
    try {
      const raw: unknown = await simulateInboundMessage({
        account_id: testAccount.id,
        channel_type: testAccount.channel_type,
        external_user_id: testUserId.trim() || 'user_ext_8801',
        external_user_name: testUserName.trim() || undefined,
        content: testContent.trim(),
        message_id: messageId,
      })
      const result = parseDispatchResult(raw)
      setObservations((prev) => [
        {
          seq: prev.length + 1,
          at: new Date().toISOString(),
          accountName: testAccount.name,
          messageId,
          replayed: replay,
          result,
        },
        ...prev.slice(0, 7),
      ])
      if (result.command_response?.includes(REPLAY_MARK)) {
        toast.error('防重放缓存命中：网关已忽略该重复消息')
      } else {
        toast.success('网关已接收并完成分发')
      }
    } catch (err: unknown) {
      toast.error('回调测试失败：' + errorMessage(err))
    } finally {
      setTesting(false)
    }
  }

  // ============================================================
  // 渲染
  // ============================================================

  const isReplay = (o: DispatchObservation) => Boolean(o.result.command_response?.includes(REPLAY_MARK))
  const replayCount = observations.filter(isReplay).length
  const protectedObservations = observations.filter((o) => o.result.is_sop_protected)

  return (
    <Layout fluid>
      <div className="flex-1 overflow-y-auto" style={{ background: PAPER.canvas }}>
        <div className="mx-auto w-full max-w-[1280px] px-6 py-8 md:px-10 md:py-10">
          {/* ============ 页头 ============ */}
          <header className="flex flex-col gap-4 pb-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-[30px] font-bold leading-[38px] tracking-tight" style={{ color: PAPER.ink }}>
                全渠道接入
              </h1>
              <p className="mt-1.5 text-[13px] leading-5" style={{ color: PAPER.muted }}>
                {accounts.length} 个渠道 · {onlineCount} 个在线 · 幂等去重 300s 生效
              </p>
            </div>
            <PrimaryButton onClick={openCreate}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              接入渠道
            </PrimaryButton>
          </header>

          {/* 身份绑定码（一行事实 + 文字链） */}
          <div
            className="flex flex-wrap items-center gap-3 border-b pb-3 text-[13px]"
            style={{ borderColor: PAPER.hair, color: PAPER.muted }}
          >
            <span className="font-medium" style={{ color: PAPER.ink }}>
              跨渠道身份绑定：
            </span>
            {bindToken ? (
              <>
                <span className="font-mono text-[12px]" style={{ color: PAPER.ink }}>
                  {bindToken.instruction}
                </span>
                <TextLink
                  onClick={() => {
                    void navigator.clipboard?.writeText(bindToken.instruction)
                    toast.success('绑定指令已复制')
                  }}
                >
                  复制
                </TextLink>
              </>
            ) : (
              <TextLink onClick={handleGenerateBindToken}>生成一次性绑定码（10 分钟内有效）</TextLink>
            )}
          </div>

          {/* ============ 主导区：渠道列表 ============ */}
          <section className="mt-10 space-y-4">
            <SectionTitle title="渠道列表" meta={`COUNT: ${String(accounts.length).padStart(2, '0')}`} />
            <HairlineBox>
              {loading ? (
                <EmptyState text="正在加载渠道接入配置…" />
              ) : accounts.length === 0 ? (
                <EmptyState text="尚未接入任何渠道。点击「接入渠道」完成企业微信或飞书的应用配置。" />
              ) : (
                <div className="divide-y" style={{ borderColor: PAPER.hair }}>
                  {accounts.map((account) => {
                    const meta = CHANNEL_LABELS[account.channel_type]
                    const owner = account.default_profile_id
                      ? profileMap.get(account.default_profile_id)
                      : undefined
                    return (
                      <div
                        key={account.id}
                        className="flex min-h-12 flex-wrap items-center justify-between gap-3 px-6 py-3 transition-colors hover:bg-[#F4F4F3]"
                        style={{ borderColor: PAPER.hair }}
                      >
                        <div className="flex min-w-[240px] items-center gap-3">
                          <span className="text-[14px] font-medium" style={{ color: PAPER.ink }}>
                            {account.name}
                          </span>
                          <span className="font-mono text-[12px]" style={{ color: PAPER.subtle }}>
                            {meta ? `${meta.name} · ${meta.transport}` : account.channel_type}
                          </span>
                        </div>
                        <div className="flex w-28 items-center">
                          <StatusLabel tone={accountTone(account)}>{accountLabel(account)}</StatusLabel>
                        </div>
                        <div className="text-right">
                          <div className="font-mono text-[12px]" style={{ color: PAPER.muted }}>
                            {owner ? `承接 ${owner.employee_badge}` : '未设默认承接人'} · 挂载{' '}
                            {(account.mounted_profile_ids || []).length} 人
                          </div>
                          <div className="mt-0.5 flex items-center justify-end gap-3">
                            <TextLink
                              onClick={() => {
                                setTestAccountId(account.id)
                                setTestOpen(true)
                              }}
                            >
                              回调测试
                            </TextLink>
                            <TextLink onClick={() => openEdit(account)}>配置</TextLink>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </HairlineBox>
          </section>

          {/* ============ 次级区 1：路由规则 ============ */}
          <section className="mt-10 space-y-4">
            <SectionTitle title="路由规则" meta={`${accounts.length} ACTIVE DISPATCH POLICIES`} />
            <HairlineBox>
              {accounts.length === 0 ? (
                <EmptyState text="接入渠道后自动生成意图路由规则。" />
              ) : (
                <div className="divide-y" style={{ borderColor: PAPER.hair }}>
                  {accounts.map((account, index) => {
                    const owner = account.default_profile_id
                      ? profileMap.get(account.default_profile_id)
                      : undefined
                    const mounted = (account.mounted_profile_ids || [])
                      .map((id) => profileMap.get(id))
                      .filter((p): p is WorkforceProfile => Boolean(p))
                    return (
                      <div
                        key={account.id}
                        className="flex min-h-12 flex-wrap items-center justify-between gap-3 px-6 py-3"
                        style={{ borderColor: PAPER.hair }}
                      >
                        <div className="flex items-center gap-3">
                          <span className="font-mono text-[12px]" style={{ color: PAPER.subtle }}>
                            {String(index + 1).padStart(2, '0')}
                          </span>
                          <span className="text-[14px] font-medium" style={{ color: PAPER.ink }}>
                            {account.name}
                          </span>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[13px]" style={{ color: PAPER.muted }}>
                            {owner ? `优先路由至 ${owner.display_name}` : '按意图打分分发至挂载员工'}
                          </span>
                          <span
                            className="rounded border px-1.5 py-0.5 font-mono text-[12px]"
                            style={{ background: PAPER.alt, borderColor: PAPER.hair, color: PAPER.ink }}
                          >
                            {mounted.length > 0
                              ? mounted.map((p) => p.employee_badge).join(' / ')
                              : '未挂载员工'}
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
              {/* SOP 保护窗：灰度观测，事实来自本页回调测试的真实网关回执 */}
              <div
                className="flex flex-wrap items-center justify-between gap-3 px-6 py-3.5"
                style={{ background: PAPER.alt, color: PAPER.muted }}
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium" style={{ color: PAPER.ink }}>
                    会话保护窗：
                  </span>
                  <span>
                    {protectedObservations.length === 0
                      ? '本页回调测试未观测到 SOP 保护窗锁定，员工可自由切换'
                      : `${protectedObservations[0].accountName} 正在执行规程 ${
                          protectedObservations[0].result.active_flow_id || '未命名规程'
                        }，暂不可切换员工`}
                  </span>
                </div>
                <span className="font-mono text-[12px]" style={{ color: PAPER.subtle }}>
                  OBSERVED: {protectedObservations.length} / SENT: {observations.length}
                </span>
              </div>
            </HairlineBox>
          </section>

          {/* ============ 次级区 2：防重放缓存 ============ */}
          <section className="mt-10 space-y-4">
            <SectionTitle title="防重放缓存" meta={`REPLAY BLOCKED: ${replayCount}`} />
            <HairlineBox>
              {observations.length === 0 ? (
                <EmptyState text="尚无回调观测。在任一渠道点击「回调测试」，用同一消息 ID 发送两次即可验证去重。" />
              ) : (
                <div className="divide-y" style={{ borderColor: PAPER.hair }}>
                  {observations.map((o) => (
                    <div
                      key={o.seq}
                      className="flex min-h-12 flex-wrap items-center justify-between gap-3 px-6 py-3"
                      style={{ borderColor: PAPER.hair }}
                    >
                      <div className="flex items-center gap-3">
                        <span className="font-mono text-[12px]" style={{ color: PAPER.ink }}>
                          {o.messageId}
                        </span>
                        <span className="text-[13px]" style={{ color: PAPER.muted }}>
                          {o.accountName} · {o.replayed ? '重放' : '首次投递'}
                        </span>
                      </div>
                      <StatusLabel tone={isReplay(o) ? 'warning' : 'success'}>
                        {isReplay(o) ? '已拦截重放' : '首次放行'}
                      </StatusLabel>
                    </div>
                  ))}
                </div>
              )}
            </HairlineBox>
          </section>
        </div>
      </div>

      {/* ============================================================ */}
      {/* 抽屉：渠道接入 / 配置                                          */}
      {/* ============================================================ */}
      {formOpen && (
        <SideDrawer title={editing ? '渠道配置' : '接入渠道'} onClose={() => setFormOpen(false)}>
          <div className="space-y-4">
            {!editing && (
              <div>
                <span className="mb-1 block text-[12px]" style={{ color: PAPER.muted }}>
                  渠道类型
                </span>
                <div className="flex flex-wrap gap-2">
                  {CREATABLE_TYPES.map((type) => (
                    <button
                      key={type}
                      type="button"
                      onClick={() => setChannelType(type)}
                      className="rounded-[6px] px-3 py-1.5 text-[13px] transition-colors"
                      style={{
                        background: channelType === type ? PAPER.alt : PAPER.card,
                        border: `1px solid ${channelType === type ? PAPER.primary : PAPER.hair}`,
                        color: channelType === type ? PAPER.ink : PAPER.muted,
                      }}
                    >
                      {CHANNEL_LABELS[type].name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <Field
              label="渠道名称"
              value={form.name}
              onChange={(v) => setForm({ ...form, name: v })}
              placeholder="如：企业微信智能机器人"
            />
            <Field
              label="备注"
              value={form.description}
              onChange={(v) => setForm({ ...form, description: v })}
              placeholder="用途说明（选填）"
            />

            <div className="border-t pt-4" style={{ borderColor: PAPER.hair }}>
              <p className="mb-3 text-[12px]" style={{ color: PAPER.muted }}>
                {channelType === 'feishu_app' ? '飞书应用凭据' : '企业微信应用凭据'}
              </p>
              {channelType === 'feishu_app' ? (
                <div className="space-y-3">
                  <Field label="App ID" mono value={form.feishuAppId} onChange={(v) => setForm({ ...form, feishuAppId: v })} />
                  <Field label="App Secret" type="password" mono value={form.feishuAppSecret} onChange={(v) => setForm({ ...form, feishuAppSecret: v })} />
                  <Field label="Verification Token" mono value={form.feishuVerificationToken} onChange={(v) => setForm({ ...form, feishuVerificationToken: v })} />
                  <Field label="Encrypt Key" mono value={form.feishuEncryptKey} onChange={(v) => setForm({ ...form, feishuEncryptKey: v })} />
                </div>
              ) : (
                <div className="space-y-3">
                  <Field label="Corp ID" mono value={form.wecomCorpId} onChange={(v) => setForm({ ...form, wecomCorpId: v })} />
                  <Field label="Corp Secret" type="password" mono value={form.wecomCorpSecret} onChange={(v) => setForm({ ...form, wecomCorpSecret: v })} />
                  <Field label="Agent ID" mono value={form.wecomAgentId} onChange={(v) => setForm({ ...form, wecomAgentId: v })} />
                  <Field label="回调 Token" mono value={form.wecomToken} onChange={(v) => setForm({ ...form, wecomToken: v })} />
                  <Field label="Encoding AES Key" mono value={form.wecomAesKey} onChange={(v) => setForm({ ...form, wecomAesKey: v })} />
                </div>
              )}
              <p className="mt-3 text-[12px]" style={{ color: PAPER.subtle }}>
                凭据对称加密落库，接口不回传明文；留空表示沿用已保存的凭据。
              </p>
            </div>

            <div className="border-t pt-4" style={{ borderColor: PAPER.hair }}>
              <span className="mb-2 block text-[12px]" style={{ color: PAPER.muted }}>
                挂载数字员工（{mountedProfiles.length}）
              </span>
              <div className="max-h-48 space-y-1 overflow-y-auto">
                {profiles.length === 0 ? (
                  <p className="text-[13px]" style={{ color: PAPER.subtle }}>
                    暂无数字员工档案，请先在「员工」页完成编制。
                  </p>
                ) : (
                  profiles.map((p) => (
                    <label
                      key={p.id}
                      className="flex cursor-pointer items-center gap-2.5 rounded-[6px] px-2 py-1.5 text-[13px] hover:bg-[#F4F4F3]"
                    >
                      <input
                        type="checkbox"
                        checked={mountedProfiles.includes(p.id)}
                        onChange={(e) =>
                          setMountedProfiles(
                            e.target.checked
                              ? [...mountedProfiles, p.id]
                              : mountedProfiles.filter((id) => id !== p.id),
                          )
                        }
                      />
                      <span
                        className="flex h-6 w-6 items-center justify-center rounded-full border text-[12px]"
                        style={{ borderColor: PAPER.hair, color: PAPER.muted }}
                      >
                        {p.display_name.slice(0, 1)}
                      </span>
                      <span style={{ color: PAPER.ink }}>{p.display_name}</span>
                      <span className="font-mono text-[12px]" style={{ color: PAPER.subtle }}>
                        {p.employee_badge}
                      </span>
                    </label>
                  ))
                )}
              </div>
              <div className="mt-3">
                <span className="mb-1 block text-[12px]" style={{ color: PAPER.muted }}>
                  默认承接员工
                </span>
                <select
                  value={defaultProfile}
                  onChange={(e) => setDefaultProfile(e.target.value)}
                  className="w-full rounded-[6px] px-3 py-2 text-[14px] outline-none"
                  style={{ background: PAPER.card, border: `1px solid ${PAPER.hair}`, color: PAPER.ink }}
                >
                  <option value="">按意图自动分发</option>
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name}（{p.employee_badge}）
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div
              className="flex items-center justify-between gap-3 border-t pt-4"
              style={{ borderColor: PAPER.hair }}
            >
              {editing ? (
                <TextLink onClick={() => handleDeactivate(editing)}>注销该渠道</TextLink>
              ) : (
                <span />
              )}
              <div className="flex gap-2">
                <SecondaryButton onClick={() => setFormOpen(false)}>取消</SecondaryButton>
                <PrimaryButton onClick={handleSaveAccount} disabled={saving}>
                  {saving ? '保存中…' : '保存'}
                </PrimaryButton>
              </div>
            </div>
          </div>
        </SideDrawer>
      )}

      {/* ============================================================ */}
      {/* 抽屉：Webhook 回调测试                                        */}
      {/* ============================================================ */}
      {testOpen && (
        <SideDrawer
          title="Webhook 回调测试"
          onClose={() => setTestOpen(false)}
          description="不连通真实企微 / 飞书服务器，直接验证网关的入站幂等、身份映射与意图分发全链路。"
        >
          <div className="space-y-4">
            <div className="rounded-[6px] p-3" style={{ background: PAPER.alt, border: `1px solid ${PAPER.hair}` }}>
              <p className="text-[12px]" style={{ color: PAPER.muted }}>
                回调地址
              </p>
              <p className="mt-1 break-all font-mono text-[12px]" style={{ color: PAPER.ink }}>
                POST {testAccount ? webhookPath(testAccount) : '—'}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="mb-1 block text-[12px]" style={{ color: PAPER.muted }}>
                  渠道账号
                </span>
                <select
                  value={testAccountId}
                  onChange={(e) => setTestAccountId(e.target.value)}
                  className="w-full rounded-[6px] px-3 py-2 text-[14px] outline-none"
                  style={{ background: PAPER.card, border: `1px solid ${PAPER.hair}`, color: PAPER.ink }}
                >
                  {accounts.length === 0 && <option value="">请先接入渠道</option>}
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </label>
              <Field label="外部用户 ID" mono value={testUserId} onChange={setTestUserId} />
              <Field label="外部用户名称" value={testUserName} onChange={setTestUserName} />
              <Field
                label="消息 ID（防重放键）"
                mono
                value={testMessageId}
                onChange={setTestMessageId}
                hint="留空自动生成；重放时复用同一 ID"
              />
            </div>

            <Field
              label="消息内容"
              value={testContent}
              onChange={setTestContent}
              placeholder="如：/当前 或 直接提问"
            />

            <div className="flex flex-wrap gap-2">
              <PrimaryButton onClick={() => sendTestMessage(false)} disabled={testing}>
                发送回调
              </PrimaryButton>
              <SecondaryButton
                onClick={() => sendTestMessage(true)}
                disabled={testing || !testMessageId.trim()}
                title={testMessageId.trim() ? undefined : '请先发送一次以生成消息 ID'}
              >
                <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                重放同一消息
              </SecondaryButton>
            </div>

            <div className="border-t pt-4" style={{ borderColor: PAPER.hair }}>
              <SectionTitle title="网关回执" meta={`${observations.length} OBSERVATIONS`} />
              {observations.length === 0 ? (
                <p className="mt-3 text-[13px]" style={{ color: PAPER.muted }}>
                  尚未发送。发送后此处按行展示真实网关返回的指令应答、承接员工与 SOP 保护窗判定。
                </p>
              ) : (
                <div className="mt-3 divide-y" style={{ borderColor: PAPER.hair }}>
                  {observations.map((o) => {
                    const blocked = isReplay(o)
                    const rows: Array<[string, string]> = [
                      [
                        '投递序号',
                        `#${o.seq} · ${new Date(o.at).toLocaleTimeString('zh-CN', { hour12: false })}`,
                      ],
                      ['消息 ID', o.messageId],
                      ['幂等去重', blocked ? '命中缓存，忽略重复消息' : '首次放行'],
                      ['指令应答', o.result.command_response || '—'],
                      [
                        '承接员工',
                        o.result.dispatched_profile_name
                          ? `${o.result.dispatched_profile_name}（${o.result.dispatched_profile_badge || '无工号'}）`
                          : '—',
                      ],
                      [
                        'SOP 保护窗',
                        o.result.is_sop_protected
                          ? `锁定中 · 规程 ${o.result.active_flow_id || '未命名'}`
                          : '未锁定',
                      ],
                      ['继续执行规程', o.result.should_execute_flow ? '是' : '否'],
                    ]
                    return (
                      <div key={o.seq} className="py-3">
                        <div className="mb-2 flex items-center justify-between">
                          <span className="font-mono text-[12px]" style={{ color: PAPER.subtle }}>
                            {o.accountName}
                          </span>
                          <StatusLabel tone={blocked ? 'warning' : 'success'}>
                            {blocked ? '重放已拦截' : '已分发'}
                          </StatusLabel>
                        </div>
                        <dl className="space-y-1">
                          {rows.map(([k, v]) => (
                            <div key={k} className="flex gap-3 text-[12px] leading-5">
                              <dt className="w-28 shrink-0" style={{ color: PAPER.subtle }}>
                                {k}
                              </dt>
                              <dd className="min-w-0 flex-1 break-words" style={{ color: PAPER.ink }}>
                                {v}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </SideDrawer>
      )}
    </Layout>
  )
}

/** 右侧抽屉：遮罩 + 12px 圆角卡面，浮层阴影 0 12px 32px rgba(0,0,0,.10)。 */
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
