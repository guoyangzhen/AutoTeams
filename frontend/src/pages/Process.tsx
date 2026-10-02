import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Check,
  Loader2,
  FileSearch,
  Database,
  Layers,
  Boxes,
  ClipboardCheck,
  Rocket,
  Terminal,
  Filter,
  ChevronRight,
  FileText,
  List,
  RotateCcw,
  RefreshCw,
  MessageSquare,
  AlertCircle,
  Square,
  Eye,
  History,
} from 'lucide-react'
import Layout from '@/components/Layout'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Spinner } from '@/components/ui/Spinner'
import { getProgress, getReport, cancelTask, startProcess, listTasks } from '@/api/process'
import { useAuth } from '@/hooks/useAuth'
import { useConfirmDialog } from '@/hooks/useConfirmDialog'
import { ProcessingTask } from '@/types'

/* ============================================================
   常量与类型定义
   ============================================================ */

interface PipelineStage {
  key: string
  name: string
  icon: typeof FileSearch
}

// 流水线 6 阶段（方案A 核心）
const PIPELINE_STAGES: PipelineStage[] = [
  { key: 'parse', name: '解析文件', icon: FileSearch },
  { key: 'vectorize', name: '向量化', icon: Database },
  { key: 'index', name: '构建索引', icon: Layers },
  { key: 'build', name: '编译智能体', icon: Boxes },
  { key: 'test', name: '运行测试', icon: ClipboardCheck },
  { key: 'deploy', name: '部署上线', icon: Rocket },
]

type StageState = 'completed' | 'active' | 'failed' | 'pending'

type LogLevel = 'INFO' | 'WARN' | 'ERROR' | 'DEBUG' | 'SUCCESS'

interface LogEntry {
  timestamp: string
  level: LogLevel
  message: string
}

interface DeploymentRecord {
  version: string
  environment: 'prod' | 'staging'
  status: 'success' | 'failed' | 'building'
  duration: string
  deployedAt: string
}

/* ============================================================
   纯函数工具
   ============================================================ */

function formatTime(date: Date): string {
  return date.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatTaskId(taskId: string | undefined): string {
  if (!taskId) return '—'
  return taskId.length > 16 ? `${taskId.slice(0, 8)}…${taskId.slice(-4)}` : taskId
}

// 根据任务状态 + 进度推算每个阶段的状态
function getStageStates(task: ProcessingTask | null): StageState[] {
  if (!task) return PIPELINE_STAGES.map(() => 'pending' as StageState)

  if (task.status === 'completed') {
    return PIPELINE_STAGES.map(() => 'completed' as StageState)
  }

  // 根据 progress (0-1) 确定当前活跃阶段
  const currentIdx = Math.min(
    PIPELINE_STAGES.length - 1,
    Math.floor(task.progress * PIPELINE_STAGES.length),
  )

  return PIPELINE_STAGES.map((_, i) => {
    if ((task.status === 'failed' || task.status === 'cancelled') && i === currentIdx) return 'failed'
    if (i < currentIdx) return 'completed'
    if (i === currentIdx) return 'active'
    return 'pending'
  })
}

// 后端不提供各阶段真实耗时，前端推算开始/结束时间属于编造数据。
// 改为仅标注阶段状态，不显示具体时长。
function getStageDurations(task: ProcessingTask | null) {
  const states = getStageStates(task)

  return PIPELINE_STAGES.map((stage, i) => {
    const state = states[i]
    return {
      name: stage.name,
      startTime: state === 'pending' ? '—' : '进行中',
      endTime: state === 'completed' ? '完成' : '—',
      duration: state === 'completed' ? '完成' : state === 'active' ? '进行中' : state === 'failed' ? '失败' : '—',
    }
  })
}

// 后端只返回 task.message 与 error_log，前端无真实的各阶段详细日志。
// 原 generateLogs 用模板（「索引构建完成 (12,047 条)」「单元测试通过 38/38」）
// 伪造逐秒日志，属于编造（UI v4 §一 罪一）。改为仅展示后端真实返回的消息。
function buildLogs(task: ProcessingTask | null): LogEntry[] {
  if (!task) return []
  const logs: LogEntry[] = []

  // 1. 任务整体消息（如果有）
  if (task.message) {
    logs.push({
      timestamp: formatTime(new Date()),
      level: task.status === 'failed' ? 'ERROR' : 'INFO',
      message: task.message,
    })
  }

  // 2. 后端返回的错误日志（error_log 数组）
  if (task.error_log && task.error_log.length > 0) {
    task.error_log.forEach((err) => {
      logs.push({
        timestamp: err.timestamp ? formatTime(new Date(err.timestamp)) : formatTime(new Date()),
        level: 'ERROR',
        message: err.file
          ? `文件 ${err.file}：${err.error || '处理失败'}`
          : err.error || '处理失败',
      })
    })
  }

  // 3. 如果完全无日志，给出兜底提示
  if (logs.length === 0) {
    logs.push({
      timestamp: formatTime(new Date()),
      level: 'INFO',
      message: '暂无详细日志',
    })
  }

  return logs
}

// 根据任务状态返回徽章配置
function getStatusBadge(status: string): {
  text: string
  className: string
  dotClass: string
} {
  switch (status) {
    case 'completed':
      return { text: '已完成', className: 'text-success bg-success/10', dotClass: 'dot-success' }
    case 'failed':
      return { text: '失败', className: 'text-error bg-error/10', dotClass: 'dot-error' }
    case 'pending':
      return { text: '等待中', className: 'text-text-tertiary bg-elevated', dotClass: 'dot-muted' }
    case 'cancelled':
      return { text: '已取消', className: 'text-text-tertiary bg-elevated', dotClass: 'dot-muted' }
    default:
      return { text: '构建中', className: 'text-warning bg-warning/10', dotClass: 'dot-warning' }
  }
}

/* ============================================================
   历史任务面板子组件（激活 list_tasks 端点）
   ============================================================ */

interface HistoryTaskPanelProps {
  currentTaskId?: string
  onSelectTask: (taskId: string) => void
}

function HistoryTaskPanel({ currentTaskId, onSelectTask }: HistoryTaskPanelProps) {
  const [expanded, setExpanded] = useState(true)
  const [tasks, setTasks] = useState<ProcessingTask[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [refreshKey, setRefreshKey] = useState(0)

  const loadHistory = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: { limit: number; offset: number; status?: string } = {
        limit: 20,
        offset: 0,
      }
      if (statusFilter !== 'all') params.status = statusFilter
      const resp = await listTasks(params)
      setTasks(resp.tasks || [])
      setTotal(resp.total || 0)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载历史任务失败'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    loadHistory()
  }, [loadHistory, refreshKey])

  // 自动刷新（每 15s 拉取一次，便于看到新任务/状态变化）
  useEffect(() => {
    if (!expanded) return
    const id = window.setInterval(() => {
      setRefreshKey((k) => k + 1)
    }, 15000)
    return () => clearInterval(id)
  }, [expanded])

  return (
    <Card className="shadow-soft overflow-hidden">
      {/* 头部：可折叠 */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between px-6 py-4 hover:bg-elevated transition-colors"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2">
          <History className="w-4 h-4 text-text-tertiary" />
          <h3 className="text-sm font-semibold text-text-primary">历史任务</h3>
          <span className="text-xs font-mono text-text-tertiary ml-1">
            共 {total} 条
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation()
              setRefreshKey((k) => k + 1)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.stopPropagation()
                setRefreshKey((k) => k + 1)
              }
            }}
            className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-brand-500 transition-colors"
            title="刷新"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </span>
          <ChevronRight
            className={`w-4 h-4 text-text-tertiary transition-transform ${
              expanded ? 'rotate-90' : ''
            }`}
          />
        </div>
      </button>

      {expanded && (
        <div className="border-t border-border-subtle">
          {/* 筛选条 */}
          <div className="flex items-center gap-2 px-6 py-3 bg-surface-2 border-b border-border-subtle">
            <Filter className="w-3.5 h-3.5 text-text-tertiary" />
            <span className="text-xs text-text-tertiary">状态筛选：</span>
            {['all', 'completed', 'failed', 'processing', 'pending', 'cancelled'].map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setStatusFilter(s)}
                className={`text-xs px-2 py-0.5 rounded transition-colors ${
                  statusFilter === s
                    ? 'bg-brand-500 text-white'
                    : 'bg-elevated text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`}
              >
                {s === 'all' ? '全部' : getStatusBadge(s).text}
              </button>
            ))}
          </div>

          {/* 错误提示 */}
          {error && (
            <div className="px-6 py-3 text-xs text-error bg-error/5 border-b border-error/10">
              {error}
            </div>
          )}

          {/* 任务列表 */}
          {loading && tasks.length === 0 ? (
            <div className="flex justify-center items-center py-8">
              <Spinner size="sm" />
            </div>
          ) : tasks.length === 0 ? (
            <div className="text-center py-8 text-sm text-text-tertiary">
              暂无历史任务记录
            </div>
          ) : (
            <div className="overflow-x-auto scrollbar-thin">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="bg-elevated">
                    <th className="px-6 py-2.5 text-left text-xs uppercase text-text-tertiary font-medium">
                      任务 ID
                    </th>
                    <th className="px-6 py-2.5 text-left text-xs uppercase text-text-tertiary font-medium">
                      智能体
                    </th>
                    <th className="px-6 py-2.5 text-left text-xs uppercase text-text-tertiary font-medium">
                      状态
                    </th>
                    <th className="px-6 py-2.5 text-left text-xs uppercase text-text-tertiary font-medium">
                      进度
                    </th>
                    <th className="px-6 py-2.5 text-left text-xs uppercase text-text-tertiary font-medium">
                      文件
                    </th>
                    <th className="px-6 py-2.5 text-left text-xs uppercase text-text-tertiary font-medium">
                      创建时间
                    </th>
                    <th className="px-6 py-2.5 text-right text-xs uppercase text-text-tertiary font-medium">
                      操作
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((t) => {
                    const badge = getStatusBadge(t.status)
                    const isCurrent = t.task_id === currentTaskId
                    const fileProgress =
                      t.total_files > 0
                        ? `${t.processed_files}/${t.total_files}`
                        : '—'
                    const percent = Math.round(t.progress * 100)
                    return (
                      <tr
                        key={t.task_id}
                        className={`border-b border-border-subtle last:border-0 hover:bg-elevated transition-colors ${
                          isCurrent ? 'bg-brand-50/50' : ''
                        }`}
                      >
                        <td className="px-6 py-3 text-xs font-mono text-text-primary whitespace-nowrap">
                          {formatTaskId(t.task_id)}
                          {isCurrent && (
                            <span className="ml-2 text-xs text-brand-500 font-sans">
                              当前
                            </span>
                          )}
                        </td>
                        <td className="px-6 py-3 text-sm text-text-secondary whitespace-nowrap">
                          {t.agent_name || '—'}
                        </td>
                        <td className="px-6 py-3 text-sm whitespace-nowrap">
                          <span
                            className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium ${badge.className}`}
                          >
                            <span className={`dot ${badge.dotClass}`} />
                            {badge.text}
                          </span>
                        </td>
                        <td className="px-6 py-3 text-xs font-mono text-text-primary whitespace-nowrap">
                          {percent}%
                        </td>
                        <td className="px-6 py-3 text-xs font-mono text-text-secondary whitespace-nowrap">
                          {fileProgress}
                        </td>
                        <td className="px-6 py-3 text-xs font-mono text-text-tertiary whitespace-nowrap">
                          {t.created_at
                            ? new Date(t.created_at).toLocaleString('zh-CN', {
                                hour12: false,
                                month: '2-digit',
                                day: '2-digit',
                                hour: '2-digit',
                                minute: '2-digit',
                              })
                            : '—'}
                        </td>
                        <td className="px-6 py-3 text-right">
                          <button
                            type="button"
                            onClick={() => onSelectTask(t.task_id)}
                            disabled={isCurrent}
                            className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-brand-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            title={isCurrent ? '当前任务' : '查看详情'}
                          >
                            <Eye className="w-3.5 h-3.5" />
                            查看
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

/* ============================================================
   主组件
   ============================================================ */

export default function Process() {
  const { taskId } = useParams<{ taskId: string }>()
  const navigate = useNavigate()
  const confirmDialog = useConfirmDialog()

  const [task, setTask] = useState<ProcessingTask | null>(null)
  const [report, setReport] = useState<{
    task_id: string
    status: string
    summary: string
    files_processed: number
    files_failed: number
    knowledge_count: number
    processing_time_seconds: number
  } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const [retrying, setRetrying] = useState(false)

  const { user } = useAuth()
  const logContainerRef = useRef<HTMLDivElement | null>(null)

  // P1-FE: 取消任务（仅 pending/processing 状态可取消）
  const handleCancel = useCallback(async () => {
    if (!taskId || !task) return
    if (task.status !== 'pending' && task.status !== 'processing') return
    const ok = await confirmDialog.ask({
      title: '取消处理任务',
      description: '确定要取消当前处理任务吗？已处理的文件不会回滚。',
      confirmText: '取消任务',
      cancelText: '保留',
      variant: 'danger',
    })
    if (!ok) return

    setCancelling(true)
    try {
      const updated = await cancelTask(taskId)
      setTask(updated)
    } catch (err) {
      console.error('取消任务失败:', err)
      setError('取消任务失败，请重试')
    } finally {
      setCancelling(false)
    }
  }, [taskId, task, confirmDialog])

  // S15: 失败后整体重试：用同一文件夹与 Agent 信息创建新任务
  const handleRetry = useCallback(async () => {
    if (!task || !user?.enterprise_id) return
    const ok = await confirmDialog.ask({
      title: '重新处理',
      description: '确定要重新处理该文件夹吗？将创建一个新任务。',
      confirmText: '创建新任务',
      cancelText: '取消',
      variant: 'default',
    })
    if (!ok) return

    setRetrying(true)
    try {
      const newTask = await startProcess({
        folder_path: task.folder_path || '',
        enterprise_id: user.enterprise_id,
        agent_name: task.agent_name || '知识助手',
        agent_description: task.agent_description || '',
      })
      navigate(`/process/${newTask.task_id}`)
    } catch (err) {
      console.error('重试任务失败:', err)
      setError('重试失败，请重试')
      setRetrying(false)
    }
  }, [task, user, navigate, confirmDialog])

  /* --- 任务进度轮询（P1-FE: 改为递归 setTimeout，避免异步请求重叠，卸载时确保停止） --- */
  useEffect(() => {
    if (!taskId) return

    let isCancelled = false
    let timeoutId: number | null = null

    const pollProgress = async () => {
      try {
        const taskData = await getProgress(taskId)
        if (isCancelled) return true

        setTask(taskData)

        if (taskData.status === 'completed') {
          try {
            const reportData = await getReport(taskId)
            if (!isCancelled) setReport(reportData)
          } catch (err) {
            console.error('获取报告失败:', err)
          }
          return true
        }

        if (taskData.status === 'failed') {
          setError(taskData.message || '处理失败，请重试')
          return true
        }

        return false
      } catch (err) {
        console.error('获取进度失败:', err)
        setError('获取进度失败')
        return true
      }
    }

    const scheduleNext = async () => {
      const shouldStop = await pollProgress()
      if (!isCancelled && !shouldStop) {
        timeoutId = window.setTimeout(scheduleNext, 2000)
      }
    }

    const startPolling = async () => {
      setLoading(true)
      await scheduleNext()
      if (!isCancelled) setLoading(false)
    }

    startPolling()

    return () => {
      isCancelled = true
      if (timeoutId !== null) {
        clearTimeout(timeoutId)
        timeoutId = null
      }
    }
  }, [taskId])

  /* --- 派生数据（必须在条件 return 之前调用 hooks） --- */
  const logs = useMemo(() => buildLogs(task), [task])
  const stageStates = useMemo(() => getStageStates(task), [task])
  const stageDurations = useMemo(() => getStageDurations(task), [task])

  /* --- 日志自动滚动到底部 --- */
  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [logs])

  /* --- 条件渲染（loading / error / not-found） --- */
  if (loading && !task) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <Spinner size="xl" className="text-brand-500" />
        </div>
      </Layout>
    )
  }

  if (error && !task) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="text-center">
            <div className="w-16 h-16 bg-error/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <AlertCircle className="w-8 h-8 text-error" />
            </div>
            <h2 className="text-xl font-semibold text-text-primary mb-2">处理出错</h2>
            <p className="text-text-secondary mb-4">{error}</p>
            <Button variant="primary" onClick={() => navigate('/')}>
              返回首页
            </Button>
          </div>
        </div>
      </Layout>
    )
  }

  if (!task) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="text-center">
            <h2 className="text-xl font-semibold text-text-primary mb-2">任务不存在</h2>
            <Button variant="primary" onClick={() => navigate('/')}>
              返回首页
            </Button>
          </div>
        </div>
      </Layout>
    )
  }

  /* --- 主页面派生值 --- */
  const progress = Math.round(task.progress * 100)
  const statusBadge = getStatusBadge(task.status)
  const isRunning =
    task.status !== 'completed' &&
    task.status !== 'failed' &&
    task.status !== 'cancelled' &&
    task.status !== 'pending'

  // 当前部署历史 = 当前任务（v1.4）+ 历史 4 条
  const currentDuration =
    task.status === 'completed' && report
      ? formatDuration(report.processing_time_seconds)
      : '进行中'

  const currentRecord: DeploymentRecord = {
    version: 'v1.4',
    environment: 'prod',
    status:
      task.status === 'completed'
        ? 'success'
        : task.status === 'failed'
        ? 'failed'
        : 'building',
    duration: currentDuration,
    deployedAt: new Date().toISOString().slice(0, 16).replace('T', ' '),
  }

  const deploymentHistory: DeploymentRecord[] = taskId ? [currentRecord] : []

  /* --- 日志级别颜色映射 --- */
  const getLogLevelColor = (level: LogLevel): string => {
    switch (level) {
      case 'INFO':
        return 'text-info'
      case 'WARN':
        return 'text-warning'
      case 'ERROR':
        return 'text-error'
      case 'DEBUG':
        return 'text-text-tertiary'
      case 'SUCCESS':
        return 'text-success'
      default:
        return 'text-text-tertiary'
    }
  }

  /* --- 主渲染 --- */
  return (
    <Layout>
      <div className="space-y-8">
        {/* ============ 0. 历史任务面板（激活 list_tasks 端点） ============ */}
        <HistoryTaskPanel
          currentTaskId={taskId}
          onSelectTask={(tid) => navigate(`/process/${tid}`)}
        />

        {/* ============ 1. 页面头部 ============ */}
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div className="space-y-2">
            {/* 面包屑 */}
            <div className="text-sm text-text-tertiary flex items-center gap-1.5">
              <button
                onClick={() => navigate('/')}
                className="hover:text-text-primary transition-colors"
              >
                控制台
              </button>
              <span>/</span>
              <span className="text-text-primary">部署流水线</span>
            </div>
            {/* 衬线标题 + mono 版本号 */}
            <div className="flex items-center">
              <h1 className="font-serif-display text-3xl font-bold text-text-primary">
                部署流水线
              </h1>
              <span className="font-mono text-sm text-text-tertiary ml-3">
                {currentRecord.version}
              </span>
            </div>
            <div className="brand-rule" />
          </div>

          {/* 右侧：任务 ID + 状态徽章 + 操作按钮 */}
          <div className="flex items-center gap-3 flex-wrap">
            <span className="font-mono text-text-tertiary text-sm">
              任务 ID · {formatTaskId(taskId)}
            </span>
            <span
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-sm font-medium ${statusBadge.className}`}
            >
              {isRunning ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <span className={`dot ${statusBadge.dotClass}`} />
              )}
              {statusBadge.text}
            </span>
            {(task.status === 'pending' || task.status === 'processing') && (
              <Button
                variant="outline"
                size="md"
                onClick={handleCancel}
                disabled={cancelling}
                className="border-error/30 text-error hover:bg-error/10 hover:text-error"
              >
                {cancelling ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Square className="w-4 h-4" />
                )}
                取消任务
              </Button>
            )}
            {(task.status === 'failed' || task.status === 'cancelled') && (
              <Button
                variant="outline"
                size="md"
                onClick={handleRetry}
                disabled={retrying}
                className="border-brand-500/30 text-brand-500 hover:bg-brand-50 hover:text-brand-500"
              >
                {retrying ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4" />
                )}
                重新处理
              </Button>
            )}
            <Button variant="outline" size="md" onClick={() => navigate('/')}>
              <MessageSquare className="w-4 h-4" />
              查看智能体
            </Button>
          </div>
        </div>

        {/* ============ 2. 流水线进度条（方案A 核心） ============ */}
        <Card className="p-6 shadow-soft">
          <h2 className="text-sm font-semibold text-text-primary mb-4">流水线进度</h2>
          <div className="overflow-x-auto scrollbar-thin pb-2">
            <div className="flex items-start min-w-[640px]">
              {PIPELINE_STAGES.map((stage, i) => {
              const state = stageStates[i]
              const Icon = stage.icon
              return (
                <div
                  key={stage.key}
                  className={`flex items-start ${i < PIPELINE_STAGES.length - 1 ? 'flex-1' : 'flex-1'}`}
                >
                  {/* 阶段节点 */}
                  <div className="flex flex-col items-center w-20 shrink-0">
                    <div
                      className={`w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
                        state === 'completed'
                          ? 'bg-success text-white'
                          : state === 'active'
                          ? 'bg-brand-500 text-white pipeline-pulse'
                          : state === 'failed'
                          ? 'bg-error text-white'
                          : 'bg-surface-3 text-text-tertiary'
                      }`}
                    >
                      {state === 'completed' && <Check className="w-5 h-5" />}
                      {state === 'active' && <Loader2 className="w-5 h-5 animate-spin" />}
                      {state === 'failed' && <AlertCircle className="w-5 h-5" />}
                      {state === 'pending' && <Icon className="w-5 h-5" />}
                    </div>
                    <div
                      className={`text-xs mt-2 ${
                        state === 'active'
                          ? 'text-brand-500 font-medium'
                          : state === 'failed'
                          ? 'text-error'
                          : state === 'completed'
                          ? 'text-text-primary'
                          : 'text-text-tertiary'
                      }`}
                    >
                      {stage.name}
                    </div>
                    <div
                      className={`text-xs font-mono mt-0.5 ${
                        state === 'active'
                          ? 'text-brand-500'
                          : state === 'failed'
                          ? 'text-error'
                          : 'text-text-tertiary'
                      }`}
                    >
                      {state === 'active'
                        ? '进行中'
                        : state === 'failed'
                        ? '失败'
                        : state === 'completed'
                        ? '已完成'
                        : '等待中'}
                    </div>
                  </div>
                  {/* 阶段连接线 */}
                  {i < PIPELINE_STAGES.length - 1 && (
                    <div
                      className={`flex-1 h-0.5 mt-5 transition-colors ${
                        state === 'completed' ? 'bg-success' : 'bg-border-default'
                      }`}
                    />
                  )}
                </div>
              )
            })}
          </div>
          </div>
          {/* 进度条底部信息 */}
          <div className="flex items-center justify-between mt-6 pt-4 border-t border-border-subtle text-sm">
            <span className="text-text-secondary">{task.message || '—'}</span>
            <span className="font-mono text-text-primary">
              已处理 {task.processed_files} / {task.total_files} 文件 · {progress}%
            </span>
          </div>
        </Card>

        {/* ============ S15: 文件级处理状态 ============ */}
        <Card className="p-5 shadow-soft">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2">
              <FileText className="w-4 h-4 text-text-tertiary" />
              文件级处理状态
            </h3>
            <span className="text-xs font-mono text-text-tertiary">
              {task.processed_files} / {task.total_files} 已完成 · {task.failed_files} 失败
            </span>
          </div>
          {/* 总进度 */}
          <div className="mb-4">
            <div className="flex justify-between text-xs mb-1.5">
              <span className="text-text-tertiary">文件处理进度</span>
              <span className="font-mono text-text-primary">
                {task.total_files > 0
                  ? Math.round((task.processed_files / task.total_files) * 100)
                  : 0}
                %
              </span>
            </div>
            <div className="h-2 rounded-full bg-elevated overflow-hidden">
              <div
                className="h-full rounded-full bg-brand-500 transition-all duration-700 ease-out"
                style={{
                  width: `${
                    task.total_files > 0
                      ? Math.round((task.processed_files / task.total_files) * 100)
                      : 0
                  }%`,
                }}
              />
            </div>
          </div>
          {/* 失败文件列表 */}
          {(task.error_log?.length ?? 0) > 0 ? (
            <div className="rounded-lg border border-error/20 bg-error/5 overflow-hidden">
              <div className="px-4 py-2 bg-error/10 border-b border-error/10 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-error" />
                <span className="text-xs font-medium text-error">失败文件明细</span>
              </div>
              <ul className="divide-y divide-error/10 max-h-48 overflow-y-auto scrollbar-thin">
                {task.error_log?.map((entry, i) => (
                  <li key={i} className="px-4 py-2.5 flex items-start gap-3 text-sm">
                    <FileText className="w-4 h-4 text-text-tertiary shrink-0 mt-0.5" />
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-text-primary truncate">
                        {entry.file && entry.file !== 'unknown'
                          ? entry.file
                          : `未知文件 #${i + 1}`}
                      </div>
                      <div className="text-xs text-text-secondary mt-0.5">
                        {entry.error || task.message || '处理失败'}
                        {entry.error_code && (
                          <span className="ml-2 font-mono text-text-tertiary">
                            [{entry.error_code}]
                          </span>
                        )}
                      </div>
                    </div>
                    <span className="text-xs font-mono text-text-tertiary shrink-0">
                      {entry.timestamp
                        ? new Date(entry.timestamp).toLocaleTimeString('zh-CN', {
                            hour12: false,
                          })
                        : '—'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : task.status === 'completed' ? (
            <div className="flex items-center gap-2 text-sm text-success">
              <Check className="w-4 h-4" />
              全部文件处理成功，无失败记录
            </div>
          ) : (
            <div className="text-sm text-text-tertiary">
              暂无文件级错误记录。处理完成后将在此展示失败文件明细。
            </div>
          )}
        </Card>

        {/* ============ 3. 两栏：构建日志 + 资源用量 ============ */}
        <section className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
          {/* 3.1 构建日志终端（左 ~60%） - 方案B 暖白底 */}
          <div className="rounded-xl border border-border-default overflow-hidden shadow-soft min-w-0 bg-surface-2">
            {/* 终端标题栏 (简洁: 标题 + 日志行数统计) */}
            <div className="flex items-center justify-between px-4 py-2.5 bg-surface border-b border-border-default">
              <div className="flex items-center gap-2">
                <Terminal className="w-4 h-4 text-text-tertiary" />
                <span className="text-sm font-semibold text-text-primary">构建日志</span>
                <span className="text-xs font-mono text-text-tertiary ml-1">
                  共 {logs.length} 行
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                {isRunning ? (
                  <>
                    <span className="dot dot-success animate-pulse" />
                    <span className="text-xs text-success">实时</span>
                  </>
                ) : (
                  <span className="text-xs text-text-tertiary">
                    {task.status === 'completed' ? '已完成' : '已停止'}
                  </span>
                )}
              </div>
            </div>
            {/* 日志内容 */}
            <div
              ref={logContainerRef}
              className="p-4 font-mono text-xs leading-relaxed h-96 overflow-y-auto overflow-x-hidden scrollbar-thin space-y-1"
            >
              {logs.length === 0 ? (
                <div className="text-text-tertiary">等待日志输出…</div>
              ) : (
                logs.map((entry, i) => {
                  const levelColor = getLogLevelColor(entry.level)
                  return (
                    <div key={i} className="flex gap-3">
                      <span className="text-text-tertiary shrink-0 w-20 text-right">
                        {entry.timestamp}
                      </span>
                      <span className={`shrink-0 w-16 ${levelColor}`}>
                        [{entry.level}]
                      </span>
                      <span className={`min-w-0 break-words ${levelColor}`}>
                        {entry.message}
                      </span>
                    </div>
                  )
                })
              )}
              {/* 实时光标行 */}
              {isRunning && (
                <div className="flex gap-3">
                  <span className="text-text-tertiary shrink-0 w-20 text-right">
                    {formatTime(new Date())}
                  </span>
                  <span className="text-text-tertiary shrink-0 w-16">[INFO]</span>
                  <span className="text-text-secondary">
                    {task.message || '处理中…'}
                    <span className="inline-block w-2 h-3.5 align-middle animate-pulse ml-1 bg-brand-500" />
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* 3.2 处理统计面板（右 ~40%）
              原「资源用量」面板的 CPU/内存/磁盘/网络四条进度条由
              `45 + Math.random() * 35` 这类随机数每 1.5 秒刷新，自己挂着
              「模拟数据」徽章 —— 属于纯装饰（UI v4 §一 罪一），已整体移除。
              替换为后端 ProcessingTask 真实返回的文件处理统计。 */}
          <Card className="p-5 shadow-soft">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-text-primary">处理统计</h3>
              <span className="text-xs text-text-tertiary font-mono">
                {task.processing_time_seconds > 0
                  ? formatDuration(task.processing_time_seconds)
                  : '—'}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-md bg-elevated p-3">
                <div className="text-lg font-semibold tabular-nums text-text-primary">
                  {task.total_files}
                </div>
                <div className="text-xs text-text-tertiary mt-0.5">文件总数</div>
              </div>
              <div className="rounded-md bg-elevated p-3">
                <div className="text-lg font-semibold tabular-nums text-success">
                  {task.processed_files}
                </div>
                <div className="text-xs text-text-tertiary mt-0.5">已处理</div>
              </div>
              <div className="rounded-md bg-elevated p-3">
                <div
                  className={`text-lg font-semibold tabular-nums ${
                    task.failed_files > 0 ? 'text-error' : 'text-text-primary'
                  }`}
                >
                  {task.failed_files}
                </div>
                <div className="text-xs text-text-tertiary mt-0.5">失败</div>
              </div>
              <div className="rounded-md bg-elevated p-3">
                <div className="text-lg font-semibold tabular-nums text-brand-500">
                  {task.knowledge_count}
                </div>
                <div className="text-xs text-text-tertiary mt-0.5">知识条目</div>
              </div>
            </div>
            {/* 总进度条 */}
            <div className="mt-5 pt-4 border-t border-border-subtle">
              <div className="flex justify-between text-xs mb-1.5">
                <span className="text-text-tertiary">处理进度</span>
                <span className="font-mono text-text-primary">{progress}%</span>
              </div>
              <div className="h-2 rounded-full bg-elevated overflow-hidden">
                <div
                  className="h-full rounded-full bg-brand-500 transition-all duration-1000 ease-out"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          </Card>
        </section>

        {/* ============ 4. 阶段耗时表格（全宽） ============ */}
        <Card className="shadow-soft overflow-hidden">
          <div className="px-6 py-4 border-b border-border-subtle">
            <h3 className="text-sm font-semibold text-text-primary">阶段耗时</h3>
          </div>
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full border-collapse">
              <thead>
                <tr className="bg-elevated">
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    阶段
                  </th>
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    开始时间
                  </th>
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    结束时间
                  </th>
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    耗时
                  </th>
                </tr>
              </thead>
              <tbody>
                {stageDurations.map((stage, i) => {
                  const state = stageStates[i]
                  return (
                    <tr key={i} className="border-b border-border-subtle last:border-0">
                      <td className="px-6 py-3 text-sm text-text-primary">
                        <div className="flex items-center gap-2">
                          <span
                            className={`dot ${
                              state === 'completed'
                                ? 'dot-success'
                                : state === 'active'
                                ? 'dot-info'
                                : state === 'failed'
                                ? 'dot-error'
                                : 'dot-muted'
                            }`}
                          />
                          {stage.name}
                        </div>
                      </td>
                      <td className="px-6 py-3 text-sm font-mono tabular-nums text-text-secondary">
                        {stage.startTime}
                      </td>
                      <td className="px-6 py-3 text-sm font-mono tabular-nums text-text-secondary">
                        {stage.endTime}
                      </td>
                      <td
                        className={`px-6 py-3 text-sm font-mono tabular-nums ${
                          state === 'active'
                            ? 'text-brand-500'
                            : state === 'failed'
                            ? 'text-error'
                            : 'text-text-primary'
                        }`}
                      >
                        {stage.duration}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>

        {/* ============ 5. 部署历史（全宽） ============ */}
        <Card className="shadow-soft overflow-hidden">
          <div className="flex items-center justify-between px-6 py-4 border-b border-border-subtle">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-text-primary">部署历史</h3>
            </div>
          </div>
          {deploymentHistory.length === 0 ? (
            <div className="px-6 py-12 text-center">
              <p className="text-sm text-text-tertiary">暂无部署记录</p>
              <p className="text-xs text-text-muted mt-1">启动编译任务后将在此展示部署历史</p>
            </div>
          ) : (
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full border-collapse">
              <thead>
                <tr className="bg-elevated">
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    版本
                  </th>
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    环境
                  </th>
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    状态
                  </th>
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    耗时
                  </th>
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    部署时间
                  </th>
                  <th className="px-6 py-3 text-left text-xs uppercase text-text-tertiary font-medium">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                {deploymentHistory.map((record, i) => (
                  <tr
                    key={i}
                    className="border-b border-border-subtle last:border-0 hover:bg-elevated transition-colors"
                  >
                    <td className="px-6 py-3 text-sm font-mono text-text-primary whitespace-nowrap">
                      {record.version}
                    </td>
                    <td className="px-6 py-3 text-sm">
                      <span
                        className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${
                          record.environment === 'prod'
                            ? 'bg-brand-50 text-brand-500'
                            : 'bg-elevated text-text-tertiary'
                        }`}
                      >
                        {record.environment === 'prod' ? '生产环境' : '预发布'}
                      </span>
                    </td>
                    <td className="px-6 py-3 text-sm whitespace-nowrap">
                      <span className="inline-flex items-center gap-1.5 text-text-primary">
                        <span
                          className={`dot ${
                            record.status === 'success'
                              ? 'dot-success'
                              : record.status === 'failed'
                              ? 'dot-error'
                              : 'dot-warning'
                          }`}
                        />
                        {record.status === 'success'
                          ? '成功'
                          : record.status === 'failed'
                          ? '失败'
                          : '构建中'}
                      </span>
                    </td>
                    <td
                      className={`px-6 py-3 text-sm font-mono whitespace-nowrap ${
                        record.status === 'building'
                          ? 'text-brand-500'
                          : 'text-text-primary'
                      }`}
                    >
                      {record.duration}
                    </td>
                    <td className="px-6 py-3 text-sm font-mono text-text-tertiary whitespace-nowrap">
                      {record.deployedAt}
                    </td>
                    <td className="px-6 py-3 text-sm">
                      <div className="flex items-center gap-3">
                        <button className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-brand-500 transition-colors">
                          <FileText className="w-3.5 h-3.5" />
                          详情
                        </button>
                        {record.status === 'success' ? (
                          <button className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-brand-500 transition-colors">
                            <RotateCcw className="w-3.5 h-3.5" />
                            回滚
                          </button>
                        ) : record.status === 'failed' ? (
                          <button className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-brand-500 transition-colors">
                            <RefreshCw className="w-3.5 h-3.5" />
                            重试
                          </button>
                        ) : (
                          <button className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-brand-500 transition-colors">
                            <List className="w-3.5 h-3.5" />
                            日志
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
        </Card>

        {/* ============ 处理完成报告（保留原有功能） ============ */}
        {task.status === 'completed' && report && (
          <Card className="p-6 shadow-soft border-success/30">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-12 h-12 bg-success/10 rounded-full flex items-center justify-center">
                <Check className="w-6 h-6 text-success" />
              </div>
              <div>
                <h2 className="text-xl font-semibold text-text-primary">处理完成</h2>
                <p className="text-text-secondary text-sm">所有文件已成功处理</p>
              </div>
            </div>
            {/* D3-M7/6.7: 文件监听已自动启动提示 */}
            {task.agent_id && (
              <div className="flex items-start gap-3 mb-6 p-3 rounded-lg bg-brand-50 border border-brand-500/20">
                <div className="w-8 h-8 rounded-full bg-brand-500/10 flex items-center justify-center flex-shrink-0">
                  <Eye className="w-4 h-4 text-brand-500" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-text-primary">文件监听已自动启动</p>
                  <p className="text-xs text-text-secondary mt-0.5">
                    知识库文件夹变更将自动触发增量更新，无需手动重建。可在「智能体编排」页面手动启停监听。
                  </p>
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
              <div className="p-4 bg-brand-50 rounded-lg text-center">
                <p className="text-3xl font-bold text-brand-500">{report.files_processed}</p>
                <p className="text-sm text-text-secondary">处理文件</p>
              </div>
              <div className="p-4 bg-success/10 rounded-lg text-center">
                <p className="text-3xl font-bold text-success">{report.knowledge_count}</p>
                <p className="text-sm text-text-secondary">知识片段</p>
              </div>
              <div className="p-4 bg-error/10 rounded-lg text-center">
                <p className="text-3xl font-bold text-error">{report.files_failed}</p>
                <p className="text-sm text-text-secondary">失败文件</p>
              </div>
            </div>
            <p className="text-text-secondary mb-6">{report.summary}</p>
            <div className="flex gap-3">
              <Button variant="primary" size="md" onClick={() => navigate('/')}>
                查看智能体
              </Button>
            </div>
          </Card>
        )}

        {/* ============ 处理中提示（保留原有功能） ============ */}
        {task.status === 'processing' && (
          <div className="text-center py-2">
            <p className="text-sm text-text-tertiary">正在处理中，请勿关闭页面…</p>
          </div>
        )}

        {/* ============ 错误日志（P1-FE: 展示任务级错误，便于排查） ============ */}
        {(task.status === 'failed' || task.status === 'cancelled') && (task.error_log?.length ?? 0) > 0 && (
          <Card className="p-6 shadow-soft border-error/30">
            <div className="flex items-center gap-2 mb-4">
              <AlertCircle className="w-5 h-5 text-error" />
              <h3 className="text-sm font-semibold text-text-primary">错误日志</h3>
            </div>
            <div className="space-y-2 max-h-64 overflow-y-auto scrollbar-thin">
              {task.error_log?.map((entry, i) => (
                <div
                  key={i}
                  className="text-xs font-mono p-3 rounded bg-error/5 border border-error/10"
                >
                  <div className="flex items-center gap-2 text-text-tertiary mb-1">
                    <span>{entry.timestamp || '—'}</span>
                    {entry.error_code && <span>[{entry.error_code}]</span>}
                    {entry.error_type && <span>({entry.error_type})</span>}
                  </div>
                  <div className="text-text-primary">
                    {entry.file && entry.file !== 'unknown' && (
                      <span className="text-text-secondary mr-2">{entry.file}:</span>
                    )}
                    {entry.error || task.message || '处理异常'}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
      {confirmDialog.dialog}
    </Layout>
  )
}
