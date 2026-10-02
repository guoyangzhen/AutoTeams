import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Headset, TrendingUp, Users, Activity, ChevronRight, Check, ArrowLeft,
  FolderSearch, Sparkles, Landmark, Stethoscope, GraduationCap, Scale,
  Save, X, BookOpen, FolderUp, Loader2,
  type LucideIcon,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { Button } from '@/components/ui/Button'
import { Card, CardBody } from '@/components/ui/Card'
import { Spinner } from '@/components/ui/Spinner'
import { PageHeader } from '@/components/ui/PageHeader'
import { ModalShell } from '@/components/ui/ModalShell'
import {
  getTemplates,
  applyTemplate,
  createPrivateTemplate,
  getSkillTemplates,
  type AgentTemplate,
  type SkillTemplate,
  type CreateTemplatePayload,
} from '@/api/templates'
import { uploadFolder } from '@/api/folders'

// 岗位 → 图标映射
const ROLE_ICON: Record<string, LucideIcon> = {
  customer_service: Headset,
  sales: TrendingUp,
  hr: Users,
  ops: Activity,
  finance: Landmark,
  medical: Stethoscope,
  education: GraduationCap,
  legal: Scale,
}

// 岗位 → 中文展示名
const ROLE_LABEL: Record<string, string> = {
  customer_service: '客服',
  sales: '销售',
  hr: 'HR',
  ops: '运营',
  finance: '金融',
  medical: '医疗',
  education: '教育',
  legal: '法律',
}

// 岗位 → 强调色（与 KpiCard 保持一致的视觉语言）
const ROLE_ACCENT: Record<string, string> = {
  customer_service: 'text-brand-500',
  sales: 'text-success',
  hr: 'text-warning',
  ops: 'text-info',
  finance: 'text-warning',
  medical: 'text-error',
  education: 'text-info',
  legal: 'text-success',
}

/**
 * 技能编码（SkillTemplate.code）→ 中文名称映射。
 * 与后端 PRESET_SKILL_TEMPLATES（backend/app/services/template_service.py）对齐，
 * 避免在前端直接展示 cs_ticket_classification、sales_lead_scoring 等英文编码。
 */
const SKILL_CODE_LABELS: Record<string, string> = {
  // 客服
  cs_ticket_classification: '工单分类',
  cs_faq_match: 'FAQ 知识问答',
  cs_sentiment_analysis: '情感与紧急度分析',
  cs_escalation: '工单升级判定',
  // 销售
  sales_lead_scoring: '线索评分',
  sales_script: '销售话术生成',
  sales_followup: '跟进计划生成',
  sales_crm_record: 'CRM 字段提取',
  // 人事
  hr_resume_screening: '简历筛选',
  hr_policy_qa: '人事政策问答',
  hr_onboarding: '入职引导生成',
  hr_offboarding: '离职流程清单',
  // 运营
  ops_monitoring_summary: '监控指标摘要',
  ops_alert_triage: '告警分级',
  ops_report_generation: '运营周报生成',
  ops_root_cause: '根因分析',
  // 金融/财务
  finance_report_summary: '财报摘要生成',
  finance_compliance_check: '合规条款检查',
  finance_risk_assessment: '信用风险评估',
  finance_investment_proposal: '投资建议生成',
  // 医疗
  medical_record_extraction: '病历信息提取',
  medical_medication_safety: '用药禁忌检查',
  medical_symptom_triage: '症状分诊建议',
  medical_literature_summary: '医学文献摘要',
  // 教育
  edu_homework_grading: '作业批改与反馈',
  edu_syllabus_generation: '课程大纲生成',
  edu_student_qa: '学生问题答疑',
  edu_learning_path: '学习路径推荐',
  // 法律
  legal_contract_extraction: '合同条款提取',
  legal_regulation_retrieval: '法规匹配检索',
  legal_litigation_risk: '诉讼风险评估',
  legal_opinion_generation: '法律意见书生成',
  // 售前
  presales_product_query: '产品参数查询',
  presales_tech_proposal: '技术方案生成',
  presales_product_comparison: '产品对比分析',
  presales_tech_qa: '技术答疑',
  // 售后
  aftersales_classification: '售后问题分类',
  aftersales_solution: '售后方案生成',
  aftersales_followup: '客户回访生成',
  aftersales_complaint: '投诉处理建议',
}

/**
 * 将技能编码转为中文名称。
 * 命中映射表优先；未命中时返回原始 code（保证信息不丢失）。
 */
function skillCodeToLabel(code: string): string {
  return SKILL_CODE_LABELS[code] || code
}

interface Toast {
  type: 'success' | 'error' | 'info'
  message: string
}

export default function TemplateGallery() {
  const navigate = useNavigate()
  const [templates, setTemplates] = useState<AgentTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<AgentTemplate | null>(null)
  const [folderPath, setFolderPath] = useState('')
  const [folderFileName, setFolderFileName] = useState('')
  const [uploadingFolder, setUploadingFolder] = useState(false)
  const [nameOverride, setNameOverride] = useState('')
  const [pathError, setPathError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const [toast, setToast] = useState<Toast | null>(null)
  // 技能详情（激活 get_skill_templates 端点）
  const [skillTemplates, setSkillTemplates] = useState<SkillTemplate[]>([])
  const [skillDetail, setSkillDetail] = useState<SkillTemplate | null>(null)
  // 另存为私有模板（激活 create_template 端点）
  const [saveAsOpen, setSaveAsOpen] = useState(false)
  const [saveAsForm, setSaveAsForm] = useState<CreateTemplatePayload | null>(null)
  const [savingAs, setSavingAs] = useState(false)

  // 加载模板列表
  const loadTemplates = useCallback(async () => {
    try {
      setLoading(true)
      const data = await getTemplates()
      setTemplates(data)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载模板失败'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [])

  // 加载技能详情列表（激活 GET /templates/skill-templates）
  const loadSkillTemplates = useCallback(async () => {
    try {
      const data = await getSkillTemplates()
      setSkillTemplates(data)
    } catch (err) {
      // 静默失败，不影响主流程
      console.debug('加载技能详情失败:', err)
    }
  }, [])

  useEffect(() => {
    loadTemplates()
    loadSkillTemplates()
  }, [loadTemplates, loadSkillTemplates])

  // 点击能力标签查看技能详情（激活 GET /templates/skill-templates 的消费侧）
  const handleSkillClick = (code: string) => {
    const skill = skillTemplates.find((s) => s.code === code)
    if (skill) {
      setSkillDetail(skill)
    } else {
      setToast({ type: 'info', message: `技能「${skillCodeToLabel(code)}」详情未加载到，可能未预置` })
    }
  }

  // 打开"另存为私有模板"对话框
  const handleOpenSaveAs = () => {
    if (!selected) return
    setSaveAsForm({
      role: selected.role as CreateTemplatePayload['role'],
      name: `${selected.name}（私有副本）`,
      description: selected.description || undefined,
      system_prompt: selected.system_prompt,
      skill_ids: selected.skill_ids || [],
      knowledge_structure: selected.knowledge_structure || {},
      sample_dialogues: selected.sample_dialogues || [],
    })
    setSaveAsOpen(true)
  }

  const handleSaveAs = async () => {
    if (!saveAsForm) return
    if (!saveAsForm.name.trim() || !saveAsForm.system_prompt.trim()) {
      setToast({ type: 'error', message: '名称和系统提示词不能为空' })
      return
    }
    setSavingAs(true)
    try {
      const created = await createPrivateTemplate(saveAsForm)
      setToast({ type: 'success', message: `已保存为私有模板「${created.name}」` })
      setSaveAsOpen(false)
      setSaveAsForm(null)
      // 刷新模板列表（新建的私有模板会出现在列表中）
      await loadTemplates()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '保存私有模板失败'
      setToast({ type: 'error', message: msg })
    } finally {
      setSavingAs(false)
    }
  }

  // toast 自动消失
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])

  // 选择模板卡片 → 展开右侧应用表单
  const handleSelect = (tpl: AgentTemplate) => {
    setSelected(tpl)
    setNameOverride(tpl.name)
    setFolderPath('')
    setFolderFileName('')
    setPathError(null)
  }

  // 上传文件夹（对齐 KnowledgePage「上传文件夹」交互）：选择本地文件夹 → 上传到服务端暂存
  const handleUploadFolder = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files || [])
    if (!selectedFiles.length) return
    setUploadingFolder(true)
    setPathError(null)
    try {
      const result = await uploadFolder(selectedFiles)
      setFolderPath(result.folder_path)
      setFolderFileName(result.folder_name)
      setToast({
        type: 'success',
        message: `已上传文件夹，共 ${result.file_count} 个文件，可继续应用模板`,
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : '文件夹上传失败'
      setPathError(msg)
      setToast({ type: 'error', message: `文件夹上传失败：${msg}` })
    } finally {
      setUploadingFolder(false)
      e.target.value = ''
    }
  }

  // 应用模板创建 Agent
  const handleApply = async () => {
    if (!selected) return
    if (!folderPath.trim()) {
      setPathError('请先上传知识源文件夹')
      return
    }

    setApplying(true)
    setError(null)
    try {
      const result = await applyTemplate(selected.id, {
        folder_path: folderPath.trim(),
        name_override: nameOverride.trim() || undefined,
      })
      setToast({
        type: 'success',
        message: `已应用模板「${selected.name}」，创建 ${result.skills_created} 个技能`,
      })
      // 跳转至画布查看新建的 Agent
      setTimeout(() => {
        navigate(`/canvas/${result.agent_id}`)
      }, 800)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '应用模板失败'
      setToast({ type: 'error', message: msg })
    } finally {
      setApplying(false)
    }
  }

  return (
    <Layout>
      <div className="max-w-[1600px] mx-auto">
        {/* 面包屑 */}
        <nav className="mb-6 text-sm text-text-tertiary flex items-center gap-2" aria-label="面包屑">
          <button
            onClick={() => navigate('/')}
            className="hover:text-text-primary transition-colors"
          >
            控制台
          </button>
          <ChevronRight className="w-3.5 h-3.5" />
          <span className="text-text-primary font-medium">模板库</span>
        </nav>

        {/* 页面标题 */}
        <PageHeader
          title="数字员工模板库"
          subtitle="选择预置垂直岗位模板，填入知识源即可一键部署专业数字员工"
        />

        {/* 错误提示 */}
        {error && (
          <div className="mb-6 p-3 rounded-lg bg-error/10 border border-error/30 text-error text-sm flex items-center justify-between">
            <span>{error}</span>
            <button
              onClick={() => setError(null)}
              className="text-error/60 hover:text-error ml-3"
              aria-label="关闭"
            >
              ×
            </button>
          </div>
        )}

        {/* Toast 提示 */}
        <AnimatePresence>
          {toast && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className={`fixed top-20 right-6 z-50 p-4 rounded-lg shadow-lift border ${
                toast.type === 'success'
                  ? 'bg-success/10 border-success/30 text-success'
                  : toast.type === 'error'
                  ? 'bg-error/10 border-error/30 text-error'
                  : 'bg-info/10 border-info/30 text-info'
              }`}
            >
              {toast.message}
            </motion.div>
          )}
        </AnimatePresence>

        {/* 主体：双栏布局（左侧模板列表 + 右侧应用面板） */}
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-8 items-start">
          {/* 左侧：模板卡片网格 */}
          <section>
            {loading ? (
              <div className="flex justify-center items-center py-20">
                <Spinner size="lg" />
              </div>
            ) : templates.length === 0 ? (
              <div className="text-center py-20 text-text-tertiary">
                <FolderSearch className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>暂无可用模板</p>
                <p className="text-xs text-text-muted mt-1.5 max-w-md mx-auto leading-relaxed">
                  系统预置岗位模板加载为空，可能是网络波动或首次初始化未完成；
                  可重试加载，或稍后再试。
                </p>
                <div className="mt-4 flex items-center justify-center gap-3">
                  <button
                    type="button"
                    onClick={loadTemplates}
                    disabled={loading}
                    className="h-8 px-3 inline-flex items-center gap-1.5 rounded-md border border-border-default bg-surface text-body-sm text-text-secondary hover:bg-elevated transition-colors disabled:opacity-50"
                  >
                    {loading ? '加载中…' : '重试加载'}
                  </button>
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                {templates.map((tpl) => {
                  const Icon = ROLE_ICON[tpl.role] || Sparkles
                  const isSelected = selected?.id === tpl.id
                  const accent = ROLE_ACCENT[tpl.role] || 'text-brand-500'
                  return (
                    <Card
                      key={tpl.id}
                      hover
                      className={`cursor-pointer transition-all ${
                        isSelected
                          ? '!border-2 !border-brand-500 ring-2 ring-brand-500/20'
                          : ''
                      }`}
                      onClick={() => handleSelect(tpl)}
                    >
                      <CardBody>
                        {/* 头部：图标 + 角色标签 */}
                        <div className="flex items-start justify-between mb-3">
                          <div className="bg-[var(--brand-soft)] rounded-md p-2.5">
                            <Icon className={`w-5 h-5 ${accent}`} />
                          </div>
                          <span className="text-xs bg-elevated text-text-secondary rounded px-2 py-0.5">
                            {ROLE_LABEL[tpl.role] || tpl.role}
                          </span>
                        </div>

                        {/* 名称 + 描述 */}
                        <h3 className="font-serif-display text-lg font-semibold text-text-primary mb-1">
                          {tpl.name}
                        </h3>
                        <p className="text-sm text-text-secondary leading-relaxed line-clamp-2">
                          {tpl.description || '—'}
                        </p>

                        {/* 能力标签：skill_ids 数量（可点击查看技能详情） */}
                        <div className="mt-4 flex flex-wrap gap-1.5">
                          {(tpl.skill_ids || []).slice(0, 4).map((code) => (
                            <button
                              key={code}
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleSkillClick(code)
                              }}
                              className="text-xs bg-brand-50 text-brand-500 rounded-full px-2 py-0.5 hover:bg-brand-100 hover:text-brand-600 transition-colors"
                              title={`点击查看技能详情（${code}）`}
                            >
                              {skillCodeToLabel(code)}
                            </button>
                          ))}
                          {(tpl.skill_ids || []).length > 4 && (
                            <span className="text-xs text-text-tertiary">
                              +{tpl.skill_ids.length - 4}
                            </span>
                          )}
                        </div>

                        {/* 选中标识 */}
                        {isSelected && (
                          <div className="mt-4 flex items-center gap-1.5 text-xs text-brand-500">
                            <Check className="w-3.5 h-3.5" />
                            <span>已选择，请在右侧配置并应用</span>
                          </div>
                        )}
                      </CardBody>
                    </Card>
                  )
                })}
              </div>
            )}
          </section>

          {/* 右侧：应用面板 */}
          <aside className="lg:sticky lg:top-24">
            <Card>
              <CardBody>
                {!selected ? (
                  <div className="text-center py-12">
                    <Sparkles className="w-10 h-10 mx-auto mb-3 text-text-tertiary opacity-50" />
                    <p className="text-sm text-text-tertiary">
                      从左侧选择一个模板
                    </p>
                    <p className="text-xs text-text-tertiary mt-1">
                      选择后可在此配置知识源并一键应用
                    </p>
                  </div>
                ) : (
                  <div>
                    {/* 模板信息 */}
                    <div className="mb-5 pb-4 border-b border-border-subtle">
                      <div className="text-xs text-text-tertiary mb-1">
                        已选模板
                      </div>
                      <h3 className="text-lg font-semibold text-text-primary">
                        {selected.name}
                      </h3>
                      <p className="text-xs text-text-secondary mt-1 leading-relaxed">
                        {selected.description}
                      </p>
                    </div>

                    {/* 知识源文件夹（上传文件夹，对齐 KnowledgePage 交互，修复 FOLDER_PATH_INVALID） */}
                    <div className="mb-4">
                      <label className="block text-sm font-medium text-text-primary mb-1.5">
                        知识源文件夹
                      </label>
                      <input
                        ref={folderInputRef}
                        type="file"
                        className="hidden"
                        onChange={handleUploadFolder}
                        disabled={uploadingFolder}
                        {...{ webkitdirectory: 'true', directory: '' }}
                      />
                      <Button
                        variant="outline"
                        size="md"
                        className="w-full"
                        onClick={() => folderInputRef.current?.click()}
                        disabled={uploadingFolder}
                      >
                        {uploadingFolder ? (
                          <>
                            <Loader2 className="w-4 h-4 animate-spin" />
                            上传中...
                          </>
                        ) : (
                          <>
                            <FolderUp className="w-4 h-4" />
                            {folderFileName ? `已选择：${folderFileName}` : '上传文件夹'}
                          </>
                        )}
                      </Button>
                      {folderPath && !uploadingFolder && (
                        <p className="mt-1.5 text-xs text-success flex items-center gap-1">
                          <Check className="w-3 h-3" />
                          已上传，可直接应用模板
                        </p>
                      )}
                      {pathError && (
                        <p className="mt-1.5 text-xs text-error">{pathError}</p>
                      )}
                      <p className="mt-1.5 text-xs text-text-tertiary">
                        选择本地知识文档文件夹，系统将上传并扫描为知识源
                      </p>
                    </div>

                    {/* 名称覆盖 */}
                    <div className="mb-4">
                      <label className="block text-sm font-medium text-text-primary mb-1.5">
                        Agent 名称（可覆盖）
                      </label>
                      <input
                        type="text"
                        value={nameOverride}
                        onChange={(e) => setNameOverride(e.target.value)}
                        placeholder={selected.name}
                        className="w-full bg-surface-2 border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 outline-none transition-colors"
                      />
                    </div>

                    {/* 模板包含的能力 */}
                    <div className="mb-5 p-3 bg-elevated rounded-md">
                      <div className="text-xs font-semibold text-text-tertiary mb-2">
                        模板包含能力（{selected.skill_ids?.length || 0} 项）
                      </div>
                      <div className="space-y-1">
                        {(selected.skill_ids || []).map((code) => (
                          <div
                            key={code}
                            className="text-xs text-text-secondary"
                            title={code}
                          >
                            · {skillCodeToLabel(code)}
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* 操作按钮 */}
                    <div className="flex flex-col gap-2">
                      <Button
                        variant="primary"
                        size="lg"
                        onClick={handleApply}
                        disabled={applying || uploadingFolder || !folderPath.trim()}
                      >
                        {applying ? (
                          <>
                            <Spinner size="sm" className="text-white" />
                            应用中...
                          </>
                        ) : (
                          <>
                            <Sparkles className="w-4 h-4" />
                            应用模板并创建
                          </>
                        )}
                      </Button>
                      <Button
                        variant="outline"
                        size="md"
                        onClick={handleOpenSaveAs}
                        disabled={applying}
                        title="将当前模板配置保存为企业私有模板，便于团队复用"
                      >
                        <Save className="w-4 h-4" />
                        另存为私有模板
                      </Button>
                      <Button
                        variant="ghost"
                        size="md"
                        onClick={() => setSelected(null)}
                        disabled={applying}
                      >
                        取消选择
                      </Button>
                    </div>

                    {/* 提示 */}
                    <div className="mt-4 p-3 bg-info/5 border border-info/20 rounded-md text-xs text-text-secondary leading-relaxed">
                      <p className="mb-1">
                        应用后将执行以下步骤：
                      </p>
                      <ol className="list-decimal ml-4 space-y-0.5">
                        <li>扫描文件夹并解析为知识片段</li>
                        <li>向量化写入 ChromaDB</li>
                        <li>预填模板中文专业 system_prompt</li>
                        <li>实例化模板技能并链式编排</li>
                      </ol>
                    </div>
                  </div>
                )}
              </CardBody>
            </Card>

            {/* 返回入口 */}
            <div className="mt-4">
              <Button
                variant="ghost"
                size="md"
                onClick={() => navigate('/setup')}
              >
                <ArrowLeft className="w-4 h-4" />
                改用自定义向导
              </Button>
            </div>
          </aside>
        </div>

        {/* ========== 技能详情对话框（激活 GET /templates/skill-templates 的消费侧） ========== */}
        {skillDetail && (
          <ModalShell
            open={Boolean(skillDetail)}
            onClose={() => setSkillDetail(null)}
            labelledBy="skill-detail-title"
            overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
            panelClassName="bg-surface border border-border-default rounded-xl shadow-xl max-w-lg w-full max-h-[85vh] overflow-hidden flex flex-col"
          >
            <>
              <div className="flex items-center justify-between px-5 py-4 border-b border-border-default">
                <div className="flex items-center gap-2">
                  <BookOpen className="w-5 h-5 text-brand-500" />
                  <h2 id="skill-detail-title" className="font-serif-display text-base font-semibold text-text-primary">
                    技能详情
                  </h2>
                </div>
                <button
                  onClick={() => setSkillDetail(null)}
                  className="text-text-tertiary hover:text-text-primary transition-colors"
                  aria-label="关闭"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="px-5 py-4 overflow-y-auto flex-1 space-y-3">
                <div>
                  <div className="text-xs text-text-tertiary mb-0.5">技能编码</div>
                  <div className="text-sm font-mono text-text-primary">{skillDetail.code}</div>
                </div>
                <div>
                  <div className="text-xs text-text-tertiary mb-0.5">名称</div>
                  <div className="text-sm font-medium text-text-primary">{skillDetail.name}</div>
                </div>
                <div>
                  <div className="text-xs text-text-tertiary mb-0.5">类型</div>
                  <div className="text-sm text-text-primary">{skillDetail.skill_type}</div>
                </div>
                <div>
                  <div className="text-xs text-text-tertiary mb-0.5">描述</div>
                  <div className="text-sm text-text-secondary leading-relaxed">
                    {skillDetail.description || '—'}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-text-tertiary mb-0.5">配置</div>
                  <pre className="text-xs bg-surface-2 border border-border-subtle rounded p-2.5 overflow-x-auto font-mono text-text-secondary">
                    {JSON.stringify(skillDetail.config, null, 2)}
                  </pre>
                </div>
                <div>
                  <div className="text-xs text-text-tertiary mb-0.5">输出 schema</div>
                  <pre className="text-xs bg-surface-2 border border-border-subtle rounded p-2.5 overflow-x-auto font-mono text-text-secondary">
                    {JSON.stringify(skillDetail.output_schema, null, 2)}
                  </pre>
                </div>
              </div>
              <div className="px-5 py-3 border-t border-border-default bg-surface-2 flex justify-end">
                <Button variant="outline" onClick={() => setSkillDetail(null)}>
                  关闭
                </Button>
              </div>
            </>
          </ModalShell>
        )}

        {/* ========== 另存为私有模板对话框（激活 POST /templates） ========== */}
        {saveAsOpen && saveAsForm && (
          <ModalShell
            open={Boolean(saveAsOpen && saveAsForm)}
            onClose={() => setSaveAsOpen(false)}
            labelledBy="save-as-title"
            overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
            panelClassName="bg-surface border border-border-default rounded-xl shadow-xl max-w-lg w-full max-h-[85vh] overflow-hidden flex flex-col"
            closeOnOverlayClick={!savingAs}
            closeOnEscape={!savingAs}
          >
            <>
              <div className="flex items-center justify-between px-5 py-4 border-b border-border-default">
                <div className="flex items-center gap-2">
                  <Save className="w-5 h-5 text-brand-500" />
                  <h2 id="save-as-title" className="font-serif-display text-base font-semibold text-text-primary">
                    另存为私有模板
                  </h2>
                </div>
                <button
                  onClick={() => !savingAs && setSaveAsOpen(false)}
                  className="text-text-tertiary hover:text-text-primary transition-colors disabled:opacity-50"
                  aria-label="关闭"
                  disabled={savingAs}
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="px-5 py-4 overflow-y-auto flex-1 space-y-3">
                <div>
                  <label htmlFor="save-as-name" className="block text-xs font-medium text-text-primary mb-1.5">名称</label>
                  <input
                    id="save-as-name"
                    type="text"
                    value={saveAsForm.name}
                    onChange={(e) => setSaveAsForm({ ...saveAsForm, name: e.target.value })}
                    className="w-full bg-surface-2 border border-border-default rounded-md px-3 py-2 text-sm text-text-primary focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 outline-none transition-colors"
                    maxLength={255}
                    disabled={savingAs}
                  />
                </div>
                <div>
                  <label htmlFor="save-as-description" className="block text-xs font-medium text-text-primary mb-1.5">描述</label>
                  <textarea
                    id="save-as-description"
                    value={saveAsForm.description || ''}
                    onChange={(e) => setSaveAsForm({ ...saveAsForm, description: e.target.value })}
                    rows={2}
                    className="w-full bg-surface-2 border border-border-default rounded-md px-3 py-2 text-sm text-text-primary focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 outline-none transition-colors resize-none"
                    disabled={savingAs}
                  />
                </div>
                <div>
                  <label htmlFor="save-as-system-prompt" className="block text-xs font-medium text-text-primary mb-1.5">系统提示词</label>
                  <textarea
                    id="save-as-system-prompt"
                    value={saveAsForm.system_prompt}
                    onChange={(e) => setSaveAsForm({ ...saveAsForm, system_prompt: e.target.value })}
                    rows={6}
                    className="w-full bg-surface-2 border border-border-default rounded-md px-3 py-2 text-xs text-text-primary font-mono focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 outline-none transition-colors resize-none"
                    disabled={savingAs}
                  />
                </div>
                <div className="bg-info/5 border border-info/20 rounded-md p-3 text-xs text-text-secondary leading-relaxed">
                  <p>
                    保存后将创建一个 <span className="font-medium">企业私有模板</span>（is_preset=false），
                    仅您所在企业可见，可用于团队复用与定制化。
                  </p>
                </div>
              </div>
              <div className="px-5 py-3 border-t border-border-default bg-surface-2 flex justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => setSaveAsOpen(false)}
                  disabled={savingAs}
                >
                  取消
                </Button>
                <Button
                  variant="primary"
                  onClick={handleSaveAs}
                  disabled={savingAs || !saveAsForm.name.trim() || !saveAsForm.system_prompt.trim()}
                >
                  {savingAs ? (
                    <>
                      <Spinner size="sm" className="text-white" />
                      保存中...
                    </>
                  ) : (
                    <>
                      <Save className="w-4 h-4" />
                      保存
                    </>
                  )}
                </Button>
              </div>
            </>
          </ModalShell>
        )}
      </div>
    </Layout>
  )
}
