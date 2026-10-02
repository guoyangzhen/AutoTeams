import { useState, useEffect, useCallback, useRef } from 'react'
import Layout from '@/components/Layout'
import { RoleGuard } from '@/components/RoleGuard'
import { useAuth } from '@/hooks/useAuth'
import { useConfirmDialog } from '@/hooks/useConfirmDialog'
import apiClient from '@/api/client'
import {
  listMembers,
  removeMember,
  changeMemberRole,
  listInvitations,
  cancelInvitation,
  type Member,
  type Invitation,
} from '@/api/enterprises'
import { Spinner } from '@/components/ui/Spinner'
import { Button } from '@/components/ui/Button'
import { PageHeader } from '@/components/ui/PageHeader'
import { setAutonomyMode as persistAutonomyMode } from '@/hooks/useAutonomyMode'
import { Enterprise, ApiResponse } from '@/types'
import {
  getLLMConfig,
  upsertLLMConfig,
  type LLMConfigView,
} from '@/api/llmConfig'
import {
  listAgentApiCredentials,
  createAgentApiCredential,
  revokeAgentApiCredential,
  AGENT_API_SCOPES,
  type AgentApiCredential,
  type CreatedAgentApiCredential,
} from '@/api/agentApiKeys'
import { getAgents } from '@/api/agents'
import {
  Building2,
  Users,
  User as UserIcon,
  SlidersHorizontal,
  Mail,
  Lock,
  Copy,
  Trash2,
  Info,
  Check,
  ChevronRight,
  Bot,
  Cpu,
  ShieldCheck,
  KeyRound,
  Route,
} from 'lucide-react'

/* ------------------------------------------------------------------ */
/* 本地类型                                                            */
/* ------------------------------------------------------------------ */
// 复用后端 InvitationResponse 类型；前端 InviteMember 旧字段移除（role 由后端默认 member）
// 注：旧 InviteMember 接口已被 enterprises.ts 中的 Invitation 类型取代

type SectionId =
  | 'account'
  | 'enterprise'
  | 'members'
  | 'preferences'
  | 'autonomy'
  | 'llm'
  | 'modelRouting'
  | 'agentKeys'

/* ---- 渐进式自主模式（PRD §2.2 / UI 方案 §2.1） ---- */
type AutonomyMode = 'boss' | 'conversation' | 'file' | 'own'

const AUTONOMY_MODES: ReadonlyArray<{ id: AutonomyMode; label: string; desc: string }> = [
  { id: 'boss', label: '老板模式', desc: '一键部署 AI 数字员工，最小摩擦。适合企业老板 / 决策者。' },
  { id: 'conversation', label: '对话模式', desc: '通过对话调整或增加 AI 员工技能、更改业务流程。适合业务人员。' },
  { id: 'file', label: '文件模式', desc: '直接修改文件来调整 AI 员工配置。适合业务人员。' },
  { id: 'own', label: '自有模式', desc: '用代码等方式深度调整 AI 员工和运行模型。适合高级 / 懂行用户。' },
]

const AUTONOMY_STORAGE_KEY = 'autoteams_autonomy_mode'

// Toast 自动消失时长（毫秒）
const TOAST_DISMISS_MS = 3000
// 邮件类操作反馈时长（稍长，等用户看完提示）
const TOAST_DISMISS_LONG_MS = 4000
// 极短反馈（如复制邀请链接成功）
const TOAST_DISMISS_SHORT_MS = 2000

const SECTIONS: ReadonlyArray<{ id: SectionId; label: string; icon: typeof Building2 }> = [
  { id: 'account', label: '账户信息', icon: UserIcon },
  { id: 'enterprise', label: '企业管理', icon: Building2 },
  { id: 'members', label: '成员管理', icon: Users },
  { id: 'preferences', label: '系统偏好', icon: SlidersHorizontal },
  { id: 'autonomy', label: '自主模式', icon: Bot },
  { id: 'llm', label: '模型 API 配置', icon: Cpu },
  { id: 'modelRouting', label: '模型路由与 BYOK', icon: Route },
  { id: 'agentKeys', label: '机器凭证', icon: KeyRound },
]

/* ------------------------------------------------------------------ */
/* AI 模型路由目录（重构方案 §决策四）                                  */
/*                                                                    */
/* 路由矩阵与 provider 清单镜像自后端：                                  */
/*   - app/services/ai/registry.py ROUTING_MATRIX / Provider           */
/*   - app/config.py DEEPSEEK_* / MOONSHOT_* / ZHIPU_*                  */
/* 后端目前尚未提供「按任务类型持久化路由」与「多 provider BYOK 存储」    */
/* 的接口，因此本卡片只做两件能真正生效的事：                           */
/*   1. 展示服务端实际使用的路由矩阵（只读，不假装可编辑）；             */
/*   2. 把选定 provider 的 OpenAI 兼容参数写入下方「模型 API 配置」槽位， */
/*      由既有 PUT /llm-config 落库 —— DeepSeek / Kimi / GLM 均为        */
/*      OpenAI 兼容端点，这条链路是当前唯一真实可用的 BYOK 通道。         */
/* ------------------------------------------------------------------ */

interface ModelProvider {
  id: 'deepseek' | 'moonshot' | 'zhipu'
  name: string
  vendor: string
  role: string
  /** OpenAI 兼容 Base URL，对应后端 settings 里的 *_API_BASE */
  apiBase: string
  /** 对应后端 settings 里的 *_MODEL */
  defaultModel: string
  scenarios: string
}

const MODEL_PROVIDERS: ReadonlyArray<ModelProvider> = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    vendor: 'DeepSeek',
    role: '主力模型',
    apiBase: 'https://api.deepseek.com',
    defaultModel: 'deepseek-chat',
    scenarios: '日常对话 / 客服、复杂推理分析、文档总结摘要',
  },
  {
    id: 'moonshot',
    name: 'Kimi',
    vendor: 'Moonshot',
    role: '备选模型',
    apiBase: 'https://api.moonshot.cn/v1',
    defaultModel: 'moonshot-v1-128k',
    scenarios: '超长上下文与精细文档研读，文档总结场景首选备选',
  },
  {
    id: 'zhipu',
    name: 'GLM-4',
    vendor: '智谱 AI',
    role: '备选模型',
    apiBase: 'https://open.bigmodel.cn/api/paas/v4',
    defaultModel: 'glm-4-plus',
    scenarios: '复杂业务逻辑与 Function Call，复杂推理场景备选',
  },
]

/** 任务类型 → 按序尝试的 provider 链（镜像后端 ROUTING_MATRIX） */
const ROUTING_MATRIX: ReadonlyArray<{ task: string; chain: string[] }> = [
  { task: '日常对话 / 客服', chain: ['DeepSeek', 'Kimi'] },
  { task: '复杂推理 / 分析', chain: ['DeepSeek', 'GLM-4'] },
  { task: '代码相关', chain: ['外接 Codex / Claude', 'DeepSeek'] },
  { task: '文档总结 / 摘要', chain: ['DeepSeek', 'Kimi'] },
  { task: '向量化 Embedding', chain: ['DeepSeek'] },
]

/* ------------------------------------------------------------------ */
/* 工具函数                                                            */
/* ------------------------------------------------------------------ */
function formatDate(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

function statusDotClass(status: string): string {
  if (status === 'accepted' || status === 'active') return 'dot dot-success'
  if (status === 'pending') return 'dot dot-warning'
  if (status === 'expired' || status === 'revoked' || status === 'cancelled') return 'dot dot-muted'
  return 'dot dot-muted'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    accepted: '已接受',
    pending: '待接受',
    expired: '已过期',
    cancelled: '已取消',
    active: '使用中',
    revoked: '已吊销',
  }
  return map[status] ?? status
}

/* ------------------------------------------------------------------ */
/* 主组件                                                              */
/* ------------------------------------------------------------------ */
export default function Settings() {
  const { user, updateUser } = useAuth()
  const confirmDialog = useConfirmDialog()

  /* ---- 既有状态 (保留) ---- */
  const [enterprise, setEnterprise] = useState<Enterprise | null>(null)
  const [enterpriseName, setEnterpriseName] = useState('')
  const [inviteLink, setInviteLink] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  /* ---- 新增: 账户信息 ---- */
  const [profileName, setProfileName] = useState(user?.name ?? '')
  const [savingProfile, setSavingProfile] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [savingPassword, setSavingPassword] = useState(false)

  /* ---- 新增: 成员邀请 ---- */
  const [invites, setInvites] = useState<Invitation[]>([])
  const [members, setMembers] = useState<Member[]>([])
  const [membersLoading, setMembersLoading] = useState(false)
  const [newInviteEmail, setNewInviteEmail] = useState('')
  const [newInviteRole, setNewInviteRole] = useState<'admin' | 'member'>('member')
  const [sendingInvite, setSendingInvite] = useState(false)
  const [cancellingInviteId, setCancellingInviteId] = useState<string | null>(null)

  /* ---- 新增: 系统偏好 ---- */
  const [notifEmail, setNotifEmail] = useState(true)
  const [notifPush, setNotifPush] = useState(false)
  const [notifDigest, setNotifDigest] = useState(true)

  /* ---- 新增: 渐进式自主模式（默认对话模式，持久化到 localStorage） ---- */
  const [autonomyMode, setAutonomyMode] = useState<AutonomyMode>(() => {
    try {
      const raw = localStorage.getItem(AUTONOMY_STORAGE_KEY)
      if (raw && AUTONOMY_MODES.some((m) => m.id === raw)) return raw as AutonomyMode
    } catch { /* ignore */ }
    return 'conversation'
  })

  /* ---- 新增: 模型 API 配置（企业级，密钥仅掩码展示） ---- */
  const [llmConfig, setLlmConfig] = useState<LLMConfigView | null>(null)
  const [llmLoading, setLlmLoading] = useState(false)
  const [savingLLM, setSavingLLM] = useState(false)
  const [openaiEnabled, setOpenaiEnabled] = useState(false)
  const [openaiBase, setOpenaiBase] = useState('')
  const [openaiKey, setOpenaiKey] = useState('')
  const [openaiModel, setOpenaiModel] = useState('')
  const [anthropicEnabled, setAnthropicEnabled] = useState(false)
  const [anthropicBase, setAnthropicBase] = useState('')
  const [anthropicKey, setAnthropicKey] = useState('')
  const [anthropicModel, setAnthropicModel] = useState('')
  /** 路由卡片当前选中的 provider：点「应用到 OpenAI 兼容槽位」后写入 openai* 三个字段 */
  const [selectedProviderId, setSelectedProviderId] = useState<ModelProvider['id']>('deepseek')

  /* ---- 新增: Agent API 机器凭证（企业级，仅管理员；明文密钥只展示一次） ---- */
  const [agentCredentials, setAgentCredentials] = useState<AgentApiCredential[]>([])
  const [agentCredsLoading, setAgentCredsLoading] = useState(false)
  const [enterpriseAgents, setEnterpriseAgents] = useState<{ id: string; name: string }[]>([])
  const [credName, setCredName] = useState('')
  const [credScopes, setCredScopes] = useState<string[]>(['agent:read'])
  const [credAllowedAgentIds, setCredAllowedAgentIds] = useState<string[]>([])
  const [credExpiryDays, setCredExpiryDays] = useState('')
  const [creatingCred, setCreatingCred] = useState(false)
  const [revokingCredId, setRevokingCredId] = useState<string | null>(null)
  const [createdCredKey, setCreatedCredKey] = useState<CreatedAgentApiCredential | null>(null)

  /* ---- 左侧导航 ---- */
  const [activeSection, setActiveSection] = useState<SectionId>('account')
  const sectionRefs = useRef<Record<SectionId, HTMLElement | null>>({
    account: null,
    enterprise: null,
    members: null,
    preferences: null,
    autonomy: null,
    llm: null,
    modelRouting: null,
    agentKeys: null,
  })

  /* ================================================================ */
  /* 既有数据加载 (保留原逻辑)                                          */
  /* ================================================================ */
  const loadEnterprise = useCallback(async () => {
    if (!user?.enterprise_id) {
      setLoading(false)
      return
    }

    try {
      setLoading(true)
      const response = await apiClient.get<ApiResponse<Enterprise>>(`/enterprises/${user.enterprise_id}`)
      const enterpriseData = response.data.data
      setEnterprise(enterpriseData)
      setEnterpriseName(enterpriseData.name)
    } catch (err) {
      console.error('加载企业信息失败:', err)
    } finally {
      setLoading(false)
    }
  }, [user?.enterprise_id])

  // 加载邀请记录（修复 URL bug：原 /invites 应为 /invitations）
  const loadInvites = useCallback(async () => {
    if (!user?.enterprise_id) return
    try {
      const data = await listInvitations(user.enterprise_id)
      setInvites(data || [])
    } catch (err) {
      // 非 admin 调用会被后端 403；这里静默处理避免页面加载报错
      console.debug('邀请列表加载失败（可能非 admin）:', err)
    }
  }, [user?.enterprise_id])

  // 加载企业成员列表（激活 list_members 端点）
  const loadMembers = useCallback(async () => {
    if (!user?.enterprise_id) return
    setMembersLoading(true)
    try {
      const data = await listMembers(user.enterprise_id)
      setMembers(data || [])
    } catch (err) {
      console.debug('成员列表加载失败:', err)
    } finally {
      setMembersLoading(false)
    }
  }, [user?.enterprise_id])

  // 加载企业模型 API 配置（密钥仅掩码展示）
  const loadLLMConfig = useCallback(async () => {
    if (!user?.enterprise_id) return
    setLlmLoading(true)
    try {
      const config = await getLLMConfig(user.enterprise_id)
      setLlmConfig(config)
      setOpenaiEnabled(config?.openai_enabled ?? false)
      setOpenaiBase(config?.openai_api_base ?? '')
      setOpenaiModel(config?.openai_model ?? '')
      setAnthropicEnabled(config?.anthropic_enabled ?? false)
      setAnthropicBase(config?.anthropic_api_base ?? '')
      setAnthropicModel(config?.anthropic_model ?? '')
      // 密钥不在加载时回填：保持输入框为空，避免明文出现在 DOM
      setOpenaiKey('')
      setAnthropicKey('')
    } catch (err) {
      console.debug('模型 API 配置加载失败:', err)
    } finally {
      setLlmLoading(false)
    }
  }, [user?.enterprise_id])

  // 加载企业机器凭证列表 + 可授权 Agent 列表（仅管理员；非管理员 403 静默处理）
  const loadAgentCredentials = useCallback(async () => {
    setAgentCredsLoading(true)
    try {
      const [creds, agents] = await Promise.all([
        listAgentApiCredentials().catch(() => [] as AgentApiCredential[]),
        getAgents().catch(() => []),
      ])
      setAgentCredentials(creds)
      setEnterpriseAgents(agents.map((a) => ({ id: a.id, name: a.name })))
    } finally {
      setAgentCredsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadEnterprise()
  }, [loadEnterprise])

  useEffect(() => {
    if (enterprise) {
      loadInvites()
      loadMembers()
      loadLLMConfig()
      loadAgentCredentials()
    }
  }, [enterprise, loadInvites, loadMembers, loadLLMConfig, loadAgentCredentials])

  // 同步用户名到 profileName
  useEffect(() => {
    setProfileName(user?.name ?? '')
  }, [user?.name])

  /* ================================================================ */
  /* 既有处理函数 (保留)                                                */
  /* ================================================================ */
  const handleCreateEnterprise = async () => {
    if (!enterpriseName.trim()) {
      setError('请输入企业名称')
      return
    }

    try {
      setCreating(true)
      setError('')
      setSuccess('')

      const response = await apiClient.post<ApiResponse<Enterprise>>('/enterprises', {
        name: enterpriseName,
      })
      const newEnterprise = response.data.data
      setEnterprise(newEnterprise)
      setEnterpriseName(newEnterprise.name)

      if (user) {
        updateUser({ ...user, enterprise_id: newEnterprise.id, role: 'admin' })
      }

      setSuccess('企业创建成功')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
    } catch (err) {
      console.error('创建企业失败:', err)
      setError('创建企业失败')
    } finally {
      setCreating(false)
    }
  }

  const handleSaveEnterprise = async () => {
    if (!enterpriseName.trim() || !enterprise) {
      setError('企业名称不能为空')
      return
    }

    try {
      setSaving(true)
      setError('')
      setSuccess('')

      await apiClient.put(`/enterprises/${enterprise.id}`, { name: enterpriseName })
      setEnterprise((prev) => (prev ? { ...prev, name: enterpriseName } : null))
      setSuccess('保存成功')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
    } catch (err) {
      console.error('保存失败:', err)
      setError('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleGenerateInvite = async () => {
    if (!enterprise) return

    try {
      setGenerating(true)
      setError('')

      const response = await apiClient.post<
        ApiResponse<{
          invite_token: string
          invite_url: string
          expires_at: string
        }>
      >(`/enterprises/${enterprise.id}/invite`)

      const data = response.data.data
      // FE-SEC-03: 强制使用后端返回的完整邀请 URL，不再依赖 window.location.origin
      // 避免应用被嵌入 attacker 控制的 iframe 时生成错误域名。
      if (!data.invite_url.startsWith('http')) {
        setError('后端未配置 FRONTEND_URL，无法生成安全的完整邀请链接')
        return
      }
      setInviteLink(data.invite_url)
      setSuccess('邀请链接已生成')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
    } catch (err) {
      console.error('生成邀请链接失败:', err)
      setError('生成邀请链接失败')
    } finally {
      setGenerating(false)
    }
  }

  const handleCopyInvite = () => {
    if (inviteLink) {
      navigator.clipboard.writeText(inviteLink)
      setSuccess('邀请链接已复制')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
    }
  }

  /* ================================================================ */
  /* 新增处理函数                                                      */
  /* ================================================================ */
  const handleSaveProfile = async () => {
    if (!profileName.trim()) {
      setError('姓名不能为空')
      return
    }
    try {
      setSavingProfile(true)
      setError('')
      setSuccess('')
      await apiClient.put('/auth/me', { name: profileName })
      if (user) {
        updateUser({ ...user, name: profileName })
      }
      setSuccess('个人信息已保存')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
    } catch (err) {
      console.error('保存个人信息失败:', err)
      setError('保存个人信息失败')
    } finally {
      setSavingProfile(false)
    }
  }

  const handleChangePassword = async () => {
    if (!currentPassword || !newPassword || !confirmPassword) {
      setError('请填写所有密码字段')
      return
    }
    if (newPassword.length < 8) {
      setError('新密码至少 8 位')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致')
      return
    }
    try {
      setSavingPassword(true)
      setError('')
      setSuccess('')
      await apiClient.post('/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setSuccess('密码已更新')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
    } catch (err) {
      console.error('修改密码失败:', err)
      setError('修改密码失败，请检查当前密码是否正确')
    } finally {
      setSavingPassword(false)
    }
  }

  const handleAutonomyChange = (mode: AutonomyMode) => {
    setAutonomyMode(mode)
    try {
      localStorage.setItem(AUTONOMY_STORAGE_KEY, mode)
    } catch { /* ignore */ }
    persistAutonomyMode(mode)
    const label = AUTONOMY_MODES.find((m) => m.id === mode)?.label || ''
    setSuccess(`已切换至「${label}」`)
    setTimeout(() => setSuccess(''), TOAST_DISMISS_SHORT_MS)
  }

  // 保存模型 API 配置（密钥仅在新输入时提交，不能同时启用两个 provider）
  const handleSaveLLMConfig = async () => {
    if (!enterprise) {
      setError('请先加入企业')
      return
    }
    if (openaiEnabled && anthropicEnabled) {
      setError('OpenAI 兼容与 Anthropic 不能同时启用，请选择其一')
      return
    }
    if (!openaiEnabled && !anthropicEnabled) {
      setError('请至少启用一个模型提供商')
      return
    }
    if (openaiEnabled && !openaiModel.trim()) {
      setError('请填写 OpenAI 兼容 API 的默认模型名')
      return
    }
    if (anthropicEnabled && !anthropicModel.trim()) {
      setError('请填写 Anthropic API 的默认模型名')
      return
    }
    try {
      setSavingLLM(true)
      setError('')
      setSuccess('')
      const saved = await upsertLLMConfig(enterprise.id, {
        openai_enabled: openaiEnabled,
        openai_api_base: openaiBase.trim() || undefined,
        // 仅当用户输入了新密钥才提交，避免覆盖已保存密钥
        openai_api_key: openaiKey.trim() || undefined,
        openai_model: openaiModel.trim() || undefined,
        anthropic_enabled: anthropicEnabled,
        anthropic_api_base: anthropicBase.trim() || undefined,
        anthropic_api_key: anthropicKey.trim() || undefined,
        anthropic_model: anthropicModel.trim() || undefined,
      })
      setLlmConfig(saved)
      setOpenaiKey('')
      setAnthropicKey('')
      setSuccess('模型 API 配置已保存并立即生效')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_LONG_MS)
    } catch (err) {
      console.error('保存模型 API 配置失败:', err)
      setError('保存模型 API 配置失败')
    } finally {
      setSavingLLM(false)
    }
  }

  /**
   * 把选中的 provider 参数写入「OpenAI 兼容 API」槽位。
   * DeepSeek / Kimi / GLM-4 均提供 OpenAI 兼容端点，这是当前后端
   * PUT /llm-config 唯一能持久化的 BYOK 通道；写完不自动保存，
   * 仍由用户在下方「保存配置」确认，避免误改企业级凭证。
   */
  const applyProviderToOpenAISlot = (provider: ModelProvider) => {
    setSelectedProviderId(provider.id)
    setOpenaiEnabled(true)
    setAnthropicEnabled(false)
    setOpenaiBase(provider.apiBase)
    setOpenaiModel(provider.defaultModel)
    setSuccess(`已填入 ${provider.name} 的接入参数，填写密钥后点「保存配置」即可生效`)
    setTimeout(() => setSuccess(''), TOAST_DISMISS_LONG_MS)
  }

  /* ---- Agent API 机器凭证：创建 / 撤销 / 一次性密钥展示 ---- */
  const toggleCredScope = (scope: string) => {
    setCredScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    )
  }

  const toggleCredAgent = (agentId: string) => {
    setCredAllowedAgentIds((prev) =>
      prev.includes(agentId)
        ? prev.filter((id) => id !== agentId)
        : [...prev, agentId]
    )
  }

  const handleCreateCredential = async () => {
    if (!credName.trim()) {
      setError('请输入凭证名称')
      return
    }
    if (credScopes.length === 0) {
      setError('请至少选择一个权限范围')
      return
    }

    try {
      setCreatingCred(true)
      setError('')
      setSuccess('')
      const expiresAt = credExpiryDays
        ? new Date(Date.now() + Number(credExpiryDays) * 24 * 60 * 60 * 1000).toISOString()
        : null
      const created = await createAgentApiCredential({
        name: credName.trim(),
        scopes: credScopes,
        allowed_agent_ids: credAllowedAgentIds,
        expires_at: expiresAt,
      })
      // 明文密钥只在这一次响应中，展示后不可再取回
      setCreatedCredKey(created)
      setCredName('')
      setCredScopes(['agent:read'])
      setCredAllowedAgentIds([])
      setCredExpiryDays('')
      setSuccess('机器凭证已创建，请立即保存密钥')
      await loadAgentCredentials()
    } catch (err) {
      console.error('创建机器凭证失败:', err)
      setError('创建机器凭证失败')
    } finally {
      setCreatingCred(false)
    }
  }

  const handleRevokeCredential = async (credential: AgentApiCredential) => {
    const ok = await confirmDialog.ask({
      title: '撤销机器凭证',
      description: `确定要撤销凭证 "${credential.name}"（${credential.key_prefix}…）吗？使用该密钥的外部 Agent 将立即失去访问能力，此操作不可恢复。`,
      confirmText: '撤销',
    })
    if (!ok) return

    try {
      setRevokingCredId(credential.id)
      await revokeAgentApiCredential(credential.id)
      setSuccess('机器凭证已撤销')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
      await loadAgentCredentials()
    } catch (err) {
      console.error('撤销机器凭证失败:', err)
      setError('撤销机器凭证失败')
    } finally {
      setRevokingCredId(null)
    }
  }

  const handleCopyApiKey = async () => {
    if (!createdCredKey) return
    try {
      await navigator.clipboard.writeText(createdCredKey.api_key)
      setSuccess('密钥已复制到剪贴板')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_SHORT_MS)
    } catch {
      setError('复制失败，请手动选择并复制密钥')
    }
  }

  const handleCloseCreatedKey = () => {
    // 关闭后明文密钥不再可见，也不应残留在组件状态中
    setCreatedCredKey(null)
  }

  const handleSendInvite = async () => {
    if (!newInviteEmail.trim() || !enterprise) {
      setError('请输入被邀请人邮箱')
      return
    }
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
    if (!emailRegex.test(newInviteEmail)) {
      setError('邮箱格式不正确')
      return
    }
    try {
      setSendingInvite(true)
      setError('')
      setSuccess('')
      // 后端 /invite 端点接收 query params（max_uses, email），返回 invite_url。
      // role 不在邀请阶段决定，可在邀请被接受后通过"成员管理"面板的"变更角色"调整。
      await apiClient.post<ApiResponse<{ invite_url: string; invitation_id: string }>>(
        `/enterprises/${enterprise.id}/invite`,
        null,
        { params: { email: newInviteEmail, max_uses: 1 } },
      )
      // 刷新邀请列表（新邀请会出现在列表顶部）
      await loadInvites()
      setNewInviteEmail('')
      setNewInviteRole('member')
      setSuccess('邀请已创建，可通过邀请链接发送给对方')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_LONG_MS)
    } catch (err) {
      console.error('发送邀请失败:', err)
      setError('发送邀请失败')
    } finally {
      setSendingInvite(false)
    }
  }

  // 移除企业成员（激活 DELETE /enterprises/{id}/members/{user_id}）
  const handleRemoveMember = async (member: Member) => {
    if (!enterprise) return
    const ok = await confirmDialog.ask({
      title: '移除成员',
      description: `确定要移除成员 "${member.name || member.email}" 吗？此操作会断开其与企业关联并禁用账号。`,
      confirmText: '移除',
      cancelText: '取消',
      variant: 'danger',
    })
    if (!ok) return
    try {
      setError('')
      setSuccess('')
      await removeMember(enterprise.id, member.id)
      setSuccess(`成员 ${member.name || member.email} 已移除`)
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
      await loadMembers()
    } catch (err) {
      console.error('移除成员失败:', err)
      setError('移除成员失败（不能移除自己）')
    }
  }

  // 变更成员角色（激活 PUT /enterprises/{id}/members/{user_id}/role）
  const handleChangeRole = async (member: Member, newRole: 'admin' | 'member') => {
    if (!enterprise) return
    if (member.id === user?.id) {
      setError('不能修改自己的角色')
      return
    }
    try {
      setError('')
      setSuccess('')
      await changeMemberRole(enterprise.id, member.id, newRole)
      setSuccess(`${member.name || member.email} 角色已变更为${newRole === 'admin' ? '管理员' : '成员'}`)
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
      await loadMembers()
    } catch (err) {
      console.error('变更角色失败:', err)
      setError('变更角色失败')
    }
  }

  // 取消邀请（激活 POST /enterprises/{id}/invitations/{iid}/cancel）
  const handleCancelInvite = async (invitation: Invitation) => {
    if (!enterprise) return
    const ok = await confirmDialog.ask({
      title: '取消邀请',
      description: `确定要取消邀请 "${invitation.email || invitation.token.slice(0, 8) + '...'}" 吗？`,
      confirmText: '取消邀请',
      cancelText: '保留',
      variant: 'danger',
    })
    if (!ok) return
    try {
      setCancellingInviteId(invitation.id)
      setError('')
      setSuccess('')
      await cancelInvitation(enterprise.id, invitation.id)
      setSuccess('邀请已取消')
      setTimeout(() => setSuccess(''), TOAST_DISMISS_MS)
      await loadInvites()
    } catch (err) {
      console.error('取消邀请失败:', err)
      setError('取消邀请失败（仅 pending 状态可取消）')
    } finally {
      setCancellingInviteId(null)
    }
  }

  /* ================================================================ */
  /* 导航点击 - 平滑滚动到对应区块                                      */
  /* ================================================================ */
  const handleNavClick = (id: SectionId) => {
    setActiveSection(id)
    const el = sectionRefs.current[id]
    if (el) {
      const top = el.getBoundingClientRect().top + window.scrollY - 96 // 顶部品牌栏 64 + 间距
      window.scrollTo({ top, behavior: 'smooth' })
    }
  }

  /* ================================================================ */
  /* 渲染                                                              */
  /* ================================================================ */
  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <Spinner size="lg" className="text-brand-500" />
        </div>
      </Layout>
    )
  }

  const roleName = user?.role === 'admin' ? '管理员' : '成员'
  const initial = user?.name?.charAt(0)?.toUpperCase() || 'U'

  return (
    <Layout>
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {/* ===== 页面头部 ===== */}
        <PageHeader
          title="设置"
          subtitle="管理企业信息、成员与安全配置"
        />

        {/* ===== 全局提示 ===== */}
        {error && (
          <div className="mb-4 p-3 bg-error/5 border border-error/20 rounded-lg text-error text-sm flex items-start gap-2">
            <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}
        {success && (
          <div className="mb-4 p-3 bg-success/5 border border-success/20 rounded-lg text-success text-sm flex items-start gap-2">
            <Check className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{success}</span>
          </div>
        )}

        {/* ===== 双栏布局 ===== */}
        <div className="grid grid-cols-1 lg:grid-cols-[200px_1fr] gap-8">
          {/* ----- 左侧设置导航 ----- */}
          <aside className="lg:sticky lg:top-24 lg:self-start">
            <nav className="bg-surface border border-border-default rounded-xl p-3 shadow-soft">
              <div className="text-xs font-semibold text-text-tertiary mb-2 px-3 uppercase tracking-wider">
                设置
              </div>
              <ul className="flex flex-col gap-1">
                {SECTIONS.map((item) => {
                  const Icon = item.icon
                  const active = activeSection === item.id
                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        onClick={() => handleNavClick(item.id)}
                        className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                          active
                            ? 'bg-[var(--brand-soft)] text-brand-500 font-medium'
                            : 'text-text-secondary hover:text-brand-500 hover:bg-elevated'
                        }`}
                      >
                        <Icon className="w-4 h-4 flex-shrink-0" />
                        <span className="flex-1 text-left">{item.label}</span>
                        {active && <ChevronRight className="w-3.5 h-3.5" />}
                      </button>
                    </li>
                  )
                })}
              </ul>
            </nav>
          </aside>

          {/* ----- 右侧内容区 ----- */}
          <div className="space-y-8 min-w-0">
            {/* ============ 账户信息 ============ */}
            <section
              ref={(el) => { sectionRefs.current.account = el }}
              className="bg-surface border border-border-default rounded-xl shadow-soft p-6 scroll-mt-24"
            >
              <div className="space-y-2 mb-5">
                <h2 className="font-serif-display text-lg font-semibold text-text-primary">账户信息</h2>
                <div className="brand-rule" />
              </div>

              {/* 用户头像 + 姓名 + 邮箱 */}
              <div className="flex items-center gap-4 mb-6">
                <div className="w-14 h-14 rounded-full bg-brand-500 text-white flex items-center justify-center font-semibold text-xl flex-shrink-0 ring-4 ring-brand-500/10">
                  {initial}
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-text-primary">{user?.name || '用户'}</span>
                    <span className="bg-brand-50 text-brand-500 rounded px-2 py-0.5 text-xs font-medium">
                      {roleName}
                    </span>
                  </div>
                  <div className="text-sm text-text-tertiary flex items-center gap-1.5 mt-0.5">
                    <Mail className="w-3.5 h-3.5" />
                    <span className="truncate">{user?.email}</span>
                  </div>
                </div>
              </div>

              {/* 可编辑姓名 */}
              <div className="space-y-4">
                <div>
                  <label htmlFor="profile-name" className="block text-sm font-medium text-text-primary mb-1.5">
                    姓名
                  </label>
                  <div className="flex gap-2">
                    <input
                      id="profile-name"
                      type="text"
                      value={profileName}
                      onChange={(e) => setProfileName(e.target.value)}
                      placeholder="请输入姓名"
                      className="settings-input flex-1 bg-surface-2 border border-border-default rounded-md px-4 py-2.5 text-sm text-text-primary placeholder-text-tertiary"
                    />
                    <Button onClick={handleSaveProfile} disabled={savingProfile}>
                      {savingProfile ? '保存中…' : '保存'}
                    </Button>
                  </div>
                </div>

                {/* 修改密码 */}
                <div className="pt-4 border-t border-border-subtle">
                  <div className="flex items-center gap-2 mb-3">
                    <Lock className="w-4 h-4 text-text-secondary" />
                    <h3 className="text-sm font-medium text-text-primary">修改密码</h3>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div>
                      <label htmlFor="cur-pwd" className="block text-xs text-text-tertiary mb-1">
                        当前密码
                      </label>
                      <input
                        id="cur-pwd"
                        type="password"
                        value={currentPassword}
                        onChange={(e) => setCurrentPassword(e.target.value)}
                        placeholder="••••••••"
                        className="settings-input w-full bg-surface-2 border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
                      />
                    </div>
                    <div>
                      <label htmlFor="new-pwd" className="block text-xs text-text-tertiary mb-1">
                        新密码
                      </label>
                      <input
                        id="new-pwd"
                        type="password"
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        placeholder="至少 8 位"
                        className="settings-input w-full bg-surface-2 border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
                      />
                    </div>
                    <div>
                      <label htmlFor="confirm-pwd" className="block text-xs text-text-tertiary mb-1">
                        确认新密码
                      </label>
                      <input
                        id="confirm-pwd"
                        type="password"
                        value={confirmPassword}
                        onChange={(e) => setConfirmPassword(e.target.value)}
                        placeholder="再次输入"
                        className="settings-input w-full bg-surface-2 border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
                      />
                    </div>
                  </div>
                  <div className="mt-3">
                    <Button variant="primary" onClick={handleChangePassword} disabled={savingPassword}>
                      {savingPassword ? '更新中…' : '更新密码'}
                    </Button>
                  </div>
                </div>
              </div>
            </section>

            {/* ============ 企业管理 ============ */}
            <section
              ref={(el) => { sectionRefs.current.enterprise = el }}
              className="bg-surface border border-border-default rounded-xl shadow-soft p-6 scroll-mt-24"
            >
              <div className="space-y-2 mb-5">
                <h2 className="font-serif-display text-lg font-semibold text-text-primary">企业管理</h2>
                <div className="brand-rule" />
              </div>

              {!enterprise && !user?.enterprise_id ? (
                /* 未加入企业 - 创建表单 */
                <div className="space-y-4">
                  <p className="text-sm text-text-secondary">您还没有加入任何企业，请创建一个新企业。</p>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={enterpriseName}
                      onChange={(e) => setEnterpriseName(e.target.value)}
                      placeholder="请输入企业名称"
                      className="settings-input flex-1 bg-surface-2 border border-border-default rounded-md px-4 py-2.5 text-sm text-text-primary placeholder-text-tertiary"
                    />
                    <Button onClick={handleCreateEnterprise} disabled={creating}>
                      {creating ? '创建中…' : '创建企业'}
                    </Button>
                  </div>
                </div>
              ) : (
                /* 已加入企业 - 展示企业信息 */
                <div className="space-y-4">
                  <div>
                    <label htmlFor="enterprise-name" className="block text-sm font-medium text-text-primary mb-1.5">
                      企业名称
                    </label>
                    <div className="flex gap-2">
                      <input
                        id="enterprise-name"
                        type="text"
                        value={enterpriseName}
                        onChange={(e) => setEnterpriseName(e.target.value)}
                        placeholder="请输入企业名称"
                        className="settings-input flex-1 bg-surface-2 border border-border-default rounded-md px-4 py-2.5 text-sm text-text-primary placeholder-text-tertiary"
                      />
                      <Button onClick={handleSaveEnterprise} disabled={saving}>
                        {saving ? '保存中…' : '保存'}
                      </Button>
                    </div>
                  </div>

                  <div>
                    <label htmlFor="enterprise-id" className="block text-sm font-medium text-text-primary mb-1.5">
                      企业 ID
                    </label>
                    <input
                      id="enterprise-id"
                      type="text"
                      value={enterprise?.id || ''}
                      disabled
                      className="settings-input w-full bg-elevated border border-border-default rounded-md px-4 py-2.5 text-sm font-mono text-text-tertiary"
                    />
                  </div>

                  {/* 企业元信息 */}
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 pt-4 border-t border-border-subtle">
                    <div>
                      <div className="text-xs text-text-tertiary mb-1">您的角色</div>
                      <div className="text-sm font-medium text-text-primary">{roleName}</div>
                    </div>
                    <div>
                      <div className="text-xs text-text-tertiary mb-1">创建时间</div>
                      <div className="text-sm font-medium text-text-primary">
                        {formatDate(enterprise?.created_at)}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-text-tertiary mb-1">邀请链接状态</div>
                      <div className="text-sm font-medium text-text-primary flex items-center gap-1.5">
                        <span
                          className={inviteLink ? 'dot dot-success' : 'dot dot-muted'}
                          aria-hidden="true"
                        />
                        {inviteLink ? '已生成' : '未生成'}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </section>

            {/* ============ 成员邀请 ============ */}
            <section
              ref={(el) => { sectionRefs.current.members = el }}
              className="bg-surface border border-border-default rounded-xl shadow-soft p-6 scroll-mt-24"
            >
              <RoleGuard
                roles={['admin']}
                fallback={
                  <>
                    <div className="space-y-2 mb-5">
                      <h2 className="font-serif-display text-lg font-semibold text-text-primary">成员管理</h2>
                      <div className="brand-rule" />
                    </div>
                    <p className="text-sm text-text-secondary">仅企业管理员可管理成员与邀请。</p>
                  </>
                }
              >
                <div className="space-y-2 mb-5">
                  <div className="flex items-center justify-between">
                    <h2 className="font-serif-display text-lg font-semibold text-text-primary">成员管理</h2>
                    <span className="bg-brand-50 text-brand-500 rounded px-2 py-0.5 text-xs font-medium">管理员</span>
                  </div>
                  <div className="brand-rule" />
                </div>

                {/* 企业成员列表（激活 GET /members, DELETE /members/{id}, PUT /members/{id}/role） */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-medium text-text-primary">企业成员</h3>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-text-tertiary">{members.length} 人</span>
                      <Button variant="outline" onClick={loadMembers} disabled={membersLoading} className="!px-2 !py-1 text-xs">
                        {membersLoading ? '加载中…' : '刷新'}
                      </Button>
                    </div>
                  </div>
                  {membersLoading && members.length === 0 ? (
                    <div className="py-6 text-center text-sm text-text-tertiary bg-surface-2 rounded-md">
                      加载中…
                    </div>
                  ) : members.length === 0 ? (
                    <div className="py-6 text-center text-sm text-text-tertiary bg-surface-2 rounded-md">
                      暂无成员
                    </div>
                  ) : (
                    <ul className="divide-y divide-border-subtle border border-border-subtle rounded-md overflow-hidden">
                      {members.map((m) => {
                        const isSelf = m.id === user?.id
                        return (
                          <li key={m.id} className="flex items-center justify-between px-3 py-2.5 bg-surface-2">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-sm font-medium text-text-primary truncate">
                                  {m.name || m.email}
                                </span>
                                {isSelf && (
                                  <span className="bg-brand-50 text-brand-500 rounded px-1.5 py-0.5 text-xs">我</span>
                                )}
                                {!m.is_active && (
                                  <span className="bg-error/10 text-error rounded px-1.5 py-0.5 text-xs">已禁用</span>
                                )}
                              </div>
                              <div className="text-xs text-text-tertiary mt-0.5 truncate">
                                {m.email}
                              </div>
                            </div>
                            <div className="flex items-center gap-2 flex-shrink-0 ml-3">
                              <select
                                value={m.role}
                                onChange={(e) => handleChangeRole(m, e.target.value as 'admin' | 'member')}
                                disabled={isSelf}
                                className="bg-surface border border-border-default rounded px-2 py-1 text-xs text-text-primary disabled:opacity-50 disabled:cursor-not-allowed"
                                title={isSelf ? '不能修改自己的角色' : '变更角色'}
                              >
                                <option value="member">成员</option>
                                <option value="admin">管理员</option>
                              </select>
                              <button
                                type="button"
                                onClick={() => handleRemoveMember(m)}
                                disabled={isSelf}
                                className="text-error/70 hover:text-error text-xs px-2 py-1 rounded border border-error/30 hover:bg-error/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                                title={isSelf ? '不能移除自己' : '移除成员'}
                                aria-label={isSelf ? '不能移除自己' : `移除成员 ${m.email}`}
                              >
                                <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                              </button>
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </div>

                {/* 邀请链接生成 */}
                <div className="mt-6 pt-5 border-t border-border-subtle space-y-3">
                  <h3 className="text-sm font-medium text-text-primary">邀请链接</h3>
                  <div className="flex gap-2">
                    <input
                      id="invite-link"
                      type="text"
                      value={inviteLink || '点击生成按钮获取邀请链接'}
                      disabled
                      className="settings-input flex-1 bg-surface-2 border border-border-default rounded-md px-4 py-2.5 font-mono text-sm text-text-tertiary"
                    />
                    <Button variant="outline" onClick={handleGenerateInvite} disabled={generating}>
                      {generating ? '生成中…' : '生成'}
                    </Button>
                    <Button
                      variant="primary"
                      onClick={handleCopyInvite}
                      disabled={!inviteLink}
                    >
                      <Copy className="w-3.5 h-3.5" />
                      复制
                    </Button>
                  </div>
                  <div className="bg-brand-50 rounded-md p-3 flex items-start gap-2">
                    <Info className="w-4 h-4 text-brand-500 flex-shrink-0 mt-0.5" />
                    <p className="text-sm text-brand-500">
                      分享此邀请链接给团队成员，他们可以通过此链接注册并加入您的企业。链接有效期为 7 天。
                    </p>
                  </div>
                </div>

                {/* 邀请记录列表（激活 GET /invitations, POST /invitations/{iid}/cancel） */}
                <div className="mt-6 pt-5 border-t border-border-subtle">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-medium text-text-primary">邀请记录</h3>
                    <span className="text-xs text-text-tertiary">{invites.length} 条记录</span>
                  </div>
                  {invites.length === 0 ? (
                    <div className="py-6 text-center text-sm text-text-tertiary bg-surface-2 rounded-md">
                      暂无邀请记录
                    </div>
                  ) : (
                    <ul className="divide-y divide-border-subtle border border-border-subtle rounded-md overflow-hidden">
                      {invites.map((inv) => (
                        <li key={inv.id} className="flex items-center justify-between px-3 py-2.5 bg-surface-2">
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-sm font-medium text-text-primary truncate">
                                {inv.email || '（未指定邮箱）'}
                              </span>
                              <span className={statusDotClass(inv.status)} aria-hidden="true" />
                              <span className="text-xs text-text-secondary">{statusLabel(inv.status)}</span>
                            </div>
                            <div className="text-xs text-text-tertiary mt-0.5 font-mono truncate">
                              token: {inv.token.slice(0, 12)}… · {formatDate(inv.created_at)}
                              {inv.expires_at && ` · 过期 ${formatDate(inv.expires_at)}`}
                            </div>
                          </div>
                          <div className="flex items-center gap-2 flex-shrink-0 ml-3">
                            {inv.status === 'pending' && (
                              <button
                                type="button"
                                onClick={() => handleCancelInvite(inv)}
                                disabled={cancellingInviteId === inv.id}
                                className="text-error/70 hover:text-error text-xs px-2 py-1 rounded border border-error/30 hover:bg-error/5 disabled:opacity-50 transition-colors"
                                title="取消邀请"
                              >
                                {cancellingInviteId === inv.id ? '取消中…' : '取消邀请'}
                              </button>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {/* 新邀请表单 */}
                <div className="mt-6 pt-5 border-t border-border-subtle">
                  <h3 className="text-sm font-medium text-text-primary mb-3">邀请新成员</h3>
                  <div className="flex flex-col sm:flex-row gap-2">
                    <input
                      type="email"
                      value={newInviteEmail}
                      onChange={(e) => setNewInviteEmail(e.target.value)}
                      placeholder="被邀请人邮箱"
                      className="settings-input flex-1 bg-surface-2 border border-border-default rounded-md px-4 py-2.5 text-sm text-text-primary placeholder-text-tertiary"
                    />
                    <select
                      value={newInviteRole}
                      onChange={(e) => setNewInviteRole(e.target.value as 'admin' | 'member')}
                      className="settings-input bg-surface-2 border border-border-default rounded-md px-4 py-2.5 text-sm text-text-primary"
                      title="角色在邀请被接受后可在上方成员列表中调整"
                    >
                      <option value="member">成员（默认）</option>
                      <option value="admin">管理员</option>
                    </select>
                    <Button onClick={handleSendInvite} disabled={sendingInvite}>
                      {sendingInvite ? '发送中…' : '生成邀请'}
                    </Button>
                  </div>
                  <p className="text-xs text-text-tertiary mt-2">
                    角色可在邀请被接受后通过上方"成员管理"列表的"角色下拉"调整。
                  </p>
                </div>
              </RoleGuard>
            </section>

            {/* ============ 系统偏好 ============ */}
            <section
              ref={(el) => { sectionRefs.current.preferences = el }}
              className="bg-surface border border-border-default rounded-xl shadow-soft p-6 scroll-mt-24"
            >
              <div className="space-y-2 mb-5">
                <h2 className="font-serif-display text-lg font-semibold text-text-primary">系统偏好</h2>
                <div className="brand-rule" />
              </div>

              {/* 通知偏好 */}
              <div className="pt-5 border-t border-border-subtle">
                <div className="flex items-center gap-2 mb-3">
                  <Users className="w-4 h-4 text-text-secondary" />
                  <h3 className="text-sm font-medium text-text-primary">通知偏好</h3>
                </div>
                <ul className="space-y-1">
                  {([
                    {
                      key: 'email',
                      label: '邮件通知',
                      desc: '接收企业活动、成员变动等重要邮件',
                      value: notifEmail,
                      setter: setNotifEmail,
                    },
                    {
                      key: 'push',
                      label: '站内推送',
                      desc: '浏览器站内消息实时推送',
                      value: notifPush,
                      setter: setNotifPush,
                    },
                    {
                      key: 'digest',
                      label: '每周摘要',
                      desc: '每周一接收企业运营数据摘要',
                      value: notifDigest,
                      setter: setNotifDigest,
                    },
                  ] as const).map(({ key, label, desc, value, setter }) => (
                    <li
                      key={key}
                      className="flex items-center justify-between py-2.5 px-2 -mx-2 rounded-md hover:bg-surface-2 transition-colors"
                    >
                      <div className="min-w-0 mr-4">
                        <div className="text-sm font-medium text-text-primary">{label}</div>
                        <div className="text-xs text-text-tertiary mt-0.5">{desc}</div>
                      </div>
                      <ToggleSwitch
                        checked={value}
                        onChange={(v) => {
                          setter(v)
                          setSuccess('通知偏好已更新')
                          setTimeout(() => setSuccess(''), TOAST_DISMISS_SHORT_MS)
                        }}
                        aria-label={label}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            </section>

            {/* ============ 渐进式自主模式 ============ */}
            <section
              ref={(el) => { sectionRefs.current.autonomy = el }}
              className="bg-surface border border-border-default rounded-xl shadow-soft p-6 scroll-mt-24"
            >
              <div className="space-y-2 mb-5">
                <div className="flex items-center justify-between">
                  <h2 className="font-serif-display text-lg font-semibold text-text-primary">渐进式自主模式</h2>
                  <span className="bg-brand-50 text-brand-500 rounded px-2 py-0.5 text-xs font-medium">
                    当前：{AUTONOMY_MODES.find((m) => m.id === autonomyMode)?.label}
                  </span>
                </div>
                <div className="brand-rule" />
              </div>

              <p className="text-sm text-text-secondary mb-4">
                系统支持由浅入深的四种自主模式，企业无需理解 AI 即可上手，随成长可深度自定义。切换后即时生效，无需重新编译。
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {AUTONOMY_MODES.map((m) => {
                  const active = autonomyMode === m.id
                  return (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => handleAutonomyChange(m.id)}
                      aria-pressed={active}
                      className={`text-left rounded-lg border p-4 transition-colors ${
                        active
                          ? 'border-brand-500 bg-brand-50'
                          : 'border-border-default bg-surface-2 hover:border-brand-500/50'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className={`text-sm font-medium ${active ? 'text-brand-500' : 'text-text-primary'}`}>
                          {m.label}
                        </span>
                        {active && (
                          <span className="ml-auto bg-brand-500 text-white rounded px-1.5 py-0.5 text-xs font-medium">当前</span>
                        )}
                      </div>
                      <p className="text-xs text-text-tertiary mt-1.5">{m.desc}</p>
                    </button>
                  )
                })}
              </div>
            </section>

            {/* ============ 模型 API 配置 ============ */}
            <section
              ref={(el) => { sectionRefs.current.llm = el }}
              className="bg-surface border border-border-default rounded-xl shadow-soft p-6 scroll-mt-24"
            >
              <RoleGuard
                roles={['admin']}
                fallback={
                  <>
                    <div className="space-y-2 mb-5">
                      <h2 className="font-serif-display text-lg font-semibold text-text-primary">模型 API 配置</h2>
                      <div className="brand-rule" />
                    </div>
                    <p className="text-sm text-text-secondary">
                      仅企业管理员可配置模型 API。配置后，协作工作台对话与 AI 数字员工将使用企业自定义的模型 API。
                    </p>
                  </>
                }
              >
                <div className="space-y-2 mb-5">
                  <div className="flex items-center justify-between">
                    <h2 className="font-serif-display text-lg font-semibold text-text-primary">模型 API 配置</h2>
                    <span className="inline-flex items-center gap-1.5 bg-brand-50 text-brand-500 rounded px-2 py-0.5 text-xs font-medium">
                      <ShieldCheck className="w-3.5 h-3.5" /> 密钥加密存储
                    </span>
                  </div>
                  <div className="brand-rule" />
                </div>

                <p className="text-sm text-text-secondary mb-5">
                  配置后，协作工作台对话与 AI 数字员工将使用企业自定义的模型 API（未配置时回退到平台全局模型）。
                  API Key 使用 Fernet 加密存储，界面仅展示掩码，绝不回显明文。
                </p>

                {llmLoading && !llmConfig ? (
                  <div className="py-8 text-center text-sm text-text-tertiary bg-surface-2 rounded-md">加载中…</div>
                ) : (
                  <div className="space-y-6">
                    {/* ---- OpenAI 兼容 API ---- */}
                    <div className="rounded-lg border border-border-default bg-surface-2 p-5">
                      <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-2">
                          <Cpu className="w-4 h-4 text-text-secondary" />
                          <h3 className="text-sm font-medium text-text-primary">OpenAI 兼容 API</h3>
                        </div>
                        <ToggleSwitch
                          checked={openaiEnabled}
                          onChange={setOpenaiEnabled}
                          aria-label="启用 OpenAI 兼容 API"
                        />
                      </div>

                      <div className="space-y-4">
                        <div>
                          <label className="block text-xs text-text-tertiary mb-1.5">
                            Base URL <span className="text-text-tertiary/70">（如 https://api.openai.com/v1）</span>
                          </label>
                          <input
                            type="text"
                            value={openaiBase}
                            onChange={(e) => setOpenaiBase(e.target.value)}
                            placeholder="https://api.openai.com/v1"
                            disabled={!openaiEnabled}
                            className="settings-input w-full bg-surface border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-tertiary disabled:opacity-50"
                          />
                        </div>

                        <div>
                          <label className="block text-xs text-text-tertiary mb-1.5">
                            API Key
                            {llmConfig?.openai_has_key && openaiEnabled && (
                              <span className="ml-2 text-brand-500 font-mono">已保存：{llmConfig.openai_api_key_masked}</span>
                            )}
                          </label>
                          <input
                            type="password"
                            value={openaiKey}
                            onChange={(e) => setOpenaiKey(e.target.value)}
                            placeholder={openaiKey ? '' : (llmConfig?.openai_has_key ? '留空则保留已保存的密钥' : 'sk-…')}
                            disabled={!openaiEnabled}
                            autoComplete="off"
                            className="settings-input w-full bg-surface border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-tertiary disabled:opacity-50"
                          />
                        </div>

                        <div>
                          <label className="block text-xs text-text-tertiary mb-1.5">默认模型名</label>
                          <input
                            type="text"
                            value={openaiModel}
                            onChange={(e) => setOpenaiModel(e.target.value)}
                            placeholder="gpt-4o"
                            disabled={!openaiEnabled}
                            className="settings-input w-full bg-surface border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-tertiary disabled:opacity-50"
                          />
                        </div>
                      </div>
                    </div>

                    {/* ---- Anthropic API ---- */}
                    <div className="rounded-lg border border-border-default bg-surface-2 p-5">
                      <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-2">
                          <Bot className="w-4 h-4 text-text-secondary" />
                          <h3 className="text-sm font-medium text-text-primary">Anthropic API</h3>
                        </div>
                        <ToggleSwitch
                          checked={anthropicEnabled}
                          onChange={setAnthropicEnabled}
                          aria-label="启用 Anthropic API"
                        />
                      </div>

                      <div className="space-y-4">
                        <div>
                          <label className="block text-xs text-text-tertiary mb-1.5">
                            Base URL <span className="text-text-tertiary/70">（默认 https://api.anthropic.com）</span>
                          </label>
                          <input
                            type="text"
                            value={anthropicBase}
                            onChange={(e) => setAnthropicBase(e.target.value)}
                            placeholder="https://api.anthropic.com"
                            disabled={!anthropicEnabled}
                            className="settings-input w-full bg-surface border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-tertiary disabled:opacity-50"
                          />
                        </div>

                        <div>
                          <label className="block text-xs text-text-tertiary mb-1.5">
                            API Key
                            {llmConfig?.anthropic_has_key && anthropicEnabled && (
                              <span className="ml-2 text-brand-500 font-mono">已保存：{llmConfig.anthropic_api_key_masked}</span>
                            )}
                          </label>
                          <input
                            type="password"
                            value={anthropicKey}
                            onChange={(e) => setAnthropicKey(e.target.value)}
                            placeholder={anthropicKey ? '' : (llmConfig?.anthropic_has_key ? '留空则保留已保存的密钥' : 'sk-ant-…')}
                            disabled={!anthropicEnabled}
                            autoComplete="off"
                            className="settings-input w-full bg-surface border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-tertiary disabled:opacity-50"
                          />
                        </div>

                        <div>
                          <label className="block text-xs text-text-tertiary mb-1.5">默认模型名</label>
                          <input
                            type="text"
                            value={anthropicModel}
                            onChange={(e) => setAnthropicModel(e.target.value)}
                            placeholder="claude-3-5-sonnet-20241022"
                            disabled={!anthropicEnabled}
                            className="settings-input w-full bg-surface border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-tertiary disabled:opacity-50"
                          />
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-start gap-2 text-xs text-text-tertiary">
                        <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
                        <span>
                          OpenAI 兼容与 Anthropic 仅可启用其一；保存后立即对协作工作台与 AI 数字员工生效。
                        </span>
                      </div>
                      <Button variant="primary" onClick={handleSaveLLMConfig} disabled={savingLLM}>
                        {savingLLM ? '保存中…' : '保存配置'}
                      </Button>
                    </div>
                  </div>
                )}
              </RoleGuard>
            </section>

            {/* ============ AI 模型路由与 BYOK ============ */}
            <section
              ref={(el) => { sectionRefs.current.modelRouting = el }}
              className="bg-surface border border-border-default rounded-xl shadow-soft p-6 scroll-mt-24"
            >
              <RoleGuard
                roles={['admin']}
                fallback={
                  <>
                    <div className="space-y-2 mb-5">
                      <h2 className="font-serif-display text-lg font-semibold text-text-primary">模型路由与 BYOK</h2>
                      <div className="brand-rule" />
                    </div>
                    <p className="text-sm text-text-tertiary">仅企业管理员可查看与配置模型路由策略。</p>
                  </>
                }
              >
                <div className="space-y-2 mb-5">
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <h2 className="font-serif-display text-lg font-semibold text-text-primary">
                      AI 模型路由与 BYOK
                    </h2>
                    <span className="inline-flex items-center gap-1.5 bg-brand-50 text-brand-500 rounded px-2 py-0.5 text-xs font-medium">
                      <ShieldCheck className="w-3.5 h-3.5" /> 密钥加密存储
                    </span>
                  </div>
                  <div className="brand-rule" />
                  <p className="text-sm text-text-tertiary">
                    主力模型 DeepSeek，按场景在 Kimi / GLM-4 之间备选。DeepSeek、Kimi、GLM-4
                    均提供 OpenAI 兼容端点，选中后把接入参数填入「模型 API 配置」的 OpenAI
                    兼容槽位，再填入你自己的 Key 即可完成 BYOK。
                  </p>
                </div>

                {/* 路由矩阵：镜像后端 registry.ROUTING_MATRIX，只读 */}
                <div className="rounded-lg border border-border-default bg-surface-2 p-5 mb-6">
                  <div className="flex items-center justify-between pb-3 mb-4 border-b border-border-default">
                    <h3 className="text-sm font-medium text-text-primary flex items-center gap-2">
                      <Route className="w-4 h-4 text-text-secondary" />
                      任务类型 → 模型路由矩阵
                    </h3>
                    <span className="text-xs text-text-tertiary">服务端策略 · 只读</span>
                  </div>
                  <dl className="space-y-2.5">
                    {ROUTING_MATRIX.map((row) => (
                      <div key={row.task} className="flex items-center gap-3 flex-wrap">
                        <dt className="text-xs text-text-tertiary w-40 flex-shrink-0">{row.task}</dt>
                        <dd className="flex items-center gap-2 flex-wrap">
                          {row.chain.map((name, i) => (
                            <span key={name} className="flex items-center gap-2">
                              {i > 0 && (
                                <span className="text-text-tertiary text-xs" aria-hidden="true">
                                  →
                                </span>
                              )}
                              <span
                                className={`text-[11px] px-2 py-0.5 rounded font-medium ${
                                  i === 0
                                    ? 'bg-brand-50 text-brand-500'
                                    : 'bg-surface text-text-tertiary border border-border-default'
                                }`}
                              >
                                {name}
                              </span>
                            </span>
                          ))}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>

                {/* Provider 目录：点选后写入下方 OpenAI 兼容槽位 */}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                  {MODEL_PROVIDERS.map((provider) => {
                    const active = provider.id === selectedProviderId
                    const saved = llmConfig?.openai_enabled && llmConfig.openai_api_base === provider.apiBase
                    return (
                      <div
                        key={provider.id}
                        className={`rounded-lg border p-5 flex flex-col ${
                          active
                            ? 'border-brand-500 bg-brand-50/40'
                            : 'border-border-default bg-surface-2'
                        }`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <h3 className="text-sm font-medium text-text-primary truncate">
                              {provider.name}
                            </h3>
                            <p className="text-[11px] text-text-tertiary truncate">
                              {provider.vendor} · {provider.role}
                            </p>
                          </div>
                          {saved && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-success/10 text-success flex-shrink-0">
                              已生效
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-text-tertiary mt-2 leading-relaxed flex-1">
                          {provider.scenarios}
                        </p>
                        <code className="text-[10px] font-mono text-text-tertiary bg-elevated px-1.5 py-1 rounded mt-2 block truncate">
                          {provider.defaultModel}
                        </code>
                        <Button
                          variant={active ? 'primary' : 'outline'}
                          size="sm"
                          className="mt-3"
                          onClick={() => applyProviderToOpenAISlot(provider)}
                        >
                          {active ? '已填入接入参数' : '应用到 OpenAI 兼容槽位'}
                        </Button>
                      </div>
                    )
                  })}
                </div>

                <div className="mt-5 flex items-start gap-2 text-xs text-text-tertiary">
                  <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  <span>
                    密钥不会回显：已保存的 Key 仅显示掩码，留空即保留原值。
                    服务端当前按 .env 决定各 provider 的兜底优先级，企业配置在启用后立即覆盖对应通道。
                  </span>
                </div>
              </RoleGuard>
            </section>

            {/* ============ Agent API 机器凭证 ============ */}
            <section
              ref={(el) => { sectionRefs.current.agentKeys = el }}
              className="bg-surface border border-border-default rounded-xl shadow-soft p-6 scroll-mt-24"
            >
              <RoleGuard
                roles={['admin']}
                fallback={
                  <>
                    <div className="space-y-2 mb-5">
                      <h2 className="font-serif-display text-lg font-semibold text-text-primary">机器凭证</h2>
                      <div className="brand-rule" />
                    </div>
                    <p className="text-sm text-text-secondary">
                      仅企业管理员可管理 Agent API 机器凭证。
                    </p>
                  </>
                }
              >
                <div className="space-y-2 mb-5">
                  <div className="flex items-center justify-between">
                    <h2 className="font-serif-display text-lg font-semibold text-text-primary">机器凭证（Agent API）</h2>
                    <span className="inline-flex items-center gap-1.5 bg-brand-50 text-brand-500 rounded px-2 py-0.5 text-xs font-medium">
                      <ShieldCheck className="w-3.5 h-3.5" /> 密钥仅哈希存储
                    </span>
                  </div>
                  <div className="brand-rule" />
                </div>

                <p className="text-sm text-text-secondary mb-5">
                  供外部 Agent 程序通过请求头 <code className="text-xs bg-surface-2 px-1 py-0.5 rounded">X-AutoTeams-Agent-Key</code> 调用受限 REST API。
                  完整密钥只在创建时显示一次，后端仅保存不可逆哈希；泄露或不再使用时请立即撤销。
                </p>

                {/* ---- 一次性密钥展示 ---- */}
                {createdCredKey && (
                  <div className="mb-5 rounded-lg border border-brand-500/40 bg-brand-50 p-4">
                    <div className="flex items-center gap-2 mb-2">
                      <KeyRound className="w-4 h-4 text-brand-500" />
                      <h3 className="text-sm font-medium text-text-primary">
                        密钥已创建：{createdCredKey.name}
                      </h3>
                    </div>
                    <p className="text-xs text-text-secondary mb-3">
                      请立即复制并安全保存以下密钥，关闭后将无法再次查看。
                    </p>
                    <div className="flex items-center gap-2">
                      <code className="flex-1 block font-mono text-xs bg-surface border border-border-default rounded-md px-3 py-2 break-all select-all">
                        {createdCredKey.api_key}
                      </code>
                      <Button variant="secondary" onClick={handleCopyApiKey}>
                        <Copy className="w-4 h-4 mr-1" /> 复制
                      </Button>
                      <Button variant="ghost" onClick={handleCloseCreatedKey}>
                        我已保存
                      </Button>
                    </div>
                  </div>
                )}

                {/* ---- 创建表单 ---- */}
                <div className="rounded-lg border border-border-default bg-surface-2 p-5 mb-5">
                  <h3 className="text-sm font-medium text-text-primary mb-4">创建新凭证</h3>
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div>
                        <label className="block text-xs text-text-tertiary mb-1.5">凭证名称</label>
                        <input
                          type="text"
                          value={credName}
                          onChange={(e) => setCredName(e.target.value)}
                          placeholder="如：CI 流水线集成"
                          maxLength={100}
                          className="settings-input w-full bg-surface border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-tertiary"
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-text-tertiary mb-1.5">
                          有效期（天）<span className="text-text-tertiary/70">（留空表示永不过期）</span>
                        </label>
                        <input
                          type="number"
                          min={1}
                          value={credExpiryDays}
                          onChange={(e) => setCredExpiryDays(e.target.value)}
                          placeholder="如：90"
                          className="settings-input w-full bg-surface border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-tertiary"
                        />
                      </div>
                    </div>

                    <div>
                      <label className="block text-xs text-text-tertiary mb-1.5">权限范围（scope）</label>
                      <div className="flex flex-wrap gap-3">
                        {AGENT_API_SCOPES.map((scope) => (
                          <label key={scope.value} className="inline-flex items-center gap-1.5 text-sm text-text-primary cursor-pointer">
                            <input
                              type="checkbox"
                              checked={credScopes.includes(scope.value)}
                              onChange={() => toggleCredScope(scope.value)}
                              className="accent-brand-500"
                            />
                            <span>
                              <span className="font-mono text-xs">{scope.value}</span>
                              <span className="text-text-tertiary text-xs ml-1">{scope.label}</span>
                            </span>
                          </label>
                        ))}
                      </div>
                    </div>

                    {enterpriseAgents.length > 0 && (
                      <div>
                        <label className="block text-xs text-text-tertiary mb-1.5">
                          可访问的 Agent <span className="text-text-tertiary/70">（不选表示可访问全部）</span>
                        </label>
                        <div className="flex flex-wrap gap-3">
                          {enterpriseAgents.map((agent) => (
                            <label key={agent.id} className="inline-flex items-center gap-1.5 text-sm text-text-primary cursor-pointer">
                              <input
                                type="checkbox"
                                checked={credAllowedAgentIds.includes(agent.id)}
                                onChange={() => toggleCredAgent(agent.id)}
                                className="accent-brand-500"
                              />
                              {agent.name}
                            </label>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="flex justify-end">
                      <Button variant="primary" onClick={handleCreateCredential} disabled={creatingCred}>
                        {creatingCred ? '创建中…' : '创建凭证'}
                      </Button>
                    </div>
                  </div>
                </div>
                {/* ---- 凭证列表 ---- */}
                {agentCredsLoading ? (
                  <div className="py-8 text-center text-sm text-text-tertiary bg-surface-2 rounded-md">加载中…</div>
                ) : agentCredentials.length === 0 ? (
                  <div className="py-8 text-center text-sm text-text-tertiary bg-surface-2 rounded-md">
                    尚未创建任何机器凭证
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-xs text-text-tertiary border-b border-border-default">
                          <th className="py-2 pr-4 font-medium">名称</th>
                          <th className="py-2 pr-4 font-medium">密钥前缀</th>
                          <th className="py-2 pr-4 font-medium">权限</th>
                          <th className="py-2 pr-4 font-medium">状态</th>
                          <th className="py-2 pr-4 font-medium">最近使用</th>
                          <th className="py-2 pr-4 font-medium">过期时间</th>
                          <th className="py-2 font-medium text-right">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {agentCredentials.map((cred) => {
                          const revoked = !cred.is_active || cred.revoked_at !== null
                          return (
                            <tr key={cred.id} className="border-b border-border-default/60">
                              <td className="py-2.5 pr-4 text-text-primary">{cred.name}</td>
                              <td className="py-2.5 pr-4 font-mono text-xs text-text-secondary">{cred.key_prefix}…</td>
                              <td className="py-2.5 pr-4">
                                <div className="flex flex-wrap gap-1">
                                  {cred.scopes.map((scope) => (
                                    <span key={scope} className="inline-block bg-surface-2 text-text-secondary text-xs rounded px-1.5 py-0.5 font-mono">
                                      {scope}
                                    </span>
                                  ))}
                                </div>
                              </td>
                              <td className="py-2.5 pr-4">
                                <span className={`inline-flex items-center gap-1.5 text-xs ${revoked ? 'text-text-tertiary' : 'text-brand-500'}`}>
                                  <span className={`dot ${revoked ? 'dot-muted' : 'dot-success'}`} />
                                  {revoked ? '已撤销' : '使用中'}
                                </span>
                              </td>
                              <td className="py-2.5 pr-4 text-text-secondary text-xs">{formatDate(cred.last_used_at)}</td>
                              <td className="py-2.5 pr-4 text-text-secondary text-xs">{cred.expires_at ? formatDate(cred.expires_at) : '永不'}</td>
                              <td className="py-2.5 text-right">
                                {!revoked && (
                                  <Button
                                    variant="ghost"
                                    onClick={() => handleRevokeCredential(cred)}
                                    disabled={revokingCredId === cred.id}
                                  >
                                    <Trash2 className="w-4 h-4 mr-1" />
                                    {revokingCredId === cred.id ? '撤销中…' : '撤销'}
                                  </Button>
                                )}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </RoleGuard>
            </section>
          </div>
        </div>
      </div>

      {/* 局部样式: 输入框聚焦 (沿用设计稿 .settings-input) */}
      <style>{`
        .settings-input { transition: border-color .15s, box-shadow .15s; }
        .settings-input:focus {
          outline: none;
          border-color: var(--brand);
          box-shadow: 0 0 0 3px rgba(30, 58, 95, 0.12);
        }
        .settings-input:disabled { cursor: not-allowed; }
      `}</style>
      {confirmDialog.dialog}
    </Layout>
  )
}

/* ------------------------------------------------------------------ */
/* 内部组件: 开关                                                       */
/* ------------------------------------------------------------------ */
function ToggleSwitch({
  checked,
  onChange,
  'aria-label': ariaLabel,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  'aria-label'?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 ${
        checked ? 'bg-brand-500' : 'bg-border-strong'
      }`}
    >
      <span
        className={`inline-block h-[18px] w-[18px] transform rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-[22px]' : 'translate-x-1'
        }`}
      />
    </button>
  )
}
