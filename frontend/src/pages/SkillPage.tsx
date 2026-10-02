import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  ChevronRight,
  Tag,
  HelpCircle,
  Smile,
  Database,
  Mail,
  FileText,
  Calendar,
  Webhook,
  Search,
  Zap,
  Wrench,
  Code,
  Play,
  Clock,
  CheckCircle2,
  TrendingUp,
  ArrowRight,
  Download,
  type LucideIcon,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { getSkill, getSkills, executeSkill, SkillExecutionResult } from '@/api/skills'
import { Spinner } from '@/components/ui/Spinner'
import { Button } from '@/components/ui/Button'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skill } from '@/types'

// ============================================================
// 类型定义
// ============================================================
interface SkillParam {
  name: string
  type: string
  description: string
  required: boolean
}

interface SkillExample {
  title: string
  description?: string
  input: string
  output: string
}

interface WorkflowNode {
  id: string
  label: string
  icon: LucideIcon
  active?: boolean
}

interface SkillStats {
  usageCount: number
  successRate: number
  avgDurationMs: number
  enabled: boolean
}

// ============================================================
// 图标映射：技能名称 → lucide 图标
// ============================================================
const SKILL_ICON_MAP: Record<string, LucideIcon> = {
  '工单分类': Tag,
  'FAQ匹配': HelpCircle,
  'FAQ 匹配': HelpCircle,
  '情感分析': Smile,
  '数据查询': Database,
  '邮件起草': Mail,
  '文档摘要': FileText,
  '日程管理': Calendar,
  'API调用': Webhook,
  'API 调用': Webhook,
  '知识检索': Search,
}

function getSkillIcon(name: string, skillType: string): LucideIcon {
  if (SKILL_ICON_MAP[name]) return SKILL_ICON_MAP[name]
  const t = (skillType || '').toLowerCase()
  if (t.includes('code') || t.includes('api') || t.includes('script')) return Code
  if (t.includes('tool') || t.includes('wrench') || t.includes('helper')) return Wrench
  if (t.includes('search') || t.includes('retrieve')) return Search
  if (t.includes('doc') || t.includes('text')) return FileText
  return Zap
}

// ============================================================
// 从 skill.config 提取参数定义（未定义时回退到 input_type/output_type）
// ============================================================
function extractParams(
  config: Record<string, unknown> | null,
  type: 'input' | 'output'
): SkillParam[] {
  if (!config) return []
  const key = type === 'input' ? 'input_params' : 'output_params'
  const altKey = type === 'input' ? 'inputs' : 'outputs'
  const raw = config[key] ?? config[altKey]
  if (!Array.isArray(raw)) return []
  return raw
    .filter((p): p is Record<string, unknown> => typeof p === 'object' && p !== null)
    .map((p) => {
      const name = p.name
      const ptype = p.type
      const desc = p.description ?? p.desc
      return {
        name: typeof name === 'string' ? name : 'param',
        type: typeof ptype === 'string' ? ptype : 'string',
        description: typeof desc === 'string' ? desc : '',
        required: p.required === true,
      }
    })
}

// ============================================================
// 提取使用示例（config 无定义时给出基于技能 ID 的默认示例）
// ============================================================
function extractExamples(skill: Skill): SkillExample[] {
  const config = skill.config
  if (config) {
    const raw = config.examples ?? config.use_cases ?? config.cases
    if (Array.isArray(raw)) {
      const mapped = raw
        .filter((e): e is Record<string, unknown> => typeof e === 'object' && e !== null)
        .map((e) => {
          const title = e.title
          const description = e.description
          const input = e.input
          const output = e.output
          return {
            title: typeof title === 'string' ? title : '使用示例',
            description: typeof description === 'string' ? description : undefined,
            input: typeof input === 'string' ? input : JSON.stringify(input ?? '', null, 2),
            output: typeof output === 'string' ? output : JSON.stringify(output ?? '', null, 2),
          }
        })
      if (mapped.length > 0) return mapped
    }
  }
  return defaultExamples(skill)
}

function defaultExamples(skill: Skill): SkillExample[] {
  return [
    {
      title: '基础调用',
      description: '最常见的单次调用场景，传入文本并获取结构化结果。',
      input: `{\n  "skill_id": "${skill.id}",\n  "input_data": {\n    "text": "请处理这段内容"\n  }\n}`,
      output: `{\n  "skill_id": "${skill.id}",\n  "output_data": {\n    "result": "处理完成",\n    "confidence": 0.95\n  },\n  "execution_time_ms": 320\n}`,
    },
    {
      title: '批量场景',
      description: '一次性传入多条内容，技能返回批量处理结果。',
      input: `{\n  "skill_id": "${skill.id}",\n  "input_data": {\n    "items": ["内容一", "内容二", "内容三"]\n  }\n}`,
      output: `{\n  "skill_id": "${skill.id}",\n  "output_data": {\n    "results": [...],\n    "total": 3,\n    "success": 3\n  },\n  "execution_time_ms": 980\n}`,
    },
    {
      title: '集成调用',
      description: '在智能体流程中作为子步骤被调用，输出供下游节点消费。',
      input: `{\n  "agent_id": "${skill.agent_id}",\n  "step": "skill:${skill.id}",\n  "context": { "session": "conv_abc123" }\n}`,
      output: `{\n  "step": "skill:${skill.id}",\n  "status": "ok",\n  "payload": { ... },\n  "next": "response.compose"\n}`,
    },
  ]
}

// ============================================================
// 构建工作流节点（优先用 config.workflow_steps，否则 3 步默认流程）
// ============================================================
function buildWorkflowNodes(skill: Skill): WorkflowNode[] {
  const config = skill.config
  let steps: { id: string; label: string }[] | null = null
  if (config && Array.isArray(config.workflow_steps)) {
    const mapped = (config.workflow_steps as unknown[])
      .filter((s): s is Record<string, unknown> => typeof s === 'object' && s !== null)
      .map((s, i) => {
        const id = s.id
        const label = s.label ?? s.name
        return {
          id: typeof id === 'string' ? id : `step-${i}`,
          label: typeof label === 'string' ? label : `步骤 ${i + 1}`,
        }
      })
    if (mapped.length > 0) steps = mapped
  }
  if (!steps) {
    steps = [
      { id: 'input', label: '接收输入' },
      { id: 'process', label: `${skill.name || '技能'}处理` },
      { id: 'output', label: '返回结果' },
    ]
  }
  const icons: LucideIcon[] = [ArrowRight, Zap, CheckCircle2, Code, Wrench, Database]
  const midIndex = Math.floor((steps.length - 1) / 2)
  return steps.map((s, i) => ({
    id: s.id,
    label: s.label,
    icon: icons[i % icons.length],
    active: i === midIndex,
  }))
}

// ============================================================
// 统计数据：优先 config 字段，否则基于 ID 哈希的稳定占位值
// ============================================================
function getSkillStats(skill: Skill): SkillStats {
  const config = skill.config
  if (config) {
    const uc = config.usage_count
    const sr = config.success_rate
    const ad = config.avg_duration_ms
    const en = config.enabled
    if (
      typeof uc === 'number' ||
      typeof sr === 'number' ||
      typeof ad === 'number' ||
      typeof en === 'boolean'
    ) {
      return {
        usageCount: typeof uc === 'number' ? uc : 0,
        successRate: typeof sr === 'number' ? sr : 0,
        avgDurationMs: typeof ad === 'number' ? ad : 0,
        enabled: en === true,
      }
    }
  }
  const hash = hashString(skill.id)
  return {
    usageCount: 100 + (hash % 5000),
    successRate: 92 + (hash % 8),
    avgDurationMs: 180 + (hash % 600),
    enabled: (hash % 3) !== 0,
  }
}

function hashString(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0
  }
  return Math.abs(h)
}

function formatNumber(n: number): string {
  return n.toLocaleString('zh-CN')
}

// ============================================================
// 主组件
// ============================================================
export default function SkillPage() {
  const { skillId } = useParams<{ skillId: string }>()
  const navigate = useNavigate()
  const [skill, setSkill] = useState<Skill | null>(null)
  const [relatedSkills, setRelatedSkills] = useState<Skill[]>([])
  const [input, setInput] = useState('')
  const [result, setResult] = useState<SkillExecutionResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [executing, setExecuting] = useState(false)
  const [error, setError] = useState('')

  const loadSkill = useCallback(async () => {
    if (!skillId) return
    try {
      setLoading(true)
      const skillData = await getSkill(skillId)
      setSkill(skillData)
      // 加载相关技能（同类型优先，不足时其他类型补足）
      try {
        const all = await getSkills()
        const related = all
          .filter((s) => s.id !== skillId && s.skill_type === skillData.skill_type)
          .slice(0, 4)
        if (related.length < 4) {
          const others = all
            .filter((s) => s.id !== skillId && !related.some((r) => r.id === s.id))
            .slice(0, 4 - related.length)
          related.push(...others)
        }
        setRelatedSkills(related.slice(0, 4))
      } catch {
        // 相关技能加载失败不阻塞主流程
      }
    } catch (err) {
      console.error('加载技能失败:', err)
      setError('加载技能失败')
    } finally {
      setLoading(false)
    }
  }, [skillId])

  useEffect(() => {
    loadSkill()
  }, [loadSkill])

  const handleExecute = async () => {
    if (!skill || !input.trim()) {
      setError('请输入内容')
      return
    }
    try {
      setExecuting(true)
      setError('')
      setResult(null)
      const resultData = await executeSkill(skill.id, { text: input })
      setResult(resultData)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '执行失败，请重试')
    } finally {
      setExecuting(false)
    }
  }

  // 派生数据
  const inputParams = useMemo<SkillParam[]>(
    () => (skill ? extractParams(skill.config, 'input') : []),
    [skill]
  )
  const outputParams = useMemo<SkillParam[]>(
    () => (skill ? extractParams(skill.config, 'output') : []),
    [skill]
  )
  const examples = useMemo<SkillExample[]>(() => (skill ? extractExamples(skill) : []), [skill])
  const workflowNodes = useMemo<WorkflowNode[]>(
    () => (skill ? buildWorkflowNodes(skill) : []),
    [skill]
  )
  const stats = useMemo<SkillStats>(
    () =>
      skill
        ? getSkillStats(skill)
        : { usageCount: 0, successRate: 0, avgDurationMs: 0, enabled: false },
    [skill]
  )
  const SkillIcon = skill ? getSkillIcon(skill.name, skill.skill_type) : Zap

  // ===== 加载态 =====
  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <Spinner size="lg" className="text-brand-500" />
        </div>
      </Layout>
    )
  }

  // ===== 错误态 =====
  if (error && !skill) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="text-center">
            <h2 className="text-xl font-semibold text-text-primary mb-2">加载失败</h2>
            <p className="text-text-secondary mb-4">{error}</p>
            <Button onClick={() => navigate('/')}>返回首页</Button>
          </div>
        </div>
      </Layout>
    )
  }

  // ===== 不存在态 =====
  if (!skill) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="text-center">
            <h2 className="text-xl font-semibold text-text-primary mb-2">技能不存在</h2>
            <Button onClick={() => navigate('/')}>返回首页</Button>
          </div>
        </div>
      </Layout>
    )
  }

  // ===== 主渲染 =====
  return (
    <Layout>
      <div className="max-w-[1600px] mx-auto space-y-8">
        {/* 1. 页面头部：面包屑 + 衬线标题 + brand-rule + 副标题 + 使用按钮 */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <nav className="flex items-center text-sm text-text-tertiary mb-2" aria-label="面包屑">
            <Link to="/" className="hover:text-brand-500 transition-colors">
              控制台
            </Link>
            <ChevronRight className="w-4 h-4 mx-1 text-text-tertiary shrink-0" />
            <Link to="/agents?tab=knowledge" className="hover:text-brand-500 transition-colors">
              技能市场
            </Link>
            <ChevronRight className="w-4 h-4 mx-1 text-text-tertiary shrink-0" />
            <span className="text-text-secondary truncate">{skill.name}</span>
          </nav>
          <PageHeader
            title={skill.name}
            subtitle={skill.description || '暂无描述'}
            actions={
              <Link
                to="/setup"
                className="inline-flex items-center justify-center gap-2 h-12 px-6 text-body rounded-lg font-medium bg-brand-500 text-white hover:bg-brand-600 transition-colors shrink-0"
              >
                <Play className="w-4 h-4" />
                使用此技能
              </Link>
            }
          />
        </motion.div>

        {/* 2. 技能概览卡片（顶部全宽） */}
        <motion.section
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
          className="bg-surface rounded-xl border border-border-default shadow-soft p-6"
        >
          <div className="flex items-start gap-5 flex-wrap">
            {/* 技能图标：大尺寸，品牌色背景圆角方块 */}
            <div className="w-16 h-16 rounded-xl bg-brand-500 flex items-center justify-center text-white shrink-0">
              <SkillIcon className="w-8 h-8" />
            </div>
            <div className="flex-1 min-w-[240px]">
              <div className="flex items-center gap-3 flex-wrap">
                <h2 className="font-serif-display text-2xl font-bold text-text-primary">
                  {skill.name}
                </h2>
                <span className="inline-block bg-brand-50 text-brand-500 rounded px-2 py-0.5 text-xs font-medium">
                  {skill.skill_type}
                </span>
                <span
                  className={`inline-flex items-center gap-1.5 text-xs ${
                    stats.enabled ? 'text-success' : 'text-text-tertiary'
                  }`}
                >
                  <span className={`dot ${stats.enabled ? 'dot-success' : 'dot-muted'}`} />
                  {stats.enabled ? '已启用' : '未启用'}
                </span>
              </div>
              <p className="text-sm text-text-secondary mt-2 leading-relaxed">
                {skill.description || '暂无描述'}
              </p>
              {/* 统计指标 */}
              <div className="flex items-center gap-6 mt-4 flex-wrap">
                <div className="flex items-center gap-2">
                  <TrendingUp className="w-4 h-4 text-text-tertiary" />
                  <span className="text-xs text-text-tertiary">使用次数</span>
                  <span className="font-mono tabular-nums text-sm text-text-primary font-medium">
                    {formatNumber(stats.usageCount)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-text-tertiary" />
                  <span className="text-xs text-text-tertiary">成功率</span>
                  <span className="font-mono tabular-nums text-sm text-text-primary font-medium">
                    {stats.successRate}%
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Clock className="w-4 h-4 text-text-tertiary" />
                  <span className="text-xs text-text-tertiary">平均耗时</span>
                  <span className="font-mono tabular-nums text-sm text-text-primary font-medium">
                    {stats.avgDurationMs}ms
                  </span>
                </div>
              </div>
            </div>
          </div>
        </motion.section>

        {/* 3 & 4. 配置参数（左） + 技能流程图（右） */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* 3. 配置参数面板 */}
          <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.1 }}
            className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
          >
            <div className="flex items-center justify-between mb-5">
              <h3 className="font-serif-display text-lg font-semibold text-text-primary">
                配置参数
              </h3>
              <span className="text-xs text-text-tertiary font-mono">
                {inputParams.length + outputParams.length} 项
              </span>
            </div>

            {/* 输入参数 */}
            <div className="mb-6">
              <div className="flex items-center gap-2 mb-3">
                <ArrowRight className="w-4 h-4 text-brand-500" />
                <h4 className="text-sm font-semibold text-text-primary">输入参数</h4>
                <span className="text-xs text-text-tertiary">({inputParams.length})</span>
              </div>
              {inputParams.length > 0 ? (
                <ul className="space-y-3">
                  {inputParams.map((p, i) => (
                    <li
                      key={`${p.name}-${i}`}
                      className="border-l-2 border-border-default pl-3 py-1"
                    >
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-text-primary">{p.name}</span>
                        <span className="font-mono text-xs text-text-tertiary bg-elevated rounded px-1.5 py-0.5">
                          {p.type}
                        </span>
                        {p.required && <span className="text-xs text-error">必填</span>}
                      </div>
                      {p.description && (
                        <p className="text-xs text-text-tertiary mt-1 leading-relaxed">
                          {p.description}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-xs text-text-tertiary italic">
                  {skill.input_type
                    ? `输入类型：${skill.input_type}`
                    : '未定义显式输入参数，默认接收文本输入。'}
                </div>
              )}
            </div>

            {/* 输出参数 */}
            <div>
              <div className="flex items-center gap-2 mb-3">
                <CheckCircle2 className="w-4 h-4 text-success" />
                <h4 className="text-sm font-semibold text-text-primary">输出参数</h4>
                <span className="text-xs text-text-tertiary">({outputParams.length})</span>
              </div>
              {outputParams.length > 0 ? (
                <ul className="space-y-3">
                  {outputParams.map((p, i) => (
                    <li
                      key={`${p.name}-${i}`}
                      className="border-l-2 border-border-default pl-3 py-1"
                    >
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-text-primary">{p.name}</span>
                        <span className="font-mono text-xs text-text-tertiary bg-elevated rounded px-1.5 py-0.5">
                          {p.type}
                        </span>
                      </div>
                      {p.description && (
                        <p className="text-xs text-text-tertiary mt-1 leading-relaxed">
                          {p.description}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-xs text-text-tertiary italic">
                  {skill.output_type
                    ? `输出类型：${skill.output_type}`
                    : '未定义显式输出参数，默认返回 JSON 结果。'}
                </div>
              )}
            </div>
          </motion.section>

          {/* 4. 技能流程图（方案C元素） */}
          <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.15 }}
            className="bg-surface-2 rounded-xl p-6"
          >
            <div className="flex items-center justify-between mb-5">
              <h3 className="font-serif-display text-lg font-semibold text-text-primary">
                执行流程
              </h3>
              <span className="inline-flex items-center gap-1.5 text-xs text-text-tertiary">
                <span className="dot dot-info" />
                实时模拟
              </span>
            </div>

            <SkillWorkflow nodes={workflowNodes} />

            <div className="mt-5 pt-4 border-t border-border-subtle">
              <p className="text-xs text-text-tertiary leading-relaxed">
                该流程展示技能从接收输入到返回结果的核心步骤。
                {workflowNodes.find((n) => n.active) && (
                  <>
                    当前{' '}
                    <span className="text-brand-500 font-medium">
                      「{workflowNodes.find((n) => n.active)?.label}」
                    </span>
                    为活跃节点。
                  </>
                )}
              </p>
            </div>
          </motion.section>
        </div>

        {/* 5. 使用示例区（底部全宽） */}
        <motion.section
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.2 }}
        >
          <div className="flex items-center justify-between mb-4">
            <div className="space-y-1">
              <h3 className="font-serif-display text-lg font-semibold text-text-primary">
                使用示例
              </h3>
              <div className="brand-rule" />
            </div>
            <span className="text-xs text-text-tertiary">{examples.length} 个场景</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {examples.map((ex, i) => (
              <div
                key={i}
                className="bg-surface rounded-xl border border-border-default shadow-soft p-5 flex flex-col"
              >
                <h4 className="text-sm font-semibold text-text-primary mb-2">{ex.title}</h4>
                {ex.description && (
                  <p className="text-xs text-text-tertiary mb-3 leading-relaxed">
                    {ex.description}
                  </p>
                )}
                <div className="space-y-3 flex-1">
                  <div>
                    <div className="text-xs text-text-tertiary mb-1.5 flex items-center gap-1">
                      <ArrowRight className="w-3 h-3" /> 输入
                    </div>
                    <pre className="bg-[#0F1419] rounded-lg p-4 font-mono text-xs text-text-secondary overflow-x-auto scrollbar-thin">
                      {ex.input}
                    </pre>
                  </div>
                  <div>
                    <div className="text-xs text-text-tertiary mb-1.5 flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" /> 预期输出
                    </div>
                    <pre className="bg-[#0F1419] rounded-lg p-4 font-mono text-xs text-text-secondary overflow-x-auto scrollbar-thin">
                      {ex.output}
                    </pre>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </motion.section>

        {/* 6. 在线试用（保留原有 executeSkill 功能） */}
        <motion.section
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.25 }}
          className="bg-surface border border-border-default rounded-xl shadow-soft p-6"
        >
          <div className="flex items-center justify-between mb-4">
            <div className="space-y-1">
              <h3 className="font-serif-display text-lg font-semibold text-text-primary">
                在线试用
              </h3>
              <p className="text-xs text-text-tertiary">输入内容并执行，查看实际返回结果</p>
            </div>
          </div>

          {error && (
            <div className="mb-4 p-3 bg-error/5 border border-error/20 rounded-lg text-error text-sm">
              {error}
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div>
              <label
                htmlFor="skill-input"
                className="block text-sm font-medium text-text-secondary mb-2"
              >
                输入文本
              </label>
              <textarea
                id="skill-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="请输入要处理的文本内容..."
                rows={6}
                className="w-full px-4 py-3 bg-elevated border border-border-default rounded-lg text-text-primary focus:border-brand-500 placeholder-text-tertiary resize-none font-mono text-sm"
              />
              <Button
                onClick={handleExecute}
                disabled={executing || !input.trim()}
                className="w-full mt-3"
                size="lg"
              >
                {executing ? (
                  <>
                    <Spinner size="sm" className="text-white" />
                    执行中...
                  </>
                ) : (
                  <>
                    <Play className="w-4 h-4" />
                    执行
                  </>
                )}
              </Button>
            </div>
            <div>
              <div className="text-sm font-medium text-text-secondary mb-2">执行结果</div>
              {result ? (
                <div className="space-y-3">
                  <div className="p-3 bg-success/5 border border-success/20 rounded-lg">
                    <div className="flex items-center gap-2 text-success">
                      <CheckCircle2 className="w-4 h-4" />
                      <span className="text-sm font-medium">执行成功</span>
                    </div>
                    <p className="text-xs text-success mt-1 font-mono">
                      耗时: {result.execution_time_ms}ms
                    </p>
                  </div>
                  <pre className="bg-[#0F1419] rounded-lg p-4 font-mono text-xs text-text-secondary overflow-x-auto scrollbar-thin max-h-64">
                    {JSON.stringify(result.output_data, null, 2)}
                  </pre>
                </div>
              ) : (
                <div className="flex items-center justify-center h-48 text-text-tertiary border border-dashed border-border-default rounded-lg">
                  <div className="text-center">
                    <Play className="w-8 h-8 mx-auto mb-2 opacity-40" />
                    <p className="text-xs">输入内容并点击执行</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </motion.section>

        {/* 7. 相关技能推荐 */}
        {relatedSkills.length > 0 && (
          <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.3 }}
          >
            <div className="flex items-center justify-between mb-4">
              <div className="space-y-1">
                <h3 className="font-serif-display text-lg font-semibold text-text-primary">
                  相关技能推荐
                </h3>
                <div className="brand-rule" />
              </div>
              <Link
                to="/agents?tab=knowledge"
                className="text-sm text-brand-500 hover:underline inline-flex items-center gap-1"
              >
                查看全部 <ChevronRight className="w-4 h-4" />
              </Link>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
              {relatedSkills.map((rel, index) => {
                const RelIcon = getSkillIcon(rel.name, rel.skill_type)
                // 模拟第一项为已启用技能（画布：已启用卡片右上角显示 dot-success + "已启用"）
                const enabled = index === 0
                const usageCount = formatNumber(100 + (hashString(rel.id) % 5000))
                return (
                  <Link
                    key={rel.id}
                    to={`/skill/${rel.id}`}
                    className="bg-surface rounded-lg border border-border-default p-5 shadow-soft hover:shadow-lift transition-shadow flex flex-col group"
                  >
                    <div className="flex items-start justify-between">
                      <div className="bg-[var(--brand-soft)] text-brand-500 rounded-md p-2.5">
                        <RelIcon className="w-5 h-5" />
                      </div>
                      {enabled && (
                        <span className="inline-flex items-center gap-1.5 text-xs text-success">
                          <span className="dot dot-success" />
                          已启用
                        </span>
                      )}
                    </div>
                    <h4 className="font-semibold text-base mt-3 text-text-primary group-hover:text-brand-500 transition-colors">
                      {rel.name}
                    </h4>
                    <span className="inline-block bg-elevated text-text-tertiary rounded px-2 py-0.5 text-xs mt-1 w-fit">
                      {rel.skill_type}
                    </span>
                    <p className="text-sm text-text-tertiary mt-2 leading-relaxed line-clamp-2 flex-1">
                      {rel.description || '暂无描述'}
                    </p>
                    {/* 画布 Footer：border-t 分隔的使用次数 + 管理操作/安装按钮 */}
                    <div className="flex items-center justify-between mt-auto pt-4 border-t border-border-default">
                      <span className="font-mono text-xs text-text-tertiary">
                        使用 {usageCount} 次
                      </span>
                      {enabled ? (
                        <span className="text-sm text-brand-500 hover:underline">管理</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 bg-brand-500 text-white rounded-md px-3 py-1.5 text-sm hover:bg-brand-600 transition-colors">
                          <Download className="w-3.5 h-3.5" />
                          安装
                        </span>
                      )}
                    </div>
                  </Link>
                )
              })}
            </div>
          </motion.section>
        )}
      </div>
    </Layout>
  )
}

// ============================================================
// 技能流程图子组件：HTML 节点（圆角矩形 + 图标） + SVG 流动虚线连线
// ============================================================
function SkillWorkflow({ nodes }: { nodes: WorkflowNode[] }) {
  if (nodes.length === 0) return null
  return (
    <div className="w-full overflow-x-auto scrollbar-thin">
      <div className="flex items-center min-w-fit py-2">
        {nodes.map((node, i) => {
          const Icon = node.icon
          return (
            <div key={node.id} className="flex items-center">
              <div
                className={`flex flex-col items-center justify-center gap-1.5 rounded-xl border bg-surface px-4 py-3 min-w-[120px] transition-all ${
                  node.active
                    ? 'border-brand-500 text-brand-500 shadow-soft wf-node-active'
                    : 'border-border-default text-text-secondary'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span className="text-xs font-medium text-center leading-tight">{node.label}</span>
              </div>
              {i < nodes.length - 1 && (
                <svg
                  width="48"
                  height="12"
                  className="shrink-0 mx-1"
                  aria-hidden="true"
                >
                  <line
                    x1="0"
                    y1="6"
                    x2="48"
                    y2="6"
                    stroke="var(--brand-lighter, #6B8CB1)"
                    strokeWidth="2"
                    className="flow-dash"
                  />
                </svg>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
