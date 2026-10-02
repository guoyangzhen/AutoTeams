/**
 * OnboardingWizard — 3 步极速入职向导（重构方案 §4.3.4）。
 *
 * 本组件此前是纯演示壳：上传是 setTimeout 假数据、部署是 toast 假成功。
 * 现在全链路接真实后端，任何一步失败都如实回报，绝不假装成功：
 * - Step 1 选行业   → 纯用户选择（无数据依赖），决定推荐岗位
 * - Step 2 传资料   → POST /folders/upload，拿到真实 folder_path
 * - Step 3 部署开工 → POST /templates/{id}/apply 建 Agent
 *                    + POST /workforce-profiles 建花名册编制（绑定 agent_id）
 *
 * 绑定 agent_id 是关键：只有绑定了底层 Agent，花名册卡片上的「找她聊聊」
 * 才能直达 /chat/:agentId（后端 WorkforceProfileUpdate 不含该字段，
 * 创建时是唯一绑定时机）。
 */
import { useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Building2,
  ShoppingCart,
  Globe2,
  Briefcase,
  GraduationCap,
  HeartPulse,
  UploadCloud,
  CheckCircle2,
  Sparkles,
  ArrowRight,
  UserCheck,
  FileText,
  AlertTriangle,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { toast } from 'sonner'
import { getTemplates, applyTemplate, type AgentTemplate } from '@/api/templates'
import { uploadFolder } from '@/api/folders'
import { createWorkforceProfile } from '@/api/workforceProfiles'
import { type Department } from '@/utils/departments'

/** 行业 → 推荐岗位（岗位值对应后端 AgentTemplate.role，部署时按 role 解析真实模板） */
interface IndustryOption {
  id: string
  name: string
  icon: typeof Building2
  desc: string
  roles: string[]
  /**
   * 编制落到哪个部门。必须取自 utils/departments 的部门表 ——
   * 直接把行业名当 department 写进去，会让这些员工在花名册的部门筛选下永远筛不出来。
   */
  department: Department
}

const INDUSTRIES: IndustryOption[] = [
  {
    id: 'manufacturing',
    name: '智能制造',
    icon: Building2,
    desc: '工单催办、设备巡检 SOP、物料出入库核对',
    roles: ['ops', 'pre_sales', 'customer_service'],
    department: '智能制造部',
  },
  {
    id: 'ecommerce',
    name: '电商零售',
    icon: ShoppingCart,
    desc: '多店铺客服接待、退换货工单流转、询盘推荐',
    roles: ['customer_service', 'sales', 'after_sales'],
    department: '电商零售部',
  },
  {
    id: 'trade',
    name: '跨境出海',
    icon: Globe2,
    desc: '多币种汇率锁价、外贸合同审核、海关单证归档',
    roles: ['sales', 'legal', 'finance'],
    department: '跨境出海部',
  },
  {
    id: 'consulting',
    name: '专业咨询',
    icon: Briefcase,
    desc: '客户访谈摘要、行研简报生成、方案大纲',
    roles: ['pre_sales', 'legal', 'hr'],
    department: '专业咨询部',
  },
  {
    id: 'education',
    name: '教育培训',
    icon: GraduationCap,
    desc: '学员课程答疑、报名线索跟进、排课自动化',
    roles: ['education', 'customer_service', 'hr'],
    department: '教育培训部',
  },
  {
    id: 'healthcare',
    name: '医疗健康',
    icon: HeartPulse,
    desc: '就诊指引、体检报告解读辅助、器械说明书问答',
    roles: ['medical', 'customer_service', 'hr'],
    department: '医疗健康部',
  },
]

/** 岗位 → 花名册里的岗位名（模板名即「XX数字员工」，这里给更贴近业务的称呼） */
const ROLE_JOB_TITLE: Record<string, string> = {
  customer_service: '客服专员',
  sales: '销售助理',
  hr: '人事助理',
  ops: '运营专员',
  finance: '财务专员',
  medical: '医疗知识助理',
  education: '教学助理',
  legal: '法务合规助理',
  pre_sales: '售前方案顾问',
  after_sales: '售后专员',
}

/** 单个岗位的部署结果：如实回报成功/失败与原因 */
interface DeployOutcome {
  name: string
  ok: boolean
  detail: string
}
/**
 * 入职建档时写入的默认「严禁越权事项」。
 *
 * 不能留空：后端 check_duty_boundary 在 forbidden 与 allowed 均为空时直接返回
 * allowed=true（profile_service.py 里的「未触发任何禁止边界项，允许继续履约」），
 * 等于给每个新员工发一张「什么都放行」的通行证，花名册上的边界探针会永远显示通过。
 */
const DEFAULT_FORBIDDEN_BOUNDARIES: string[] = [
  '未经人工确认不得对外发送承诺、报价或合同内容',
  '不得修改或删除企业原始业务数据',
  '不得代替真人做出付款、退款、下单等资金动作',
]

/**
 * 行业推荐岗位 ∩ 服务端实际提供的模板。
 *
 * 放在模块作用域：勾选态与卡片列表必须由同一条规则推导，
 * 组件内各写一份迟早会在「改行业」时对不上。
 */
function resolveTemplates(industryId: string, list: AgentTemplate[]): AgentTemplate[] {
  const ind = INDUSTRIES.find((i) => i.id === industryId) ?? INDUSTRIES[2]
  return ind.roles
    .map((role) => list.find((t) => t.role === role))
    .filter((t): t is AgentTemplate => Boolean(t))
}

export function OnboardingWizard({ onComplete }: { onComplete?: () => void }) {
  const navigate = useNavigate()
  /** 部署进行中的同步闸门：防止同一帧内连点启动两轮循环、建出重复员工 */
  const deployingRef = useRef(false)
  const [step, setStep] = useState<1 | 2 | 3>(1)
  /** 正在部署的岗位：applyTemplate 单个可能耗时数十秒，需要给逐条反馈 */
  const [deployingRole, setDeployingRole] = useState<string | null>(null)
  const [deployedCount, setDeployedCount] = useState(0)
  const [selectedIndustry, setSelectedIndustry] = useState<string>('trade')

  // 真实模板：部署目标按行业推荐的 role 从后端模板表里解析，不在前端硬编码模板
  const [templates, setTemplates] = useState<AgentTemplate[] | null>(null)
  const [templatesError, setTemplatesError] = useState<string | null>(null)
  const [loadingTemplates, setLoadingTemplates] = useState(false)

  const [folder, setFolder] = useState<{ path: string; name: string; fileCount: number } | null>(null)
  const [uploading, setUploading] = useState(false)
  const [pickedRoles, setPickedRoles] = useState<string[]>([])

  const [isDeploying, setIsDeploying] = useState(false)
  const [outcomes, setOutcomes] = useState<DeployOutcome[]>([])

  const currentInd = INDUSTRIES.find((i) => i.id === selectedIndustry) ?? INDUSTRIES[2]

  /** 行业推荐岗位中，服务端确实提供了模板的那些（缺模板就少建一个，不编造） */
  const recommended = useMemo(
    () => (templates ? resolveTemplates(selectedIndustry, templates) : []),
    [templates, selectedIndustry],
  )



  const loadTemplates = async () => {
    setLoadingTemplates(true)
    setTemplatesError(null)
    try {
      const list = await getTemplates()
      setTemplates(list)
      setPickedRoles(resolveTemplates(selectedIndustry, list).map((t) => t.role))
    } catch (err) {
      setTemplatesError(err instanceof Error ? err.message : '模板清单加载失败')
    } finally {
      setLoadingTemplates(false)
    }
  }

  /**
   * 清掉上一轮部署结果。
   *
   * 换行业（岗位集合变了）与「第 3 步退回再回来」都必须重置：
   * 否则 finished 一直为真，部署按钮被永久替换成「进入花名册」，
   * 失败的岗位再也点不到重试。
   */
  const resetDeployState = () => {
    setOutcomes([])
    setDeployedCount(0)
    setDeployingRole(null)
  }

  const selectIndustry = (industryId: string) => {
    setSelectedIndustry(industryId)
    resetDeployState()
    if (templates) {
      setPickedRoles(resolveTemplates(industryId, templates).map((t) => t.role))
    }
  }

  const goToStep3 = async () => {
    setStep(3)
    resetDeployState()
    if (!templates) await loadTemplates()
  }

  const handleUpload = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return
    setUploading(true)
    try {
      const res = await uploadFolder(Array.from(fileList))
      setFolder({ path: res.folder_path, name: res.folder_name, fileCount: res.file_count })
      toast.success(`已上传 ${res.file_count} 个文件，将作为这批数字员工的知识源`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '资料上传失败，请重试')
    } finally {
      setUploading(false)
    }
  }

  const deploy = async () => {
    // ref 兜底：disabled 只挡得住「重渲染之后」的点击，挡不住同一帧内的连点，
    // 而两次并发循环会真的建出两套重复员工
    if (deployingRef.current) return
    const targets = recommended.filter((t) => pickedRoles.includes(t.role))
    if (targets.length === 0) return
    deployingRef.current = true
    setIsDeploying(true)
    setOutcomes([])
    setDeployedCount(0)
    const results: DeployOutcome[] = []
    for (const tpl of targets) {
      setDeployingRole(tpl.role)
      try {
        let agentId: string | undefined
        if (folder) {
          // 建 Agent（带知识库 + 技能 + 中文 system prompt）
          const applied = await applyTemplate(tpl.id, {
            folder_path: folder.path,
            name_override: tpl.name,
          })
          agentId = applied.agent_id
        }
        // 建花名册编制并绑定 agent_id —— 这是「找她聊聊」能直达对话的前提
        await createWorkforceProfile({
          display_name: tpl.name,
          job_title: ROLE_JOB_TITLE[tpl.role] ?? tpl.role,
          department: currentInd.department,
          agent_id: agentId,
          duty_boundaries: {
            allowed: [],
            forbidden: DEFAULT_FORBIDDEN_BOUNDARIES,
          },
          tone_style: 'professional',
          employment_status: 'shadow',
          performance_score: 85,
        })
        results.push({
          name: tpl.name,
          ok: true,
          detail: folder ? '已上线，可直接对话' : '已建编制（未上传资料，暂不能对话）',
        })
        setDeployedCount(results.length)
        setOutcomes([...results])
      } catch (err) {
        results.push({
          name: tpl.name,
          ok: false,
          detail: err instanceof Error && err.message ? err.message : '部署失败',
        })
        setDeployedCount(results.length)
        setOutcomes([...results])
      }
    }
    setOutcomes(results)
    setIsDeploying(false)
    setDeployingRole(null)
    deployingRef.current = false
    const okCount = results.filter((r) => r.ok).length
    if (okCount > 0) {
      toast.success(`已成功上线 ${okCount} 位数字员工`)
    }
    if (okCount < results.length) {
      toast.error(`${results.length - okCount} 位数字员工部署失败，详情见下方清单`)
    }
  }

  // 注意：部署过程中 outcomes 也会逐条回写，所以必须排除 isDeploying，
  // 否则进度条刚出现就把「进入花名册」按钮顶出来了
  const finished = !isDeploying && outcomes.length > 0

  return (
    <div className="max-w-4xl mx-auto py-8 px-4">
      {/* 顶部步骤导航 */}
      <div className="flex items-center justify-between mb-8 pb-4 border-b border-[#E4E4E1]">
        <div>
          <h1 className="text-[22px] font-bold text-[#0B0B0B] tracking-tight flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-[#1F4FD8]" />
            AutoTeams 极速入职向导 — 3 分钟拥有你的首批 AI 员工
          </h1>
          <p className="text-[13px] text-[#6B6B66] mt-1">
            专为中小企业量身打造，选行业、传资料即刻开工
          </p>
        </div>
        <div className="flex items-center gap-2 text-[12px] font-mono">
          <span
            className={`px-2.5 py-1 rounded-[4px] ${step === 1 ? 'bg-[#1F4FD8] text-white' : 'bg-[#F4F4F3] text-[#6B6B66]'}`}
          >
            1. 选行业
          </span>
          <span>→</span>
          <span
            className={`px-2.5 py-1 rounded-[4px] ${step === 2 ? 'bg-[#1F4FD8] text-white' : 'bg-[#F4F4F3] text-[#6B6B66]'}`}
          >
            2. 传资料
          </span>
          <span>→</span>
          <span
            className={`px-2.5 py-1 rounded-[4px] ${step === 3 ? 'bg-[#1F4FD8] text-white' : 'bg-[#F4F4F3] text-[#6B6B66]'}`}
          >
            3. 部署开工
          </span>
        </div>
      </div>

      {/* Step 1: 选行业 */}
      {step === 1 && (
        <div className="space-y-6">
          <div className="text-[15px] font-semibold text-[#0B0B0B]">请选择贵公司的核心所属业务领域：</div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {INDUSTRIES.map((ind) => {
              const Icon = ind.icon
              const isSelected = selectedIndustry === ind.id
              return (
                <button
                  key={ind.id}
                  type="button"
                  onClick={() => selectIndustry(ind.id)}
                  className={`p-5 rounded-[10px] border text-left transition-all ${
                    isSelected
                      ? 'border-[#1F4FD8] bg-[#1F4FD8]/5 shadow-[0_2px_8px_rgba(31,79,216,0.1)]'
                      : 'border-[#E4E4E1] bg-white hover:border-[#1F4FD8]/40'
                  }`}
                >
                  <div className="flex items-center gap-3 mb-2">
                    <div
                      className={`p-2 rounded-[6px] ${isSelected ? 'bg-[#1F4FD8] text-white' : 'bg-[#F4F4F3] text-[#0B0B0B]'}`}
                    >
                      <Icon className="w-5 h-5" />
                    </div>
                    <span className="font-semibold text-[15px] text-[#0B0B0B]">{ind.name}</span>
                  </div>
                  <p className="text-[12px] text-[#6B6B66] leading-relaxed mb-3">{ind.desc}</p>
                  <div className="flex flex-wrap gap-1">
                    {ind.roles.map((role) => (
                      <span
                        key={role}
                        className="text-[11px] px-1.5 py-0.5 bg-white border border-[#E4E4E1] rounded-[4px] text-[#0B0B0B]"
                      >
                        {ROLE_JOB_TITLE[role] ?? role}
                      </span>
                    ))}
                  </div>
                </button>
              )
            })}
          </div>

          <div className="flex justify-end pt-4">
            <Button size="lg" onClick={() => setStep(2)}>
              下一步：上传业务资料 <ArrowRight className="w-4 h-4 ml-1" />
            </Button>
          </div>
        </div>
      )}

      {/* Step 2: 传资料 */}
      {step === 2 && (
        <div className="space-y-6">
          <div>
            <div className="text-[15px] font-semibold text-[#0B0B0B]">
              上传你们公司的产品手册、报价单或常见客户问题
            </div>
            <p className="text-[13px] text-[#6B6B66] mt-1">
              资料将由本地安全加密解析，仅用于为您推荐的数字员工构建专属知识库。
            </p>
          </div>

          <label
            className="block border-2 border-dashed border-[#E4E4E1] hover:border-[#1F4FD8] bg-[#FAFAF9] rounded-[10px] p-10 text-center cursor-pointer transition-colors"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              void handleUpload(e.dataTransfer.files)
            }}
          >
            <input
              type="file"
              multiple
              className="hidden"
              disabled={uploading}
              onChange={(e) => void handleUpload(e.target.files)}
            />
            <UploadCloud className="w-10 h-10 text-[#1F4FD8] mx-auto mb-3" />
            <div className="text-[14px] font-medium text-[#0B0B0B]">
              {uploading ? '正在上传并解析…' : '点击或将文件拖拽至此处上传'}
            </div>
            <div className="text-[12px] text-[#6B6B66] mt-1">
              支持 Word / Excel / PDF / Markdown（单个文件不超过 20MB）
            </div>
          </label>

          {folder && (
            <div className="bg-white border border-[#E4E4E1] rounded-[10px] p-4 space-y-2">
              <div className="text-[13px] font-medium text-[#0B0B0B] flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4 text-[#2E7D32]" /> 已装载业务资料（共 {folder.fileCount}{' '}
                篇）：
              </div>
              <div className="text-[12px] text-[#6B6B66] flex items-center gap-2 font-mono pl-5">
                <FileText className="w-3.5 h-3.5 text-[#1F4FD8]" /> {folder.name}
              </div>
            </div>
          )}

          <div className="flex justify-between pt-4">
            <Button variant="secondary" onClick={() => setStep(1)}>
              返回上一步
            </Button>
            <Button size="lg" onClick={() => void goToStep3()}>
              生成专属数字员工团队 <ArrowRight className="w-4 h-4 ml-1" />
            </Button>
          </div>
        </div>
      )}

      {/* Step 3: 真实岗位模板 + 一键部署 */}
      {step === 3 && (
        <div className="space-y-6">
          <div>
            <div className="text-[15px] font-semibold text-[#0B0B0B]">
              基于【{currentInd.name}】行业特征，为您匹配到 {recommended.length} 个可直接上岗的岗位：
            </div>
            <p className="text-[13px] text-[#6B6B66] mt-1">
              每个岗位来自服务端模板库，开箱即配系统提示词与工作技能。
            </p>
          </div>

          {loadingTemplates && (
            <p className="text-[13px] text-[#6B6B66]">正在读取服务端岗位模板…</p>
          )}

          {templatesError && (
            <div className="flex items-start gap-2 rounded-[10px] border border-[#B23A2F]/30 bg-[#FFF5F5] p-4">
              <AlertTriangle className="w-4 h-4 text-[#B23A2F] mt-0.5 flex-shrink-0" />
              <div className="text-[12px] text-[#B23A2F]">
                岗位模板加载失败：{templatesError}
                <button type="button" className="ml-2 underline" onClick={() => void loadTemplates()}>
                  重试
                </button>
              </div>
            </div>
          )}

          {!loadingTemplates && !templatesError && recommended.length === 0 && (
            <div className="rounded-[10px] border border-[#E4E4E1] bg-white p-4 text-[13px] text-[#6B6B66]">
              服务端模板库暂未提供「{currentInd.name}」对应的岗位。
              <button
                type="button"
                className="ml-2 text-[#1F4FD8] underline"
                onClick={() => {
                  // 模板已在手上，无需重拉；直接换行业并重算勾选
                  selectIndustry('ecommerce')
                }}
              >
                改用「电商零售」岗位
              </button>
            </div>
          )}

          {recommended.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {recommended.map((tpl) => {
                const checked = pickedRoles.includes(tpl.role)
                return (
                  <label
                    key={tpl.id}
                    className={`block cursor-pointer ${checked ? '' : 'opacity-60'}`}
                  >
                    <Card
                      className={`p-5 border-[#E4E4E1] bg-white ${checked ? 'ring-1 ring-[#1F4FD8]' : ''}`}
                    >
                      <div className="flex items-start gap-2 mb-2">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={isDeploying}
                          onChange={() =>
                            setPickedRoles((prev) =>
                              prev.includes(tpl.role)
                                ? prev.filter((r) => r !== tpl.role)
                                : [...prev, tpl.role],
                            )
                          }
                        />
                        <div className="min-w-0">
                          <div className="font-semibold text-[14px] text-[#0B0B0B] truncate">
                            {tpl.name}
                          </div>
                          <div className="text-[11px] text-[#6B6B66] font-mono truncate">
                            {tpl.skill_ids.length} 项工作技能
                          </div>
                        </div>
                      </div>
                      <p className="text-[12px] text-[#6B6B66] mt-2 leading-relaxed">
                        {tpl.description ?? '按行业标准作业流程履约'}
                      </p>
                    </Card>
                  </label>
                )
              })}
            </div>
          )}

          {!folder && (
            <p className="p-4 bg-[#F4F4F3] rounded-[10px] text-[12px] text-[#6B6B66]">
              未上传业务资料：将只创建岗位编制，数字员工暂无可检索的知识库，也还不能直接对话。
              需要完整能力请返回上一步上传资料。
            </p>
          )}

          {/* 部署结果：逐条如实回报 */}
          {/* 部署进度：applyTemplate 单个可达数十秒，必须给逐条反馈 */}
          {isDeploying && (
            <div className="rounded-[10px] border border-[#1F4FD8]/30 bg-[#1F4FD8]/5 p-4 text-[12px] text-[#0B0B0B] space-y-1">
              <p>
                正在部署第 {deployedCount + 1} / {pickedRoles.length} 位
                {deployingRole && `：${recommended.find((t) => t.role === deployingRole)?.name ?? ''}`}
                …
              </p>
              <p className="text-[#6B6B66]">
                每位员工需要扫描文档、向量化并装配技能，请勿关闭页面。
              </p>
            </div>
          )}

          {finished && (
            <div className="rounded-[10px] border border-[#E4E4E1] bg-white p-4 space-y-2">
              <div className="text-[13px] font-medium text-[#0B0B0B]">部署结果</div>
              {outcomes.map((o) => (
                <div key={o.name} className="flex items-start gap-2 text-[12px]">
                  {o.ok ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-[#2E7D32] mt-0.5 flex-shrink-0" />
                  ) : (
                    <AlertTriangle className="w-3.5 h-3.5 text-[#B23A2F] mt-0.5 flex-shrink-0" />
                  )}
                  <span className={o.ok ? 'text-[#6B6B66]' : 'text-[#B23A2F]'}>
                    {o.name}：{o.detail}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="p-4 bg-[#F4F4F3] rounded-[10px] text-[12px] text-[#6B6B66] flex items-center justify-between">
            <span>
              💡 提示：高级用户可在「Agent 接入」中无缝接入本地正在运行的 OpenAI Codex 或 Claude Code。
            </span>
          </div>

          <div className="flex justify-between pt-4">
            <Button variant="secondary" onClick={() => setStep(2)} disabled={isDeploying}>
              返回上一步
            </Button>
            {finished ? (
              <div className="flex items-center gap-2">
                {outcomes.some((o) => !o.ok) && (
                  <Button
                    size="lg"
                    variant="secondary"
                    onClick={() => void deploy()}
                    disabled={isDeploying}
                  >
                    重试失败的员工
                  </Button>
                )}
                <Button
                  size="lg"
                  onClick={() => {
                    // 驾驶舱传了 onComplete（只做刷新，不跳转），
                    // 但按钮写的是「进入花名册」——必须真的跳过去，否则点了等于没反应
                    onComplete?.()
                    navigate('/workforce')
                  }}
                >
                  <UserCheck className="w-4 h-4 mr-1.5" />
                  进入花名册查看
                </Button>
              </div>
            ) : (
              <Button
                size="lg"
                onClick={() => void deploy()}
                disabled={isDeploying || recommended.length === 0 || pickedRoles.length === 0}
              >
                <UserCheck className="w-4 h-4 mr-1.5" />
                {isDeploying ? '正在初始化数字员工…' : '一键批准上线并进入工作台'}
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
