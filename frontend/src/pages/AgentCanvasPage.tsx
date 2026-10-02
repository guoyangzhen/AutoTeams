/**
 * AgentCanvasPage · LangGraph 构建状态可视化页面（P0-1c + S1）
 *
 * 重构说明：
 * 之前是假的"可视化工作流编辑器"（hardcoded 6 节点 + mock 保存/调试），
 * 与后端 LangGraph 7 节点状态机完全脱节。
 *
 * 现降级为只读状态可视化：
 * - 接受 /canvas/:threadId 路由参数，拉取 GET /agents/build/{thread_id}/state
 * - 展示真实的 LangGraph 7 节点流水线（planner→scanner→approval→parser→vectorizer→builder→tester）
 * - 节点状态由后端 current_step / status 派生，非前端 mock
 * - 右侧面板显示真实 messages 日志流
 * - 构建中（running/paused）时每 3 秒轮询刷新
 * - 无 threadId 时展示入口引导
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import {
  MousePointer2,
  ChevronRight,
  AlertCircle,
  RefreshCw,
  Clock,
  Terminal,
  Rocket,
  Eye,
  EyeOff,
  FolderUp,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'
import { ApprovalPanel } from '@/components/ApprovalPanel'
import PipelineStepper, { type PipelineStep, type PipelineStepStatus } from '@/components/PipelineStepper'
import { JsonView } from '@/components/ui/JsonView'
import { getBuildState, resumeBuild, startFileWatching, stopFileWatching, startBuild, type BuildState } from '@/api/agents'
import { uploadFolder } from '@/api/folders'

// ============================================================
// LangGraph 7 节点流水线定义（静态拓扑）
// ============================================================

interface PipelineNodeDef {
  id: string
  label: string
  description: string
}

/** LangGraph 状态机节点顺序（与 agent_graph.py 一致） */
const PIPELINE_ORDER = [
  'planner',
  'scanner',
  'approval',
  'parser',
  'vectorizer',
  'builder',
  'tester',
] as const

/** 7 节点流水线定义（顺序与 LangGraph 状态转移一致） */
const PIPELINE_NODES: PipelineNodeDef[] = [
  { id: 'planner', label: '规划器', description: '创建 Agent 记录' },
  { id: 'scanner', label: '文件扫描', description: '扫描文件夹' },
  { id: 'approval', label: '人工审批', description: 'HITL 检查点' },
  { id: 'parser', label: '解析分块', description: '解析 + 分块' },
  { id: 'vectorizer', label: '向量化', description: '存入 ChromaDB' },
  { id: 'builder', label: '构建器', description: '生成 prompt + skills' },
  { id: 'tester', label: '测试器', description: '测试 Agent 可用性' },
]

// ============================================================
// 状态映射工具
// ============================================================

/** B2: 检测 approval 节点是否通过启发式自动审批（非人工） */
function isApprovalAutoApproved(state: BuildState | null): boolean {
  if (!state?.messages) return false
  return state.messages.some(
    (m) => m.includes('启发式自动通过') || m.includes('未启用审批，直接通过')
  )
}

/** 构建日志级别推断：根据消息关键词映射 info/success/warning/error */
type LogLevel = 'info' | 'success' | 'warning' | 'error'

const LOG_LEVEL_RULES: Array<{ level: LogLevel; keywords: string[] }> = [
  { level: 'error', keywords: ['错误', '失败', '异常', '终止', 'Error', 'Failed', 'Exception'] },
  { level: 'warning', keywords: ['警告', '跳过', '重试', '超时', 'Warn', 'Skip', 'Retry'] },
  { level: 'success', keywords: ['成功', '完成', '通过', '已生成', '已创建', '已索引', 'Success', 'Complete', 'PASS'] },
]

const LOG_LEVEL_STYLES: Record<LogLevel, { text: string; border: string; tag: string; label: string }> = {
  info: { text: 'text-text-secondary', border: 'border-l-4 border-l-info', tag: 'text-info', label: 'INFO' },
  success: { text: 'text-success', border: 'border-l-4 border-l-success', tag: 'text-success', label: 'OK' },
  warning: { text: 'text-warning', border: 'border-l-4 border-l-warning', tag: 'text-warning', label: 'WARN' },
  error: { text: 'text-error', border: 'border-l-4 border-l-error', tag: 'text-error', label: 'ERR' },
}

function inferLogLevel(msg: string): LogLevel {
  for (const rule of LOG_LEVEL_RULES) {
    if (rule.keywords.some((k) => msg.includes(k))) return rule.level
  }
  return 'info'
}

// ---- 构建日志按节点分组（同类信息聚合） ----

/** 节点 id → 分组名 */
const NODE_GROUP_LABEL: Record<string, string> = {
  planner: '规划器',
  scanner: '文件扫描',
  approval: '人工审批',
  parser: '解析分块',
  vectorizer: '向量化',
  builder: '构建器',
  tester: '测试器',
}

/** 根据日志关键词推断所属节点（无法判断时归入 其他） */
function classifyMessageNode(msg: string): string {
  const kw: Array<[string, string[]]> = [
    ['planner', ['规划器', '创建agent', '创建 agent', 'planner', '规划']],
    ['scanner', ['扫描', '文件夹', 'scanner', '遍历']],
    ['approval', ['审批', '通过', 'approval', '暂停', '检查点']],
    ['parser', ['解析', '分块', 'parser', 'chunk']],
    ['vectorizer', ['向量', 'chromadb', '索引', 'vector', 'embedding']],
    ['builder', ['生成', 'prompt', 'skills', 'builder', '构建 prompt']],
    ['tester', ['测试', 'tester', '可用性', '验证 agent']],
  ]
  const lower = msg.toLowerCase()
  for (const [node, keys] of kw) {
    if (keys.some((k) => lower.includes(k))) return node
  }
  return 'other'
}

const NODE_GROUP_ORDER = [...PIPELINE_ORDER, 'other'] as const

/** 根据后端 BuildState 派生每个节点的运行状态 */
function deriveNodeStatus(
  nodeId: string,
  state: BuildState | null,
): PipelineStepStatus {
  if (!state || !state.current_step) {
    return 'idle'
  }

  const order = [...PIPELINE_ORDER]
  const currentIdx = order.indexOf(state.current_step as typeof PIPELINE_ORDER[number])
  const nodeIdx = order.indexOf(nodeId as typeof PIPELINE_ORDER[number])

  if (currentIdx === -1 || nodeIdx === -1) return 'idle'

  // 构建已完成 → 全部 completed
  if (state.status === 'completed') return 'completed'

  // 构建失败 → 当前节点 failed，之前 completed
  if (state.status === 'failed') {
    if (nodeIdx < currentIdx) return 'completed'
    if (nodeIdx === currentIdx) return 'failed'
    return 'idle'
  }

  // 暂停在 approval → approval 节点显示 processing（等待审批）
  if (state.is_paused && nodeId === 'approval') return 'processing'

  // 运行中
  if (nodeIdx < currentIdx) return 'completed'
  if (nodeIdx === currentIdx) return 'processing'
  return 'idle'
}

/** 构建状态徽章配置 */
function buildStatusBadge(status: string | null, isPaused: boolean): {
  label: string
  dotClass: string
  textClass: string
} {
  if (isPaused) return { label: '已暂停 · 等待审批', dotClass: 'dot-warning', textClass: 'text-warning' }
  switch (status) {
    case 'running': return { label: '构建中', dotClass: 'dot-info', textClass: 'text-brand-500' }
    case 'completed': return { label: '构建完成', dotClass: 'dot-success', textClass: 'text-success' }
    case 'failed': return { label: '构建失败', dotClass: 'dot-error', textClass: 'text-error' }
    case 'paused': return { label: '已暂停', dotClass: 'dot-warning', textClass: 'text-warning' }
    default: return { label: '未知', dotClass: 'dot-muted', textClass: 'text-text-tertiary' }
  }
}

// ============================================================
// CanvasEntry — 无 threadId 时的入口引导（多种创建方法 + 直接构建）
// ============================================================

interface CreationMethod {
  id: string
  title: string
  description: string
  icon: typeof Terminal
  color: string
  bg: string
}

const CREATION_METHODS: CreationMethod[] = [
  {
    id: 'folder',
    title: '从知识库文件夹构建',
    description: '指定本地知识库文件夹，LangGraph 7 节点流水线自动扫描、解析、向量化并构建 Agent',
    icon: Terminal,
    color: 'text-brand-500',
    bg: 'bg-brand-50',
  },
  {
    id: 'interview',
    title: '交互式企业访谈',
    description: '通过结构化访谈收集企业信息，系统自动推荐岗位并生成 AI 员工团队',
    icon: Rocket,
    color: 'text-success',
    bg: 'bg-success/10',
  },
  {
    id: 'template',
    title: '从模板创建',
    description: '基于预置行业模板快速创建智能体，适用于标准业务场景',
    icon: MousePointer2,
    color: 'text-info',
    bg: 'bg-info/10',
  },
]

function CanvasEntry({ navigate }: { navigate: (path: string) => void }) {
  const [selectedMethod, setSelectedMethod] = useState<string | null>(null)
  const [building, setBuilding] = useState(false)
  const [buildError, setBuildError] = useState<string | null>(null)
  // 构建表单
  const [agentName, setAgentName] = useState('')
  const [folderPath, setFolderPath] = useState('')
  const [description, setDescription] = useState('')
  const [requireApproval, setRequireApproval] = useState(true)
  // 上传知识库文件夹状态
  const [folderUploading, setFolderUploading] = useState(false)
  const folderInputRef = useRef<HTMLInputElement>(null)

  // 上传知识库文件夹：选择本地文件夹 → 上传到服务端暂存，返回可扫描的 folder_path
  const handleUploadFolder = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files || [])
    if (!selectedFiles.length) return
    setFolderUploading(true)
    setBuildError(null)
    try {
      const result = await uploadFolder(selectedFiles)
      setFolderPath(result.folder_path)
    } catch (err) {
      setBuildError(err instanceof Error ? err.message : '文件夹上传失败')
    } finally {
      setFolderUploading(false)
      e.target.value = ''
    }
  }

  const handleStartBuild = async () => {
    if (!agentName.trim() || !folderPath.trim()) {
      setBuildError('请填写智能体名称并上传知识库文件夹')
      return
    }
    setBuilding(true)
    setBuildError(null)
    try {
      const result = await startBuild({
        name: agentName.trim(),
        description: description.trim() || undefined,
        folder_path: folderPath.trim(),
        require_approval: requireApproval,
      })
      // 构建启动后跳转到 Canvas 查看实时状态
      if (result.thread_id) {
        navigate(`/canvas/${result.thread_id}`)
      } else if (result.status === 'completed' && result.agent_id) {
        // 同步构建已完成，直接跳转协作测试
        navigate(`/chat/${result.agent_id}`)
      }
    } catch (err) {
      setBuildError(err instanceof Error ? err.message : '启动构建失败，请检查文件夹路径是否正确')
    } finally {
      setBuilding(false)
    }
  }

  return (
    <Layout fluid>
      <div className="w-full space-y-8">
        {/* 创建方式选择 */}
            {!selectedMethod && (
              <>
                <div>
                  <h2 className="text-h3 text-text-primary mb-1">选择创建方式</h2>
                  <p className="text-sm text-text-tertiary">三种方式均可启动 LangGraph 构建流水线，构建后可在下方查看实时执行状态</p>
                </div>
                <div className="grid gap-4 md:grid-cols-3">
                  {CREATION_METHODS.map((method) => {
                    const Icon = method.icon
                    return (
                      <button
                        key={method.id}
                        onClick={() => {
                          if (method.id === 'interview') {
                            navigate('/interview')
                          } else if (method.id === 'template') {
                            navigate('/employees?tab=templates')
                          } else {
                            setSelectedMethod(method.id)
                          }
                        }}
                        className="text-left p-5 rounded-xl border border-border-default bg-surface hover:border-brand-300 hover:shadow-soft transition-all group"
                      >
                        <div className={`w-12 h-12 rounded-lg ${method.bg} flex items-center justify-center mb-3`}>
                          <Icon className={`w-6 h-6 ${method.color}`} aria-hidden="true" />
                        </div>
                        <h3 className="text-base font-semibold text-text-primary mb-1 group-hover:text-brand-500 transition-colors">
                          {method.title}
                        </h3>
                        <p className="text-sm text-text-tertiary leading-relaxed">{method.description}</p>
                      </button>
                    )
                  })}
                </div>
              </>
            )}

            {/* 从知识库文件夹构建表单 */}
            {selectedMethod === 'folder' && (
              <div className="max-w-2xl mx-auto">
                <button
                  onClick={() => setSelectedMethod(null)}
                  className="text-sm text-text-tertiary hover:text-brand-500 transition-colors mb-4 flex items-center gap-1"
                >
                  <ChevronRight className="w-3.5 h-3.5 rotate-180" aria-hidden="true" />
                  返回选择创建方式
                </button>
                <div className="bg-surface border border-border-default rounded-xl p-6 space-y-5">
                  <div>
                    <h2 className="text-h3 text-text-primary mb-1">从知识库文件夹构建</h2>
                    <p className="text-sm text-text-tertiary">LangGraph 将自动执行：规划 → 扫描 → 审批 → 解析 → 向量化 → 构建 → 测试</p>
                  </div>

                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-text-primary mb-1.5">
                        智能体名称 <span className="text-error">*</span>
                      </label>
                      <input
                        type="text"
                        value={agentName}
                        onChange={(e) => setAgentName(e.target.value)}
                        placeholder="例如：销售助理 Agent"
                        className="w-full px-3 py-2 rounded-md border border-border-default bg-canvas text-text-primary placeholder-text-tertiary focus:outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-text-primary mb-1.5">
                        知识库文件夹 <span className="text-error">*</span>
                      </label>
                      <input
                        ref={folderInputRef}
                        type="file"
                        className="hidden"
                        onChange={handleUploadFolder}
                        disabled={folderUploading}
                        {...{ webkitdirectory: 'true', directory: '' }}
                      />
                      <button
                        type="button"
                        onClick={() => folderInputRef.current?.click()}
                        disabled={folderUploading}
                        className="w-full inline-flex items-center justify-center gap-2 px-3 py-2.5 rounded-md border border-dashed border-border-default bg-canvas text-text-secondary hover:border-brand-400 hover:text-brand-500 focus:outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400 transition-colors text-sm"
                      >
                        {folderUploading ? (
                          <RefreshCw className="w-4 h-4 animate-spin" aria-hidden="true" />
                        ) : (
                          <FolderUp className="w-4 h-4" aria-hidden="true" />
                        )}
                        {folderUploading ? '上传中...' : '点击上传文件夹'}
                      </button>
                      {folderPath ? (
                        <p className="text-xs text-success mt-1.5 flex items-center gap-1">
                          <FolderUp className="w-3.5 h-3.5" aria-hidden="true" />
                          已就绪：{folderPath}
                        </p>
                      ) : (
                        <p className="text-xs text-text-tertiary mt-1">选择本地知识库文件夹，将自动上传至服务端并作为构建数据源</p>
                      )}
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-text-primary mb-1.5">
                        描述（可选）
                      </label>
                      <textarea
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="简要描述智能体的职责和业务范围"
                        rows={2}
                        className="w-full px-3 py-2 rounded-md border border-border-default bg-canvas text-text-primary placeholder-text-tertiary focus:outline-none focus:border-brand-400 focus:ring-1 focus:ring-brand-400 resize-none"
                      />
                    </div>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={requireApproval}
                        onChange={(e) => setRequireApproval(e.target.checked)}
                        className="w-4 h-4 rounded border-border-default text-brand-500 focus:ring-brand-400"
                      />
                      <span className="text-sm text-text-secondary">
                        启用人工审批检查点（HITL）
                      </span>
                      <span className="text-xs text-text-tertiary">— 文件扫描后暂停，等待人工确认后再继续构建</span>
                    </label>
                  </div>

                  {buildError && (
                    <div className="flex items-start gap-2 p-3 rounded-md bg-error/10 border border-error/20">
                      <AlertCircle className="w-4 h-4 text-error flex-shrink-0 mt-0.5" aria-hidden="true" />
                      <p className="text-sm text-error">{buildError}</p>
                    </div>
                  )}

                  <div className="flex items-center gap-3 pt-2">
                    <Button variant="primary" onClick={handleStartBuild} disabled={building}>
                      {building ? (
                        <>
                          <RefreshCw className="w-4 h-4 animate-spin" aria-hidden="true" />
                          构建启动中...
                        </>
                      ) : (
                        <>
                          <Rocket className="w-4 h-4" aria-hidden="true" />
                          启动构建
                        </>
                      )}
                    </Button>
                    <Button variant="outline" onClick={() => setSelectedMethod(null)} disabled={building}>
                      取消
                    </Button>
                  </div>
                </div>
              </div>
            )}

            {/* 流水线只读预览 */}
            {!selectedMethod && (
              <div className="bg-surface border border-border-default rounded-xl p-6">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-base font-semibold text-text-primary">LangGraph 构建流水线（7 节点）</h3>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-elevated text-text-tertiary text-xs">
                    <MousePointer2 className="w-3 h-3" aria-hidden="true" />
                    只读 · 状态监控
                  </span>
                </div>
                <PipelineStepper
                  steps={PIPELINE_NODES.map((n): PipelineStep => ({
                    id: n.id,
                    label: n.label,
                    description: n.description,
                    status: 'idle',
                  }))}
                />
                <p className="text-xs text-text-tertiary mt-2">构建启动后，每个节点的实时状态、执行日志与审批检查点都会在此流水线中可视化展示</p>
              </div>
            )}
      </div>
    </Layout>
  )
}

// ============================================================
// 页面入口
// ============================================================

export default function AgentCanvasPage() {
  return (
    <Layout fluid>
      <AgentCanvasContent />
    </Layout>
  )
}

// ============================================================
// 页面主体
// ============================================================

function AgentCanvasContent() {
  const { threadId } = useParams<{ threadId: string }>()
  const navigate = useNavigate()

  const [buildState, setBuildState] = useState<BuildState | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  // 构建日志分组折叠状态（默认全展开，面板重新加载时清空）
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  // P0-1c 复查补全：审批恢复状态
  const [approving, setApproving] = useState(false)
  // D3-6.7: 文件监听启停状态（构建完成时 tester_node 自动启动，故默认 true）
  const [watching, setWatching] = useState(false)
  const [watchToggling, setWatchToggling] = useState(false)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 拉取构建状态
  const loadState = useCallback(async (tid: string, showLoading = true) => {
    try {
      if (showLoading) setLoading(true)
      setError(null)
      const data = await getBuildState(tid)
      setBuildState(data)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '未知错误'
      if (showLoading) setError(`加载构建状态失败: ${msg}`)
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [])

  // P0-1c 复查补全：审批恢复（通过/拒绝）
  const handleApprove = useCallback(async (approved: boolean) => {
    if (!threadId) return
    try {
      setApproving(true)
      setError(null)
      const data = await resumeBuild(threadId, approved)
      setBuildState(data)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '未知错误'
      setError(`审批操作失败: ${msg}`)
    } finally {
      setApproving(false)
    }
  }, [threadId])

  // D3-6.7: 手动启停文件监听
  const handleToggleWatch = useCallback(async () => {
    const agentId = buildState?.agent_id
    if (!agentId) return
    try {
      setWatchToggling(true)
      if (watching) {
        await stopFileWatching(agentId)
        setWatching(false)
      } else {
        await startFileWatching(agentId)
        setWatching(true)
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '未知错误'
      setError(`文件监听操作失败: ${msg}`)
    } finally {
      setWatchToggling(false)
    }
  }, [buildState?.agent_id, watching])

  // D3-6.7: 构建完成时，tester_node 已自动启动文件监听，同步前端状态
  useEffect(() => {
    if (buildState?.status === 'completed' && buildState.agent_id) {
      setWatching(true)
    } else if (buildState?.status === 'failed') {
      setWatching(false)
    }
  }, [buildState?.status, buildState?.agent_id])

  // 初始加载 + 轮询
  useEffect(() => {
    if (!threadId) return
    loadState(threadId)

    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    }
  }, [threadId, loadState])

  // 构建中/暂停时轮询（每 3 秒）；终态立即停止轮询
  useEffect(() => {
    if (!threadId || !buildState) return
    const isActive = buildState.status === 'running' || buildState.is_paused

    if (!isActive) {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current)
        pollTimerRef.current = null
      }
      return
    }

    pollTimerRef.current = setTimeout(async () => {
      await loadState(threadId, false)
    }, 3000)

    return () => {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }
  }, [threadId, buildState, loadState])

  // 派生：流水线步骤（状态由后端 current_step 派生）
  const steps: PipelineStep[] = useMemo(() => {
    return PIPELINE_NODES.map((def) => ({
      id: def.id,
      label: def.label,
      description: def.description,
      status: deriveNodeStatus(def.id, buildState),
    }))
  }, [buildState])

  const statusBadge = buildStatusBadge(buildState?.status ?? null, buildState?.is_paused ?? false)

  const selectedNode = selectedNodeId
    ? PIPELINE_NODES.find((n) => n.id === selectedNodeId) ?? null
    : null

  const completedCount = steps.filter((s) => s.status === 'completed').length
  const totalCount = steps.length

  const handleRefresh = () => {
    if (threadId) loadState(threadId)
  }

  // ---- 无 threadId：入口引导（多种创建方法 + 直接构建） ----
  if (!threadId) {
    return <CanvasEntry navigate={navigate} />
  }

  // ---- 有 threadId：状态可视化 ----
  return (
    <div className="flex flex-col h-full min-h-0">
      {/* range slider 样式 */}
      <style>{`
        .wf-range {
          -webkit-appearance: none;
          appearance: none;
          height: 4px;
          border-radius: 9999px;
          background: var(--bg-surface-3);
          outline: none;
        }
        .wf-range::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 14px;
          height: 14px;
          border-radius: 9999px;
          background: var(--brand);
          cursor: pointer;
          border: 2px solid var(--bg-surface);
          box-shadow: 0 0 0 1px var(--brand);
        }
        .wf-range::-moz-range-thumb {
          width: 14px;
          height: 14px;
          border-radius: 9999px;
          background: var(--brand);
          cursor: pointer;
          border: 2px solid var(--bg-surface);
        }
      `}</style>

      {/* ===== 工具栏（面包屑 + thread_id + 状态 + 操作） ===== */}
      <div className="h-14 mx-6 mt-4 bg-surface border border-border-default rounded-lg flex items-center justify-between px-4 flex-shrink-0 shadow-soft">
        <div className="flex items-center gap-3 min-w-0">
          <Link to="/canvas" className="text-sm text-text-tertiary hover:text-brand-500 transition-colors">
            智能体
          </Link>
          <ChevronRight className="w-3.5 h-3.5 text-text-tertiary flex-shrink-0" />
          <span className="text-sm font-medium text-text-primary truncate">构建线程</span>
          <span className="bg-elevated text-text-tertiary rounded px-2 py-0.5 text-xs font-mono flex-shrink-0">
            {threadId.slice(0, 8)}
          </span>
          <div className="flex items-center gap-1.5 flex-shrink-0">
            <span className={`dot ${statusBadge.dotClass}`} />
            <span className={`text-sm ${statusBadge.textClass}`}>{statusBadge.label}</span>
          </div>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <span className="text-xs text-text-tertiary">
            进度: <span className="font-mono text-text-secondary">{completedCount}/{totalCount}</span>
          </span>
          <Button variant="outline" size="sm" onClick={handleRefresh} disabled={loading}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          {buildState?.agent_id && (
            <Button variant="primary" size="sm" onClick={() => navigate(`/chat/${buildState.agent_id}`)}>
              <Rocket className="w-3.5 h-3.5" />
              协作测试
            </Button>
          )}
          {buildState?.agent_id && buildState?.status === 'completed' && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleToggleWatch}
              disabled={watchToggling}
              title={watching ? '文件变化将自动触发增量更新，点击停止' : '启动后文件变化将自动触发增量更新'}
              className={watching ? 'border-success/30 text-success hover:bg-success/10' : ''}
            >
              {watchToggling ? (
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              ) : watching ? (
                <EyeOff className="w-3.5 h-3.5" />
              ) : (
                <Eye className="w-3.5 h-3.5" />
              )}
              {watching ? '停止监听' : '启动监听'}
            </Button>
          )}
          {buildState?.is_paused && (
            <>
              <span className="text-xs text-warning flex items-center gap-1">
                <Clock className="w-3 h-3" />
                等待审批
              </span>
              {/* WT5: 集成 ApprovalPanel 组件（紧凑模式），恢复 HITL 暂停的构建 */}
              <ApprovalPanel
                compact
                title="构建审批"
                typeLabel="HITL"
                processing={approving}
                onApprove={() => handleApprove(true)}
                onReject={() => handleApprove(false)}
              />
            </>
          )}
        </div>
      </div>

      {/* ===== 2 列主区 ===== */}
      <div className="flex flex-1 mx-6 my-4 min-h-0 gap-0">
        {/* ---- 左侧：流水线步进器 ---- */}
        <div className="flex-1 relative border border-border-default rounded-l-lg bg-surface-2 min-w-0 overflow-y-auto">
          <PipelineStepper
            steps={steps}
            selectedId={selectedNodeId}
            onSelect={(id) => setSelectedNodeId(selectedNodeId === id ? null : id)}
            isPaused={buildState?.is_paused ?? false}
            approvalAutoApproved={isApprovalAutoApproved(buildState)}
          />

          {/* 加载遮罩 */}
          {loading && (
            <div className="absolute inset-0 bg-surface/60 backdrop-blur-sm flex items-center justify-center z-20">
              <Spinner size="xl" className="text-brand-500" />
            </div>
          )}

          {/* 错误遮罩 */}
          {error && (
            <div className="absolute inset-0 bg-surface/60 backdrop-blur-sm flex flex-col items-center justify-center gap-3 z-20 px-6 text-center">
              <AlertCircle className="h-10 w-10 text-error" />
              <p className="text-body text-text-secondary">{error}</p>
              <Button variant="outline" size="sm" onClick={handleRefresh}>重试</Button>
            </div>
          )}
        </div>

        {/* ---- 右侧：消息日志面板 ---- */}
        <aside className="w-80 bg-surface border border-border-default rounded-r-lg flex flex-col flex-shrink-0 overflow-hidden">
          {/* 面板标题 */}
          <div className="px-4 py-3 border-b border-border-subtle flex items-center gap-2 flex-shrink-0">
            <Terminal className="w-4 h-4 text-brand-500" />
            <span className="text-sm font-semibold text-text-primary">构建日志</span>
            {buildState?.messages && (
              <span className="ml-auto text-xs text-text-tertiary">{buildState.messages.length} 条</span>
            )}
          </div>

          {/* 节点详情（选中时） */}
          {selectedNode && (
            <div className="px-4 py-3 border-b border-border-subtle bg-elevated/50 flex-shrink-0">
              <div className="text-xs font-medium text-text-tertiary mb-1">选中节点</div>
              <div className="text-sm font-medium text-text-primary">{selectedNode.label}</div>
              <div className="text-xs text-text-secondary mt-0.5">{selectedNode.description}</div>
              <div className="flex items-center gap-1.5 mt-2">
                <span className={`dot ${
                  deriveNodeStatus(selectedNode.id, buildState) === 'completed' ? 'dot-success' :
                  deriveNodeStatus(selectedNode.id, buildState) === 'processing' ? 'dot-info' :
                  deriveNodeStatus(selectedNode.id, buildState) === 'failed' ? 'dot-error' : 'dot-muted'
                }`} />
                <span className="text-xs text-text-secondary">
                  {deriveNodeStatus(selectedNode.id, buildState) === 'completed' ? '已完成' :
                   deriveNodeStatus(selectedNode.id, buildState) === 'processing' ? '执行中' :
                   deriveNodeStatus(selectedNode.id, buildState) === 'failed' ? '失败' : '等待中'}
                </span>
                {/* B2: approval 节点自动通过时显示提示 */}
                {selectedNode.id === 'approval' && isApprovalAutoApproved(buildState) && (
                  <span className="text-xs text-success ml-1">· 启发式自动通过</span>
                )}
              </div>
            </div>
          )}

          {/* WT5: 选中 approval 节点且构建暂停时，展示完整审批面板 */}
          {selectedNode?.id === 'approval' && buildState?.is_paused && !isApprovalAutoApproved(buildState) && (
            <div className="px-4 py-3 border-b border-border-subtle flex-shrink-0">
              <ApprovalPanel
                title="LangGraph 构建审批"
                description="构建已暂停于人工审批检查点。批准后将继续执行 parser→vectorizer→builder→tester；拒绝将终止本次构建。"
                typeLabel="HITL 构建"
                requesterName="自动构建流程"
                processing={approving}
                onApprove={() => handleApprove(true)}
                onReject={() => handleApprove(false)}
              />
            </div>
          )}

          {/* 消息列表（按节点分组，可折叠） */}
          <div className="flex-1 overflow-y-auto scrollbar-thin px-3 py-2 space-y-2">
            {!buildState?.messages || buildState.messages.length === 0 ? (
              <div className="py-8 text-center text-text-tertiary text-sm">暂无日志</div>
            ) : (
              (() => {
                // 按节点分组，保持流水线顺序
                const groups = new Map<string, string[]>()
                NODE_GROUP_ORDER.forEach((node) => groups.set(node, []))
                buildState.messages.forEach((msg) => {
                  const node = groups.has(classifyMessageNode(msg)) ? classifyMessageNode(msg) : 'other'
                  groups.get(node)!.push(msg)
                })
                const orderedGroups = NODE_GROUP_ORDER.filter((node) => groups.get(node)!.length > 0)
                return orderedGroups.map((node) => {
                  const msgs = groups.get(node)!
                  const collapsed = collapsedGroups.has(node)
                  const label = NODE_GROUP_LABEL[node] || '其他节点'
                  return (
                    <div key={node} className="rounded-md border border-border-subtle overflow-hidden">
                      <button
                        type="button"
                        onClick={() =>
                          setCollapsedGroups((prev) => {
                            const next = new Set(prev)
                            if (next.has(node)) next.delete(node)
                            else next.add(node)
                            return next
                          })
                        }
                        className="w-full flex items-center gap-2 px-3 py-2 bg-elevated hover:bg-elevated/60 transition-colors"
                      >
                        <ChevronRight
                          className={`w-3.5 h-3.5 text-text-muted transition-transform ${collapsed ? '' : 'rotate-90'}`}
                          aria-hidden="true"
                        />
                        <span className="text-xs font-semibold text-text-primary">{label}</span>
                        <span className="text-[10px] text-text-muted">· {msgs.length} 条</span>
                        <span className="ml-auto text-[10px] text-text-muted">
                          {collapsed ? '展开' : '收起'}
                        </span>
                      </button>
                      {!collapsed && (
                        <div className="divide-y divide-border-subtle">
                          {msgs.map((msg, i) => {
                            const level = inferLogLevel(msg)
                            const style = LOG_LEVEL_STYLES[level]
                            return (
                              <div
                                key={`${node}-${i}`}
                                className={`px-2.5 py-2 text-xs font-mono ${style.text} ${style.border} rounded-l-none`}
                              >
                                <span className={`${style.tag} mr-1.5 font-semibold`}>
                                  [{style.label}]
                                </span>
                                <span className="text-text-tertiary mr-1.5">›</span>
                                {msg}
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )
                })
              })()
            )}
          </div>

          {/* 测试结果（如有） */}
          {buildState?.test_result && (
            <div className="px-4 py-3 border-t border-border-subtle flex-shrink-0">
              <div className="text-xs font-medium text-text-tertiary mb-1.5">测试结果</div>
              <JsonView value={buildState.test_result} title="测试结果" defaultCollapsed={false} />
            </div>
          )}
        </aside>
      </div>

      {/* ===== 底部状态栏 ===== */}
      <div className="h-10 mx-6 mb-4 bg-surface border border-border-default rounded-lg flex items-center justify-end px-4 flex-shrink-0 text-sm text-text-tertiary">
        <div className="flex items-center gap-4">
          <span>
            完成节点: <span className="font-mono text-text-secondary">{completedCount}/{totalCount}</span>
          </span>
          <div className="flex items-center gap-1.5">
            <span className={`dot ${statusBadge.dotClass}`} />
            <span className={statusBadge.textClass}>{statusBadge.label}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
