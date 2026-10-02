/**
 * CompilePage — 五级编译页面（v2 重构）。
 *
 * 对应 PRD §6.5 前端可视化模块"企业编译动画" + spec.md §10.7 WT1 编译端点。
 *
 * 重构要点（v2）：
 * - 编译结果概览：4 个手写重复卡 → MetricGrid 统一组件
 * - 五级编译动画：升级版 CompilerAnimation，InlineTabs 切换时间线/横向流程图
 * - 完成度评估：CompletenessGauge 升级为五维雷达图（RadarGauge）
 * - 缺失项建议：平铺网格 → SectionGroup 折叠分组（默认折叠，badge 计数），去除与 CompletenessGauge 的 gaps 重复
 * - 空态处理：无企业 ID 时 EmptyState 引导
 * - Framer Motion：staggered 入场动画
 * - 修复 level 显示阈值（0-100 而非 0-1）
 *
 * 功能：
 * - 触发五级编译（POST /compiler/compile）— 异步模式，返回 job_id 后轮询状态
 * - 展示五级编译动画（CompilerAnimation）— 编译过程中实时更新
 * - 实时完成度更新（CompletenessGauge）
 * - 缺失项建议展示
 * - 增量重编译
 *
 * 异步编译流程：
 * 1. POST /compiler/compile → 返回 {job_id, status: "running"}
 * 2. 每 3 秒轮询 GET /compiler/jobs/{job_id} + GET /compiler/animation/{job_id}
 * 3. status === "completed" → 加载完成度 + 运行时数据
 * 4. status === "failed" → 显示错误信息
 *
 * 错误状态：API 失败时显示告警 + 重试按钮（spec.md §2.5 约束）。
 */
import { useState, useEffect, useCallback, useRef, type ChangeEvent, type InputHTMLAttributes } from 'react'
import { motion } from 'framer-motion'
import { Play, RefreshCw, AlertTriangle, CheckCircle2, Users, GitBranch, Box, TrendingUp, XCircle, Sparkles, History, Folder, FolderUp } from 'lucide-react'
import Layout from '@/components/Layout'

import { Button } from '@/components/ui/Button'
import { Card, CardBody } from '@/components/ui/Card'
import { Spinner } from '@/components/ui/Spinner'
import { ApiErrorState } from '@/components/ApiErrorState'
import { CompilerAnimation } from '@/components/CompilerAnimation'
import { CompletenessGauge } from '@/components/CompletenessGauge'
import { IdChip } from '@/components/ui/IdChip'
import { MetricGrid } from '@/components/ui/MetricGrid'
import { SectionGroup } from '@/components/ui/SectionGroup'
import { EmptyState } from '@/components/ui/EmptyState'
import * as compilerApi from '@/api/compiler'
import * as runtimeApi from '@/api/runtime'
import { uploadFolder } from '@/api/folders'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { toPercentValue } from '@/utils/format'
import type {
  CompilationAnimation,
  CompletenessResult,
  CompilationJob,
  CompilerStage,
  CompletenessLevel,
} from '@/types'

// 初始空阶段（pending 状态）
const INITIAL_STAGES: CompilerStage[] = [
  { name: 'information', status: 'pending', discovered: '', confidence: 0 },
  { name: 'knowledge', status: 'pending', discovered: '', confidence: 0 },
  { name: 'process', status: 'pending', discovered: '', confidence: 0 },
  { name: 'capability', status: 'pending', discovered: '', confidence: 0 },
  { name: 'runtime', status: 'pending', discovered: '', confidence: 0 },
]

const POLL_INTERVAL = 3000 // 3 秒轮询

/** 完成度 → 等级映射（PRD §4.3，0-100 尺度） */
function deriveLevel(overall?: number | null): CompletenessLevel | undefined {
  if (overall == null || Number.isNaN(overall)) return undefined
  if (overall >= 80) return 'runnable'
  if (overall >= 50) return 'basic'
  return 'incomplete'
}

/** 等级 → 中文标签 + 语气色 */
const LEVEL_META: Record<CompletenessLevel, { label: string; tone: 'success' | 'warning' | 'error' }> = {
  runnable: { label: '可运行', tone: 'success' },
  basic: { label: '基本可用', tone: 'warning' },
  incomplete: { label: '不完整', tone: 'error' },
}

/** #11 缺失项 gap_type 中文化（role→岗位 / knowledge→知识 / data→数据 / process→流程） */
const GAP_TYPE_LABELS: Record<string, string> = {
  data: '数据',
  process: '流程',
  role: '岗位',
  knowledge: '知识',
}

/** 编译任务状态 → 中文标签 + 徽标样式 */
function jobStatusBadge(status: CompilationJob['status']) {
  switch (status) {
    case 'completed': return { label: '已完成', cls: 'bg-success/10 text-success' }
    case 'failed': return { label: '失败', cls: 'bg-error/10 text-error' }
        case 'queued': return { label: '等待执行', cls: 'bg-info/10 text-info' }
    case 'running': return { label: '编译中', cls: 'bg-brand-50 text-brand-600' }
    case 'paused': return { label: '已暂停', cls: 'bg-warning/10 text-warning' }
    case 'cancelled': return { label: '已取消', cls: 'bg-elevated text-text-tertiary' }
    default: return { label: status, cls: 'bg-elevated text-text-tertiary' }

  }
}

export default function CompilePage() {
  const enterpriseId = useEnterpriseId()
  const [animation, setAnimation] = useState<CompilationAnimation | null>(null)
  const [completeness, setCompleteness] = useState<CompletenessResult | null>(null)
  const [job, setJob] = useState<CompilationJob | null>(null)
  const [lastResult, setLastResult] = useState<compilerApi.CompileResponseWithSource | null>(null)
  const [runtimeAgentCount, setRuntimeAgentCount] = useState<number>(0)
  const [runtimeProcessCount, setRuntimeProcessCount] = useState<number>(0)
  const [loading, setLoading] = useState(false)
  const [compiling, setCompiling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pollingNotice, setPollingNotice] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // 请求耗时超过轮询间隔时，跳过本轮而非并行读取同一任务的旧快照。
  const pollInFlightRef = useRef(false)

  /* ---- #17 数据源选择：演示数据 / 用户上传本地文件夹 ----
     用户通过 <input type="file" webkitdirectory> 任选本机任意文件夹，
     由 uploadFolder 上传到服务端暂存，返回可扫描的 folder_path 作为编译数据源。 */
  const [dataSource, setDataSource] = useState<'default' | 'folder'>('default')
  const [selectedFolderName, setSelectedFolderName] = useState<string>('')
  const [selectedFolderPath, setSelectedFolderPath] = useState<string>('')
  const [folderUploading, setFolderUploading] = useState(false)
  const folderInputRef = useRef<HTMLInputElement>(null)
  /* 记录编译触发时的数据源信息，供轮询完成时回填，避免被轮询结果覆盖 */
  const lastSourceRef = useRef<{ source?: 'default' | 'user_upload'; folder_name?: string | null }>({})

  /** 停止轮询 */
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

    /** 轮询编译任务状态 */
  const pollJobStatus = useCallback(async (jobId: string) => {
    if (pollInFlightRef.current) return
    pollInFlightRef.current = true
    try {

      const [jobData, animData] = await Promise.all([
        compilerApi.getCompilationJob(jobId),
        compilerApi.getCompilationAnimation(jobId).catch(() => null),
      ])
      setJob(jobData)
      if (animData) {
        setAnimation(animData)
      }

            setPollingNotice(null)
      if (jobData.status === 'completed') {
        stopPolling()

        setCompiling(false)
        // 加载完成度和运行时数据
        try {
          const [compData, rt] = await Promise.all([
            compilerApi.getCompleteness(enterpriseId).catch(() => null),
            runtimeApi.getRuntime(enterpriseId).catch(() => null),
          ])
          if (compData) setCompleteness(compData)
          if (rt) {
            setRuntimeAgentCount(rt.agents?.length ?? 0)
            setRuntimeProcessCount(rt.process_engines?.length ?? 0)
          }
          setLastResult({
            job_id: jobId,
            status: 'completed',
            completeness: jobData.completeness,
            level: deriveLevel(compData?.overall),
            runtime_version: rt?.version,
            agent_count: rt?.agents?.length ?? 0,
            process_count: rt?.process_engines?.length ?? 0,
            ...lastSourceRef.current,
          })
        } catch {
          // 即使运行时加载失败，编译本身已完成
          setLastResult({
            job_id: jobId,
            status: 'completed',
            completeness: jobData.completeness,
            ...lastSourceRef.current,
          })
        }
      } else if (jobData.status === 'failed') {
        stopPolling()
        setCompiling(false)
        const errMsg = jobData.error_message || jobData.error || '编译失败'
        setError(errMsg)
      }
    } catch (err) {
      // 轮询本身出错不停止任务：后端队列仍会继续推进，页面在下一轮自动恢复同步。
      console.warn('轮询编译状态失败:', err)
      setPollingNotice('暂时无法同步编译进度，系统会继续自动重试。')
    } finally {
      pollInFlightRef.current = false
    }
  }, [enterpriseId, stopPolling])

  /** 启动对指定编译任务的轮询（先立即轮询一次，再定时续传） */
  const startPolling = useCallback((jobId: string) => {
    stopPolling()
    pollJobStatus(jobId)
    pollRef.current = setInterval(() => {
      pollJobStatus(jobId)
    }, POLL_INTERVAL)
  }, [pollJobStatus, stopPolling])

  /** 组件卸载时清理轮询 */
  useEffect(() => {
    return () => stopPolling()
  }, [stopPolling])

  const loadData = useCallback(async () => {
    if (!enterpriseId) return
    setLoading(true)
    setError(null)
    try {
      const [compData] = await Promise.all([
        compilerApi.getCompleteness(enterpriseId).catch(() => null),
      ])
      setCompleteness(compData)
      // 动画数据：尝试用最近 job，失败则用初始空阶段（后端可能无历史 job）
      try {
        const jobsResp = await compilerApi.listCompilationJobs(enterpriseId, 1, 0)
        if (jobsResp.items.length > 0) {
          const latestJob = jobsResp.items[0]
          setJob(latestJob)
          const animData = await compilerApi.getCompilationAnimation(latestJob.job_id)
          setAnimation(animData)
          // 如果最近任务是已完成状态，加载运行时数据
          if (latestJob.status === 'completed') {
            try {
              const rt = await runtimeApi.getRuntime(enterpriseId)
              setRuntimeAgentCount(rt.agents?.length ?? 0)
              setRuntimeProcessCount(rt.process_engines?.length ?? 0)
              setLastResult({
                job_id: latestJob.job_id,
                status: 'completed',
                completeness: latestJob.completeness,
                level: deriveLevel(compData?.overall),
                runtime_version: rt.version,
                agent_count: rt.agents?.length ?? 0,
                process_count: rt.process_engines?.length ?? 0,
              })
            } catch {
              // runtime 可能不存在
            }
                    } else if (latestJob.status === 'queued' || latestJob.status === 'running' || latestJob.status === 'paused') {

            // 页面返回时若最近一次编译仍在运行/暂停，续传轮询进度，
            // 否则任务会一直停留在上次快照，看起来「没有继续」。
            setCompiling(true)
            startPolling(latestJob.job_id)
          }
        }
      } catch {
        // 无历史 job 时保留初始空阶段
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载编译数据失败')
    } finally {
      setLoading(false)
    }
  }, [enterpriseId, startPolling])

  useEffect(() => {
    loadData()
  }, [loadData])

  /* ---- #17 本地文件夹上传（webkitdirectory）---- */
  const handleFolderChange = useCallback(async (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    e.target.value = '' // 允许重复选择同一文件夹
    if (files.length === 0) return
    setFolderUploading(true)
    setError(null)
    try {
      const res = await uploadFolder(files)
      setSelectedFolderPath(res.folder_path)
      setSelectedFolderName(res.folder_name)
      setDataSource('folder')
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传文件夹失败')
    } finally {
      setFolderUploading(false)
    }
  }, [])

    const handleCompile = async () => {
    if (!enterpriseId || compiling || replaying || folderUploading) return

        setCompiling(true)
    setError(null)
    setPollingNotice(null)
    // 重置动画为初始状态

    setAnimation({ stages: INITIAL_STAGES })
    try {
      const result = await compilerApi.compile({
        enterprise_id: enterpriseId,
        trigger_source: 'manual',
      }, { folderPath: dataSource === 'folder' && selectedFolderPath ? selectedFolderPath : undefined })
      // 异步模式：后端返回 {job_id, status: "running"}
      // 启动轮询
      if (result.job_id) {
        lastSourceRef.current = { source: result.source, folder_name: result.folder_name }
        setLastResult(result)
        // 立即轮询 + 定时续传
        startPolling(result.job_id)
      } else {
        // 兼容旧版同步响应（status === "completed"）
        setLastResult(result)
        setCompiling(false)
        await loadData()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '触发编译失败')
      setCompiling(false)
    }
  }

    const handleRecompile = async () => {
    if (!enterpriseId || compiling || replaying || folderUploading) return

    setCompiling(true)
    setError(null)
    setAnimation({ stages: INITIAL_STAGES })
    // 增量重编译基于已存在的演示数据源，不沿用上次的用户上传文件夹来源
    lastSourceRef.current = {}
    try {
      const result = await compilerApi.recompile({
        enterprise_id: enterpriseId,
        trigger_source: 'manual',
      })
            if (result.job_id && (result.status === 'running' || result.status === 'queued')) {

        setLastResult(result)
        startPolling(result.job_id)
      } else {
        setLastResult(result)
        setCompiling(false)
        await loadData()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '增量重编译失败')
      setCompiling(false)
    }
  }

  // ============================================================
  // 编译回放（UI v4 §九 —— 路演/评委远程体验方案）
  // ------------------------------------------------------------
  // 真实五级编译需 10-20 分钟，现场无法等待。回放模式播放**最近一次
  // 成功编译的真实产物与真实时序**，按 duration_ms 相对比例加速。
  // 数据 100% 真实，仅压缩时间轴；UI 明确标注「回放」，不冒充实时编译。
  // ============================================================
  const [replayAvailable, setReplayAvailable] = useState(false)
  const [replaying, setReplaying] = useState(false)
  const replayTimersRef = useRef<ReturnType<typeof setTimeout>[]>([])

  // 探测是否有可回放的历史编译
  useEffect(() => {
    if (!enterpriseId) return
    let cancelled = false
    compilerApi
      .getCompilationReplay(enterpriseId)
      .then((r) => {
        if (!cancelled) setReplayAvailable(Boolean(r.available))
      })
      .catch(() => {
        if (!cancelled) setReplayAvailable(false)
      })
    return () => {
      cancelled = true
    }
  }, [enterpriseId])

  const clearReplayTimers = useCallback(() => {
    replayTimersRef.current.forEach(clearTimeout)
    replayTimersRef.current = []
  }, [])

  useEffect(() => clearReplayTimers, [clearReplayTimers])

  const handleReplay = useCallback(async () => {
    if (!enterpriseId || replaying) return
    setError(null)
    clearReplayTimers()
    try {
      const data = await compilerApi.getCompilationReplay(enterpriseId)
      if (!data.available || data.stages.length === 0) {
        setError(data.reason || '暂无可回放的编译记录，请先执行一次完整编译')
        return
      }

      setReplaying(true)
      setAnimation({ stages: INITIAL_STAGES })

      // 时间轴压缩：真实总耗时映射到 REPLAY_TOTAL_MS，各级按真实占比分配，
      // 保留「哪一级慢、哪一级快」的真实节奏差异。
      const REPLAY_TOTAL_MS = 12000
      const realTotal = data.stages.reduce((s, st) => s + (st.duration_ms || 0), 0)
      let cursor = 0

      data.stages.forEach((st, idx) => {
        const share = realTotal > 0 ? (st.duration_ms || 0) / realTotal : 1 / data.stages.length
        const slot = Math.max(900, REPLAY_TOTAL_MS * share)

        // 进入该级：标记 running
        const enterAt = cursor
        replayTimersRef.current.push(
          setTimeout(() => {
            setAnimation({
              stages: data.stages.map((s, i) => ({
                name: s.name as CompilerStage['name'],
                status: i < idx ? 'completed' : i === idx ? 'running' : 'pending',
                discovered: i <= idx ? s.discovered : '',
                confidence: i < idx ? s.confidence : null,
                duration_ms: s.duration_ms,
              })) as CompilerStage[],
            })
          }, enterAt),
        )

        cursor += slot

        // 该级完成
        const doneAt = cursor
        replayTimersRef.current.push(
          setTimeout(() => {
            const isLast = idx === data.stages.length - 1
            setAnimation({
              stages: data.stages.map((s, i) => ({
                name: s.name as CompilerStage['name'],
                status: i <= idx ? 'completed' : 'pending',
                discovered: i <= idx ? s.discovered : '',
                confidence: i <= idx ? s.confidence : null,
                duration_ms: s.duration_ms,
              })) as CompilerStage[],
            })
            if (isLast) {
              setReplaying(false)
              // 回放结束后拉取真实完成度，画面与数据保持一致
              void loadData()
            }
          }, doneAt),
        )
      })
    } catch (err) {
      setReplaying(false)
      setError(err instanceof Error ? err.message : '加载回放数据失败')
    }
  }, [enterpriseId, replaying, clearReplayTimers, loadData])

  const stages = animation?.stages ?? INITIAL_STAGES
  const completedCount = stages.filter((s) => s.status === 'completed').length
  /**
   * 编译进度：优先使用后端真实 progress（UI v4 起 Pipeline 逐级写入，0-1 归一化）。
   *
   * 此前前端用「已完成阶段数 / 5」估算，导致 10-20 分钟的编译长期卡在
   * 20% 的阶梯跳变上（后端 progress 字段因模型缺列恒为 0，已在 P3 修复）。
   * 后端无值时（旧任务数据）仍回退到阶段估算，保证向后兼容。
   */
  const progress =
    typeof job?.progress === 'number' && job.progress > 0
      ? toPercentValue(job.progress)
      : (completedCount / stages.length) * 100

  // 当前编译阶段（用于显示"正在编译 XXX..."）
  const currentStage = stages.find((s) => s.status === 'running')
  const currentStageLabel = currentStage
    ? ({ information: '信息编译', knowledge: '知识图谱', process: '流程编译', capability: '能力矩阵', runtime: '运行时构建' } as const)[currentStage.name]
    : null

  if (loading && !completeness) {
    return (
      <Layout>
        <div className="flex items-center justify-center py-20">
          <Spinner size="lg" />
        </div>
      </Layout>
    )
  }

  if (!enterpriseId) {
    return (
      <Layout>
        <div className="max-w-3xl mx-auto w-full px-4 sm:px-6 py-8">
          <EmptyState
            icon={Box}
            title="未检测到企业信息"
            description="请先登录或联系管理员分配企业，才能开始五级编译并生成 Enterprise Runtime。"
            variant="warning"
          />
        </div>
      </Layout>
    )
  }

  const displayAgentCount = lastResult?.agent_count ?? runtimeAgentCount
  const displayProcessCount = lastResult?.process_count ?? runtimeProcessCount
  // 后端 completeness 为 0-1 分数，展示统一换算为 0-100 百分比（避免显示 0.63% 等错误单位）
  const displayCompleteness = lastResult?.completeness ?? job?.completeness ?? completeness?.overall ?? 0
  const completenessPct = displayCompleteness * 100
  const currentLevel = deriveLevel(completenessPct)
  const hasResult = !!(lastResult || job)
  const gaps = completeness?.gaps ?? []

  // 编译结果指标（MetricGrid）
  const resultMetrics = hasResult ? [
    {
      key: 'completeness',
      icon: TrendingUp,
      value: Math.round(completenessPct),
      unit: '%',
      label: '完成度',
      tone: (currentLevel === 'runnable' ? 'success' : currentLevel === 'basic' ? 'warning' : 'error') as 'success' | 'warning' | 'error',
    },
    {
      key: 'level',
      icon: Box,
      value: currentLevel ? LEVEL_META[currentLevel].label : '—',
      label: '运行时等级',
      tone: (currentLevel ? LEVEL_META[currentLevel].tone : 'brand') as 'success' | 'warning' | 'error' | 'brand',
    },
    {
      key: 'agents',
      icon: Users,
      value: displayAgentCount,
      label: 'Agent 数',
      tone: 'info' as const,
    },
    {
      key: 'processes',
      icon: GitBranch,
      value: displayProcessCount,
      label: '流程数',
      tone: 'brand' as const,
    },
  ] : []

  // 容器入场动画 variants
  const containerVariants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
  }
  const itemVariants = {
    hidden: { opacity: 0, y: 12 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: 'easeOut' as const } },
  }

  return (
    <Layout>
      <motion.div
                className="w-full space-y-7 pb-4"

        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {/* 构建首屏：先解释当前动作，再呈现少量高优先级操作，避免标题、状态、按钮分散在多块卡片。 */}
        <motion.section variants={itemVariants} className="ui-card overflow-hidden rounded-2xl border border-border-default">
          <div className="grid gap-6 p-5 sm:p-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
            <div className="min-w-0">
              <p className="ui-context-kicker">五级编译流程</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-text-primary">把资料转化为可运行的企业能力</h2>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-text-secondary">
                选择数据源后启动耐久编译任务。任务会在后台持续执行，可随时返回查看进度、产物与缺失项建议。
              </p>
              <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-text-tertiary">
                <span className="inline-flex items-center gap-1.5"><span className="dot dot-info" />信息与知识</span>
                <span className="inline-flex items-center gap-1.5"><span className="dot dot-info" />流程与能力</span>
                <span className="inline-flex items-center gap-1.5"><span className="dot dot-success" />运行时交付</span>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 lg:justify-end">
              <Button
                variant="primary"
                onClick={handleCompile}
                disabled={compiling || replaying || folderUploading || !enterpriseId}
                title="创建可恢复的五级编译任务。真实编译通常需要数分钟到数十分钟。"
              >
                {compiling ? <RefreshCw className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Play className="w-4 h-4" aria-hidden="true" />}
                {compiling ? '编译中' : '开始编译'}
              </Button>
              <Button
                variant="secondary"
                onClick={handleRecompile}
                disabled={compiling || replaying || folderUploading || !enterpriseId}
              >
                <RefreshCw className={`w-4 h-4 ${compiling ? 'animate-spin' : ''}`} aria-hidden="true" />
                增量重编译
              </Button>
              {replayAvailable && (
                <Button
                  variant="ghost"
                  onClick={handleReplay}
                  disabled={compiling || replaying || folderUploading || !enterpriseId}
                  title="加速回放最近一次成功编译的真实产物与时序，不会重新提交任务。"
                >
                  <History className={`w-4 h-4 ${replaying ? 'animate-pulse' : ''}`} aria-hidden="true" />
                  {replaying ? '回放中' : '查看回放'}
                </Button>
              )}
            </div>
          </div>
        </motion.section>

        {/* #17 数据源选择：演示数据 / 用户上传本地文件夹（评委上传测试并存） */}
        <motion.div variants={itemVariants}>
                    <fieldset className="ui-card rounded-2xl border border-border-default p-5">
            <legend className="sr-only">编译数据源</legend>
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--surface-tint)]">
                  <Folder className="w-4 h-4 text-brand-500" aria-hidden="true" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-text-primary">选择编译数据源</p>
                  <p className="mt-0.5 text-xs text-text-tertiary">内置示例用于快速体验，文件夹可用于真实资料编译。</p>
                </div>
              </div>
              <span className="text-xs font-medium text-text-tertiary">当前：{dataSource === 'folder' ? '用户上传文件夹' : '内置示例数据'}</span>
            </div>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
              <div className="flex items-center gap-4">

                <label className="inline-flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
                  <input
                    type="radio"
                    name="dataSource"
                    checked={dataSource === 'default'}
                    onChange={() => setDataSource('default')}
                    className="h-4 w-4 accent-brand-500"
                  />
                  演示数据
                </label>
                <label className="inline-flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
                  <input
                    type="radio"
                    name="dataSource"
                    checked={dataSource === 'folder'}
                    onChange={() => setDataSource('folder')}
                    className="h-4 w-4 accent-brand-500"
                  />
                  用户上传文件夹
                </label>
              </div>
              <div className="flex items-center gap-2 flex-1 min-w-0">
                {dataSource === 'folder' ? (
                  <>
                    <button
                      type="button"
                      onClick={() => folderInputRef.current?.click()}
                      disabled={folderUploading || compiling || replaying}
                                            className="ui-control inline-flex items-center gap-2 text-sm text-text-primary bg-[var(--surface-sunken)] border border-border-default px-3 min-w-0 flex-1 truncate hover:bg-[var(--surface-tint)]"

                      title="点击浏览本机文件夹并上传作为编译数据源"
                    >
                      <FolderUp className="w-4 h-4 text-brand-500 flex-shrink-0" aria-hidden="true" />
                      <span className="truncate">
                        {folderUploading
                          ? '正在上传文件夹...'
                          : selectedFolderName || '未选择文件夹（点击浏览选择）'}
                      </span>
                    </button>
                    {selectedFolderName && (
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-success/10 text-success flex-shrink-0">
                        已上传 {selectedFolderName}
                      </span>
                    )}
                  </>
                ) : (
                  <span className="text-xs text-text-tertiary">
                    使用内置示例企业数据（example-enterprise）作为编译数据源
                  </span>
                )}
              </div>
            </div>
            {dataSource === 'folder' && !selectedFolderName && (
              <p className="text-xs text-text-tertiary mt-2">
                点击上方按钮，从本机任意位置选择文件夹，上传后即可作为编译数据源。
              </p>
            )}
                    </fieldset>
          {/* 隐藏的原生文件夹选择器：webkitdirectory 支持任选本机文件夹 */}

          <input
            ref={folderInputRef}
            type="file"
            className="hidden"
            multiple
            {...({ webkitdirectory: '' } as InputHTMLAttributes<HTMLInputElement>)}
            onChange={handleFolderChange}
          />
        </motion.div>

        {/* 回放状态提示：明确标注为「回放」，绝不冒充实时编译 */}
        {replaying && (
          <motion.div
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            className="flex items-center gap-3 rounded-lg bg-info/10 border border-info p-4"
          >
            <History className="w-5 h-5 text-info flex-shrink-0 animate-pulse" aria-hidden="true" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-info">
                正在回放最近一次真实编译
              </p>
              <p className="text-xs text-text-tertiary mt-0.5">
                展示的产物、置信度与各级耗时占比均为真实编译数据，仅时间轴被压缩加速
              </p>
            </div>
          </motion.div>
        )}

        {/* 编译进度提示：任务刚创建、排队、运行与短暂网络失联都给出连续反馈。 */}
        {compiling && (
          <motion.div
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            className={`flex items-center gap-3 rounded-xl border p-4 ${
              pollingNotice ? 'border-warning/30 bg-warning/10' : 'border-brand-200 bg-brand-50'
            }`}
            role="status"
            aria-live="polite"
          >
            <RefreshCw className={`h-5 w-5 shrink-0 animate-spin ${pollingNotice ? 'text-warning' : 'text-brand-500'}`} aria-hidden="true" />
            <div className="min-w-0">
              <p className={`text-sm font-medium ${pollingNotice ? 'text-warning' : 'text-brand-700'}`}>
                {pollingNotice
                  ? '正在恢复进度同步'
                  : job?.status === 'queued'
                    ? '任务正在等待 Worker 执行'
                    : currentStageLabel
                      ? `正在执行：${currentStageLabel}...`
                      : '正在创建并同步编译任务'}
              </p>
              <p className={`mt-0.5 text-xs ${pollingNotice ? 'text-warning' : 'text-brand-600'}`}>
                {pollingNotice
                  ? pollingNotice
                  : job?.status === 'queued'
                    ? '任务已安全写入耐久队列，开始后会自动更新到当前阶段。'
                    : currentStageLabel
                      ? '编译涉及多轮 AI 分析，通常需要 10–20 分钟；你可以安全离开后再返回查看。'
                      : '请求已提交，正在获取任务状态。'}
              </p>
              {pollingNotice && job?.job_id && (
                <button
                  type="button"
                  onClick={() => void pollJobStatus(job.job_id)}
                  className="mt-2 text-xs font-medium text-warning underline underline-offset-2 transition-colors hover:text-warning/80 focus:outline-none focus:ring-2 focus:ring-warning/30"
                >
                  立即重试同步
                </button>
              )}
            </div>
            <span className={`ml-auto shrink-0 font-mono text-xs ${pollingNotice ? 'text-warning' : 'text-brand-500'}`}>
              {job?.status === 'queued' ? '队列中' : currentStageLabel ? `${Math.round(progress)}%` : '同步中'}
            </span>
          </motion.div>
        )}

        {/* 错误状态 */}
        {error && (
          <motion.div variants={itemVariants}>
            <ApiErrorState
              message={error}
              onRetry={loadData}
              retrying={loading}
            />
          </motion.div>
        )}

        {/* 编译失败详情：后端 error_message 此前无处呈现，
            用户只能看到一个灰圈，不知道失败在哪一级、为什么失败 */}
        {job?.status === 'failed' && job.error_message && (
          <motion.div variants={itemVariants}>
            <div className="rounded-lg border border-error bg-error/10 p-4">
              <div className="flex items-start gap-3">
                <XCircle className="w-5 h-5 text-error flex-shrink-0 mt-0.5" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-error">
                    编译在「{currentStageLabel || job.stage}」阶段失败
                  </p>
                  <p className="text-xs text-text-secondary mt-1 break-words font-mono">
                    {job.error_message}
                  </p>
                  <p className="text-xs text-text-tertiary mt-2">
                    可修正数据源后重新编译；已完成阶段的产物已保留，可使用「增量重编译」跳过前置阶段。
                  </p>
                </div>
              </div>
            </div>
          </motion.div>
        )}

        {/* 编译结果概览（最近一次编译产物） */}
        {hasResult && (
          <motion.div variants={itemVariants} className="space-y-3">
            <Card>
              <CardBody>
                <div className="flex items-center gap-2 mb-4">
                  {job?.status === 'failed' ? (
                    <XCircle className="w-5 h-5 text-error" aria-hidden="true" />
                  ) : (
                    <CheckCircle2 className="w-5 h-5 text-success" aria-hidden="true" />
                  )}
                  <h3 className="text-sm font-semibold text-text-primary">编译结果</h3>
                  <span className="text-xs text-text-tertiary">最近一次编译的产出与运行时统计</span>
                </div>

                <MetricGrid metrics={resultMetrics} columns={4} />

                {/* Job 元信息条 */}
                {job && (
                  <div className="mt-4 flex items-center gap-3 text-xs text-text-tertiary flex-wrap">
                    <span className="flex items-center gap-1.5">任务 ID：<IdChip id={job.job_id} maxLength={20} /></span>
                    <span className="text-text-muted">·</span>
                    <span>状态：
                      <span className={`ml-1 inline-flex items-center px-1.5 py-0.5 rounded ${jobStatusBadge(job.status).cls}`}>
                        {jobStatusBadge(job.status).label}
                      </span>
                    </span>
                    {/* #17 数据源标记：仅当确为用户上传文件夹时展示，演示数据不展示 */}
                    {lastResult?.source === 'user_upload' && (
                      <>
                        <span className="text-text-muted">·</span>
                        <span className="inline-flex items-center gap-1.5">
                          <FolderUp className="w-3.5 h-3.5 text-brand-500" aria-hidden="true" />
                          数据源：用户上传文件夹
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-brand-50 text-brand-600 font-medium">
                            {lastResult.folder_name || '已选择'}
                          </span>
                        </span>
                      </>
                    )}
                    {job.confidence > 0 && (
                      <>
                        <span className="text-text-muted">·</span>
                        <span>置信度：<code className="font-mono text-text-secondary">{Math.round(job.confidence * 100)}%</code></span>
                      </>
                    )}
                    {lastResult?.runtime_version && (
                      <>
                        <span className="text-text-muted">·</span>
                        <span>运行时版本：<code className="font-mono text-brand-500">{lastResult.runtime_version}</code></span>
                      </>
                    )}
                  </div>
                )}

                {/* 失败详情告警 */}
                {job?.status === 'failed' && job.error_message && (
                  <div className="mt-3 flex items-start gap-2 rounded-md bg-error/5 border border-error/20 p-3 text-xs text-error">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" aria-hidden="true" />
                    <div>
                      <p className="font-medium mb-1">编译失败</p>
                      <p className="text-error/80">{job.error_message}</p>
                    </div>
                  </div>
                )}

                {/* 低完成度引导告警 */}
                {job?.status === 'completed' && completenessPct < 50 && (
                  <div className="mt-3 flex items-start gap-2 rounded-md bg-warning/5 border border-warning/20 p-3 text-xs text-warning">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" aria-hidden="true" />
                    <div>
                      当前完成度较低（{Math.round(completenessPct)}%），运行时等级为「{currentLevel ? LEVEL_META[currentLevel].label : '不完整'}」。
                      建议补充下方缺失项的数据后重新编译，达到更高等级后即可在「企业运行时」页查看完整 Runtime。
                    </div>
                  </div>
                )}
              </CardBody>
            </Card>
          </motion.div>
        )}

        {/* 无编译历史引导（仅有企业 ID 但从未编译过） */}
        {!hasResult && !compiling && !error && (
          <motion.div variants={itemVariants}>
            <Card>
              <CardBody>
                <EmptyState
                  icon={Sparkles}
                  title="尚未执行编译"
                  description="五级编译器将原始文件、表格、聊天记录逐级升维为可运行的 Enterprise Runtime。点击「触发编译」开始首次编译。"
                  variant="brand"
                  action={{ label: '触发首次编译', onClick: handleCompile }}
                />
              </CardBody>
            </Card>
          </motion.div>
        )}

        {/* 五级编译动画 */}
        <motion.div variants={itemVariants}>
          <CompilerAnimation
            stages={stages}
            progress={progress}
            isCompiling={compiling}
          />
        </motion.div>

        {/* 完成度评估（五维雷达图） */}
        <motion.div variants={itemVariants}>
          <CompletenessGauge completeness={completeness} loading={false} />
        </motion.div>

        {/* 缺失项与建议（折叠分组，避免 endless scrolling） */}
        {gaps.length > 0 && (
          <motion.div variants={itemVariants}>
            <SectionGroup
              title="缺失项与建议"
              icon={<AlertTriangle className="w-4 h-4" aria-hidden="true" />}
              badge={
                <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-warning/10 text-warning text-xs font-medium">
                  {gaps.length} 项
                </span>
              }
              description="补充以下数据可提升完成度，引导 AI 员工执行准确率提升"
              defaultOpen={false}
            >
              <div className="p-4">
                <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                  {gaps.map((gap, idx) => (
                    <motion.div
                      key={idx}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.3, delay: idx * 0.05 }}
                      className="rounded-lg border border-warning/30 bg-warning/5 p-4"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-warning/10 text-warning">
                          {GAP_TYPE_LABELS[gap.gap_type] ?? gap.gap_type}
                        </span>
                        <span className="text-xs text-success font-medium">
                          +{Math.round(gap.impact_on_completeness)}%
                        </span>
                      </div>
                      <p className="text-sm text-text-primary mb-2">{gap.description}</p>
                      <p className="text-xs text-text-tertiary">{gap.suggestion}</p>
                    </motion.div>
                  ))}
                </div>
              </div>
            </SectionGroup>
          </motion.div>
        )}
      </motion.div>
    </Layout>
  )
}
