import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import Layout from '@/components/Layout'
import { PageHeader } from '@/components/ui/PageHeader'

import KnowledgeMaintenancePanel from '@/components/KnowledgeMaintenancePanel'
import { Card, CardBody } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'

import { Spinner } from '@/components/ui/Spinner'
import { Dialog } from '@/components/ui/Dialog'
import { ModalShell } from '@/components/ui/ModalShell'
import { useConfirmDialog } from '@/hooks/useConfirmDialog'
import { useAuth } from '@/hooks/useAuth'
import { MetricGrid, type Metric } from '@/components/ui/MetricGrid'
import { InlineTabs } from '@/components/ui/InlineTabs'
import { SectionGroup } from '@/components/ui/SectionGroup'
import { EmptyState } from '@/components/ui/EmptyState'
import { useEnterpriseId } from '@/hooks/useEnterpriseId'
import { EnterpriseCognition } from '@/components/EnterpriseCognition'
import { IdChip } from '@/components/ui/IdChip'

/**
 * 将用户 UUID 格式化为易读标签（#11：避免暴露原始 ID）。
 * 当前用户显示「我」，其他用户显示「用户 xxxxxxxx」短标识。
 */
function formatUserLabel(userId: string | undefined | null): string {
  if (!userId) return '—'
  return `用户 ${userId.slice(0, 8)}`
}
import { getAgents, getKnowledgeStats, updateAgent } from '@/api/agents'
import { listFiles, deleteFile, previewFile, batchUploadFiles, getFile, grantConfidentialAccess, revokeConfidentialAccess, listConfidentialAccess, type FileRecord, type ConfidentialAccess } from '@/api/files'
import { Agent } from '@/types'
import {
  Upload,
  Search,
  FileText,
  HardDrive,
  Eye,
  Trash2,
  RotateCw,
  RefreshCw,
  FileCode,
  Image as ImageIcon,
  Video,
  Music,
  FileJson,
  BarChart3,
  Settings2,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  X,
  Brain,
  Inbox,
  FolderUp,
  FileUp,
  ShieldCheck,
} from 'lucide-react'

/* ------------------------------------------------------------------ */
/*  类型定义                                                           */
/* ------------------------------------------------------------------ */

interface AgentInfo {
  id: string
  name: string
  status: string
  // P1-FE: 保留原始 config 以便加载知识库配置
  config?: Record<string, unknown> | null
}

interface StatsData {
  fileTypeDistribution: { name: string; value: number; color: string }[]
  knowledgeTimeline: { date: string; count: number }[]
  fileStatus: { name: string; value: number }[]
  totalFiles: number
  totalChunks: number
  agentName: string
}

/** 前端展示用的文件类型（中文标签） */
type FileType = '文档' | '图片' | '视频' | '音频' | '代码' | '数据' | '其他'
type FileStatus = 'indexed' | 'processing' | 'pending' | 'failed' | 'uploaded'

/** 前端展示用的文件结构（基于后端 FileRecord 映射） */
interface KnowledgeFile {
  id: string
  name: string
  type: FileType
  size: string
  sizeBytes: number
  status: FileStatus
  chunks: number
  updatedAt: string
  source: string
  errorMessage?: string | null
  /** 是否为极度私密/涉密文件（决定是否显示授权管理按钮） */
  isHighlyConfidential?: boolean
  /** 涉密状态：'authorized' / 'pending' / null */
  confidentialStatus?: string | null
}

/* ------------------------------------------------------------------ */
/*  后端 → 前端映射                                                     */
/* ------------------------------------------------------------------ */

/** 后端 file_type → 前端中文类型 */
const fileTypeMap: Record<string, FileType> = {
  document: '文档',
  image: '图片',
  video: '视频',
  audio: '音频',
  code: '代码',
  data: '数据',
  spreadsheet: '数据',
  presentation: '文档',
}

/** 后端 status → 前端展示状态
 * 后端 status 取值：uploaded / processing / indexed / failed / pending
 * uploaded 视为 pending（刚上传待处理） */
const statusMap: Record<string, FileStatus> = {
  uploaded: 'pending',
  pending: 'pending',
  processing: 'processing',
  indexed: 'indexed',
  vectorized: 'indexed',
  failed: 'failed',
  error: 'failed',
}

const fileTypeIcon: Record<FileType, typeof FileText> = {
  '文档': FileText,
  '图片': ImageIcon,
  '视频': Video,
  '音频': Music,
  '代码': FileCode,
  '数据': FileJson,
  '其他': FileText,
}

const statusConfig: Record<FileStatus, { label: string; dot: string; text: string }> = {
  indexed: { label: '已索引', dot: 'dot-success', text: 'text-success' },
  processing: { label: '索引中', dot: 'dot-warning', text: 'text-warning' },
  pending: { label: '待处理', dot: 'dot-warning', text: 'text-warning' },
  failed: { label: '索引失败', dot: 'dot-error', text: 'text-error' },
  uploaded: { label: '待处理', dot: 'dot-warning', text: 'text-warning' },
}

const fileTypeOptions: { value: FileType | 'all'; label: string }[] = [
  { value: 'all', label: '全部类型' },
  { value: '文档', label: '文档' },
  { value: '图片', label: '图片' },
  { value: '视频', label: '视频' },
  { value: '音频', label: '音频' },
  { value: '代码', label: '代码' },
  { value: '数据', label: '数据' },
]

const statusOptions: { value: FileStatus | 'all'; label: string }[] = [
  { value: 'all', label: '全部状态' },
  { value: 'indexed', label: '已索引' },
  { value: 'processing', label: '索引中' },
  { value: 'pending', label: '待处理' },
  { value: 'failed', label: '索引失败' },
]

/** 文件类型 → 水平进度条颜色（对标 prototype brand-500/info/success/warning 彩色） */
const fileTypeBarColor: Record<FileType, string> = {
  '文档': 'bg-brand-500',
  '图片': 'bg-info',
  '视频': 'bg-success',
  '音频': 'bg-warning',
  '代码': 'bg-brand-400',
  '数据': 'bg-info',
  '其他': 'bg-text-muted',
}

/** 将后端 FileRecord 映射为前端 KnowledgeFile */
function mapFileRecord(raw: FileRecord): KnowledgeFile {
  const type = fileTypeMap[raw.file_type] ?? '其他'
  const status = statusMap[raw.status] ?? 'pending'
  return {
    id: raw.id,
    name: raw.original_name,
    type,
    size: formatFileSize(raw.file_size),
    sizeBytes: raw.file_size,
    status,
    chunks: raw.chunk_count || 0,
    updatedAt: formatDate(raw.created_at),
    source: deriveSource(raw.file_path, raw.original_name),
    errorMessage: raw.error_message,
    isHighlyConfidential: raw.is_highly_confidential,
    confidentialStatus: raw.confidential_status ?? null,
  }
}

/** 字节数 → 人类可读 */
function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '—'
  if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB'
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KB'
  return bytes + ' B'
}

/** ISO 时间 → YYYY-MM-DD */
function formatDate(iso: string): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return '—'
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch {
    return '—'
  }
}

/** 从文件路径推导来源分类（用于前端筛选展示） */
function deriveSource(filePath: string, originalName: string): string {
  const lower = (filePath + '/' + originalName).toLowerCase()
  if (lower.includes('faq')) return 'FAQ'
  if (lower.includes('wiki') || lower.includes('知识库')) return '企业Wiki'
  if (lower.includes('工单') || lower.includes('ticket')) return '工单记录'
  if (lower.includes('培训') || lower.includes('train')) return '培训资料'
  if (lower.includes('产品') || lower.includes('手册') || lower.includes('product')) return '产品手册'
  if (lower.includes('开发') || lower.includes('api') || lower.includes('dev')) return '开发文档'
  if (lower.includes('调研') || lower.includes('research')) return '调研资料'
  if (lower.includes('官网') || lower.includes('website')) return '官网'
  return '其他'
}

/** 根据文件列表动态推导可选来源 */
function buildSourceOptions(files: KnowledgeFile[]): string[] {
  const set = new Set<string>()
  files.forEach((f) => set.add(f.source))
  return ['全部来源', ...Array.from(set).sort()]
}

const PAGE_SIZE = 8

/* ------------------------------------------------------------------ */
/*  格式化辅助                                                         */
/* ------------------------------------------------------------------ */

/** 千分位格式化 */
function formatCount(n: number): string {
  return n.toLocaleString('en-US')
}

/* ------------------------------------------------------------------ */
/*  统计卡片子组件                                                     */
/* ------------------------------------------------------------------ */
/* 使用全局 StatCard variant="compact" 组件，对标 prototype 知识库页 */

/* ------------------------------------------------------------------ */
/*  主页面组件                                                         */
/* ------------------------------------------------------------------ */

export default function KnowledgePage() {
  const confirmDialog = useConfirmDialog()
  const { user } = useAuth()
  const enterpriseId = useEnterpriseId()
  const navigate = useNavigate()

  // §7 知识源选择：本地路径授权优先，上传降级（双选项对话框）
  const [showSourceDialog, setShowSourceDialog] = useState(false)

  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [selectedAgentId, setSelectedAgentId] = useState<string>('')
  const [stats, setStats] = useState<StatsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 文件列表状态（接入真实 API）
  const [files, setFiles] = useState<KnowledgeFile[]>([])
  const [filesLoading, setFilesLoading] = useState(false)
  const [filesError, setFilesError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [filterType, setFilterType] = useState<FileType | 'all'>('all')
  const [filterStatus, setFilterStatus] = useState<FileStatus | 'all'>('all')
  const [filterSource, setFilterSource] = useState<string>('全部来源')
  const [currentPage, setCurrentPage] = useState(1)

  // 文件预览对话框
  const [previewing, setPreviewing] = useState<KnowledgeFile | null>(null)
  const [previewContent, setPreviewContent] = useState<string>('')
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewTruncated, setPreviewTruncated] = useState(false)

  // 删除确认对话框
  const [deleting, setDeleting] = useState<KnowledgeFile | null>(null)
  const [deleteLoading, setDeleteLoading] = useState(false)

  // 涉密文件授权管理对话框（P1-SANDBOX）
  const [confidentialFile, setConfidentialFile] = useState<KnowledgeFile | null>(null)
  const [confidentialAccessList, setConfidentialAccessList] = useState<ConfidentialAccess[]>([])
  const [confidentialLoading, setConfidentialLoading] = useState(false)
  const [confidentialTargetUserId, setConfidentialTargetUserId] = useState('')
  const [confidentialActionLoading, setConfidentialActionLoading] = useState(false)

  // P1-UPLOAD: 批量上传状态
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  // Toast 提示
  const [toast, setToast] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  // 分块与索引配置
  const [chunkSize, setChunkSize] = useState(512)
  const [chunkOverlap, setChunkOverlap] = useState(64)
  const [embeddingModel, setEmbeddingModel] = useState('text-embedding-3-small')
  const [topK, setTopK] = useState(5)
  const [similarityThreshold, setSimilarityThreshold] = useState(0.75)

  // 页面级 Tab：知识库 / 企业认知
  const [mainTab, setMainTab] = useState<'knowledge' | 'enterprise'>('knowledge')

  /* ---- 自动消失的 toast ---- */
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 3500)
    return () => clearTimeout(timer)
  }, [toast])

  /* ---- 加载 agent 列表 ---- */
  useEffect(() => {
    const loadAgents = async () => {
      try {
        const list = await getAgents()
        // 显示所有智能体（不止 ready），让用户能看到处理中的状态
        const visibleAgents = list.filter((a: Agent) => a.status === 'ready' || a.status === 'processing' || a.status === 'error')
        setAgents(visibleAgents)
        if (visibleAgents.length > 0 && !selectedAgentId) {
          setSelectedAgentId(visibleAgents[0].id)
        }
      } catch (err) {
        console.error('加载智能体列表失败:', err)
        setError('加载智能体列表失败，请检查网络或登录状态')
      } finally {
        setLoading(false)
      }
    }
    loadAgents()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ---- 加载选中 agent 的文件列表 + 知识库统计 ---- */
  const loadFilesAndStats = useCallback(async (agentId: string) => {
    setFilesLoading(true)
    setFilesError(null)
    setError(null)
    try {
      const [fileListResult, statsResult] = await Promise.allSettled([
        listFiles({ agent_id: agentId, limit: 100 }),
        getKnowledgeStats(agentId),
      ])
      if (fileListResult.status === 'fulfilled') {
        setFiles(fileListResult.value.files.map(mapFileRecord))
      } else {
        setFiles([])
        setFilesError('文件列表加载失败')
        console.error('加载文件列表失败:', fileListResult.reason)
      }
      if (statsResult.status === 'fulfilled') {
        setStats(statsResult.value)
      } else {
        setStats(null)
        console.error('加载知识库统计失败:', statsResult.reason)
      }
    } finally {
      setFilesLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedAgentId) return
    loadFilesAndStats(selectedAgentId)
  }, [selectedAgentId, loadFilesAndStats])

  /* ---- 切换 agent 时加载已保存的知识库配置 ---- */
  useEffect(() => {
    if (!selectedAgentId) return
    const agent = agents.find((a) => a.id === selectedAgentId)
    const knowledgeConfig = agent?.config?.knowledge as Record<string, unknown> | undefined
    if (knowledgeConfig) {
      if (typeof knowledgeConfig.chunkSize === 'number') setChunkSize(knowledgeConfig.chunkSize)
      if (typeof knowledgeConfig.chunkOverlap === 'number') setChunkOverlap(knowledgeConfig.chunkOverlap)
      if (typeof knowledgeConfig.embeddingModel === 'string') setEmbeddingModel(knowledgeConfig.embeddingModel)
      if (typeof knowledgeConfig.topK === 'number') setTopK(knowledgeConfig.topK)
      if (typeof knowledgeConfig.similarityThreshold === 'number') setSimilarityThreshold(knowledgeConfig.similarityThreshold)
    }
  }, [selectedAgentId, agents])

  /* ---- 过滤后的文件列表 ---- */
  const filteredFiles = useMemo(() => {
    return files.filter((f) => {
      if (searchQuery && !f.name.toLowerCase().includes(searchQuery.toLowerCase())) return false
      if (filterType !== 'all' && f.type !== filterType) return false
      if (filterStatus !== 'all' && f.status !== filterStatus) return false
      if (filterSource !== '全部来源' && f.source !== filterSource) return false
      return true
    })
  }, [files, searchQuery, filterType, filterStatus, filterSource])

  /* ---- 动态来源选项 ---- */
  const sourceOptions = useMemo(() => buildSourceOptions(files), [files])

  /* ---- 分页 ---- */
  const totalPages = Math.max(1, Math.ceil(filteredFiles.length / PAGE_SIZE))
  const currentPageSafe = Math.min(currentPage, totalPages)
  const paginatedFiles = useMemo(() => {
    const start = (currentPageSafe - 1) * PAGE_SIZE
    return filteredFiles.slice(start, start + PAGE_SIZE)
  }, [filteredFiles, currentPageSafe])

  // 当筛选条件变化时重置到第 1 页
  useEffect(() => {
    setCurrentPage(1)
  }, [searchQuery, filterType, filterStatus, filterSource])

  /* ---- 统计卡片数据（基于真实文件列表计算） ---- */
  const totalChunks = stats?.totalChunks ?? files.reduce((sum, f) => sum + f.chunks, 0)
  const totalFiles = stats?.totalFiles ?? files.length
  const confidentialCount = files.filter((f) => f.isHighlyConfidential).length
  const totalSizeBytes = files.reduce((sum, f) => sum + f.sizeBytes, 0)

  /* ---- 概览指标（MetricGrid） ---- */
  const overviewMetrics: Metric[] = useMemo(() => {
    if (files.length === 0 && !stats) return []
    return [
      {
        key: 'files',
        icon: FileText,
        value: formatCount(totalFiles),
        label: '文件总数',
        tone: 'brand',
        variant: 'compact',
      },
      {
        key: 'chunks',
        icon: Brain,
        value: formatCount(totalChunks),
        label: '总 chunks',
        tone: 'info',
        variant: 'compact',
      },
      {
        key: 'confidential',
        icon: ShieldCheck,
        value: confidentialCount,
        label: '涉密文件',
        tone: confidentialCount > 0 ? 'warning' : 'info',
        variant: 'compact',
      },
      {
        key: 'size',
        icon: HardDrive,
        value: formatFileSize(totalSizeBytes),
        label: '总大小',
        tone: 'success',
        variant: 'compact',
      },
    ]
  }, [files.length, stats, totalChunks, totalFiles, confidentialCount, totalSizeBytes])

  /* ---- 文件类型分布数据（水平进度条） ---- */
  const fileTypeDistribution = useMemo(() => {
    const groups: Record<string, number> = {}
    files.forEach((f) => {
      groups[f.type] = (groups[f.type] || 0) + 1
    })
    return Object.entries(groups)
      .map(([name, count]) => ({ name: name as FileType, count }))
      .sort((a, b) => b.count - a.count)
  }, [files])

  /* ---- 操作处理 ---- */
  // "添加知识源"：弹出双选项对话框 —— 本地路径授权优先，上传文件/文件夹为降级通道（§7）
  const handleAddSource = () => {
    if (!selectedAgentId) {
      setToast({ type: 'error', message: '请先选择一个智能体，再添加知识源' })
      return
    }
    setShowSourceDialog(true)
  }

  // 本地路径授权：跳转到协作工作台（右栏「本地连接」卡片可注册/连接本地路径）
  const handleAddLocalSource = () => {
    setShowSourceDialog(false)
    navigate('/work-execution')
  }

  // 降级通道：上传文件/文件夹（云端沙箱）
  const handleUploadSource = () => {
    setShowSourceDialog(false)
    fileInputRef.current?.click()
  }

  // P1-UPLOAD: 批量上传文件/文件夹
  const handleUploadFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files || [])
    if (!selected.length) return
    if (!selectedAgentId) {
      setToast({ type: 'error', message: '请先选择一个智能体' })
      return
    }
    setUploading(true)
    try {
      const result = await batchUploadFiles(selectedAgentId, selected)
      const failedNames = result.items
        .filter((i) => i.status === 'error')
        .map((i) => i.original_name)
        .slice(0, 3)
      let msg = `上传完成：成功 ${result.successful} 个，失败 ${result.failed} 个`
      if (failedNames.length) {
        msg += `（${failedNames.join('、')} 等失败）`
      }
      setToast({ type: result.failed > 0 ? 'error' : 'success', message: msg })
      loadFilesAndStats(selectedAgentId)
    } catch (err: any) {
      const msg = err?.response?.data?.message || err?.message || '上传失败'
      setToast({ type: 'error', message: `上传失败：${msg}` })
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const handleRefresh = () => {
    if (selectedAgentId) {
      loadFilesAndStats(selectedAgentId)
      setToast({ type: 'success', message: '已刷新文件列表' })
    }
  }

  // P1-FE: 保存知识库配置到后端
  const [savingConfig, setSavingConfig] = useState(false)
  const handleSaveConfig = async () => {
    if (!selectedAgentId) {
      setToast({ type: 'error', message: '请先选择一个智能体' })
      return
    }
    setSavingConfig(true)
    try {
      await updateAgent(selectedAgentId, {
        config: {
          knowledge: {
            chunkSize,
            chunkOverlap,
            embeddingModel,
            topK,
            similarityThreshold,
          },
        },
        changelog: '更新知识库配置',
      })
      setToast({ type: 'success', message: '配置已保存' })
    } catch (err: any) {
      const msg = err?.response?.data?.message || err?.message || '保存配置失败'
      setToast({ type: 'error', message: msg })
    } finally {
      setSavingConfig(false)
    }
  }

  const handleViewFile = async (file: KnowledgeFile) => {
    setPreviewing(file)
    setPreviewContent('')
    setPreviewTruncated(false)
    setPreviewLoading(true)
    try {
      const result = await previewFile(file.id, 2000)
      setPreviewContent(result.preview)
      setPreviewTruncated(result.truncated)
    } catch (err: any) {
      const msg = err?.response?.data?.message || err?.message || '预览失败'
      setPreviewContent(`⚠️ ${msg}\n\n可能原因：\n• 文件为二进制格式（图片/视频/音频不可预览）\n• 文件已被移动或删除\n• 文件编码不支持`)
    } finally {
      setPreviewLoading(false)
    }
  }

  const handleDeleteFile = (file: KnowledgeFile) => {
    setDeleting(file)
  }

  const handleConfirmDelete = async () => {
    if (!deleting) return
    setDeleteLoading(true)
    try {
      await deleteFile(deleting.id)
      // 从列表中移除
      setFiles((prev) => prev.filter((f) => f.id !== deleting.id))
      setToast({ type: 'success', message: `已删除文件：${deleting.name}` })
      setDeleting(null)
      // 重新加载统计
      if (selectedAgentId) {
        getKnowledgeStats(selectedAgentId).then(setStats).catch(() => {})
      }
    } catch (err: any) {
      const msg = err?.response?.data?.message || err?.message || '删除失败'
      setToast({ type: 'error', message: `删除失败：${msg}` })
    } finally {
      setDeleteLoading(false)
    }
  }

  const handleRetryFile = (file: KnowledgeFile) => {
    // 后端没有「单文件重新索引」端点；真实可用的重建入口是
    // POST /agents/{id}/incremental-update（上方「知识库运维」面板），
    // 它基于 content_hash 比对重新处理变更文件。
    // 此处如实指向该入口，并回显后端记录的失败原因。
    setToast({
      type: 'error',
      message: file.errorMessage
        ? `「${file.name}」索引失败：${file.errorMessage}。修正文件后可在「知识库运维」执行增量同步重试。`
        : `「${file.name}」索引失败。修正文件后可在「知识库运维」执行增量同步重试。`,
    })
  }

  /* ---- P1-SANDBOX: 涉密文件授权管理 ---- */
  const handleManageConfidential = async (file: KnowledgeFile) => {
    setConfidentialFile(file)
    setConfidentialAccessList([])
    setConfidentialTargetUserId('')
    setConfidentialLoading(true)
    try {
      const list = await listConfidentialAccess(file.id)
      setConfidentialAccessList(list)
    } catch (err: any) {
      const msg = err?.response?.data?.message || err?.message || '加载授权列表失败'
      setToast({ type: 'error', message: msg })
      setConfidentialFile(null)
    } finally {
      setConfidentialLoading(false)
    }
  }

  const handleGrantConfidential = async () => {
    if (!confidentialFile) return
    const trimmed = confidentialTargetUserId.trim()
    if (!trimmed) {
      setToast({ type: 'error', message: '请输入用户 ID' })
      return
    }
    setConfidentialActionLoading(true)
    try {
      await grantConfidentialAccess(confidentialFile.id, trimmed)
      setToast({ type: 'success', message: '授权已授予' })
      setConfidentialTargetUserId('')
      // 刷新授权列表
      const list = await listConfidentialAccess(confidentialFile.id)
      setConfidentialAccessList(list)
      // 同步更新文件列表中的 confidentialStatus
      setFiles((prev) =>
        prev.map((f) =>
          f.id === confidentialFile.id
            ? { ...f, confidentialStatus: 'authorized' }
            : f,
        ),
      )
    } catch (err: any) {
      const msg = err?.response?.data?.message || err?.message || '授权失败'
      setToast({ type: 'error', message: msg })
    } finally {
      setConfidentialActionLoading(false)
    }
  }

  const handleRevokeConfidential = async (userId: string) => {
    if (!confidentialFile) return
    const ok = await confirmDialog.ask({
      title: '撤销访问权限',
      description: '确定要撤销该用户对此涉密文件的访问权限吗？',
      confirmText: '撤销',
      cancelText: '取消',
      variant: 'danger',
    })
    if (!ok) return
    setConfidentialActionLoading(true)
    try {
      await revokeConfidentialAccess(confidentialFile.id, userId)
      setToast({ type: 'success', message: '授权已撤销' })
      // 刷新授权列表
      const list = await listConfidentialAccess(confidentialFile.id)
      setConfidentialAccessList(list)
      // 若列表为空，同步更新文件列表中的 confidentialStatus
      if (list.length === 0) {
        setFiles((prev) =>
          prev.map((f) =>
            f.id === confidentialFile.id
              ? { ...f, confidentialStatus: null }
              : f,
          ),
        )
      }
    } catch (err: any) {
      const msg = err?.response?.data?.message || err?.message || '撤销失败'
      setToast({ type: 'error', message: msg })
    } finally {
      setConfidentialActionLoading(false)
    }
  }

  const handleCloseConfidentialDialog = () => {
    setConfidentialFile(null)
    setConfidentialAccessList([])
    setConfidentialTargetUserId('')
  }

  /* ---- 文件详情：先调 getFile 获取完整字段，再走预览 ---- */
  const handleViewFileDetail = async (file: KnowledgeFile) => {
    // 已有 mapFileRecord 字段，可直接预览；同时尝试 getFile 获取最新详情用于展示涉密分级
    try {
      const detail = await getFile(file.id)
      // 同步更新文件列表中的涉密字段
      setFiles((prev) =>
        prev.map((f) =>
          f.id === file.id
            ? {
                ...f,
                isHighlyConfidential: detail.is_highly_confidential,
                confidentialStatus: detail.confidential_status ?? null,
              }
            : f,
        ),
      )
    } catch {
      // 忽略详情获取失败，继续走预览
    }
    handleViewFile(file)
  }

  const handlePageChange = (page: number) => {
    if (page >= 1 && page <= totalPages) {
      setCurrentPage(page)
    }
  }

  /* ---- 渲染分页按钮 ---- */
  const renderPagination = () => {
    const pages: (number | string)[] = []
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i)
    } else {
      pages.push(1)
      if (currentPageSafe > 3) pages.push('…')
      const start = Math.max(2, currentPageSafe - 1)
      const end = Math.min(totalPages - 1, currentPageSafe + 1)
      for (let i = start; i <= end; i++) pages.push(i)
      if (currentPageSafe < totalPages - 2) pages.push('…')
      pages.push(totalPages)
    }

    return (
      <div className="flex items-center gap-1">
        <button
          onClick={() => handlePageChange(currentPageSafe - 1)}
          disabled={currentPageSafe <= 1}
          className="w-8 h-8 inline-flex items-center justify-center rounded-md border border-border-default text-text-tertiary hover:bg-elevated hover:text-text-primary transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label="上一页"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        {pages.map((p, idx) =>
          typeof p === 'number' ? (
            <button
              key={`page-${p}`}
              onClick={() => handlePageChange(p)}
              className={`w-8 h-8 inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors ${
                p === currentPageSafe
                  ? 'bg-brand-500 text-white'
                  : 'border border-border-default text-text-tertiary hover:bg-elevated hover:text-text-primary'
              }`}
            >
              {p}
            </button>
          ) : (
            <span key={`ellipsis-${idx}`} className="text-text-tertiary px-1 text-sm">
              …
            </span>
          )
        )}
        <button
          onClick={() => handlePageChange(currentPageSafe + 1)}
          disabled={currentPageSafe >= totalPages}
          className="w-8 h-8 inline-flex items-center justify-center rounded-md border border-border-default text-text-tertiary hover:bg-elevated hover:text-text-primary transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label="下一页"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    )
  }

  /* ---------------------------------------------------------------- */
  /*  渲染                                                             */
  /* ---------------------------------------------------------------- */

  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <Spinner size="lg" className="text-brand-500" />
          <span className="ml-3 text-text-secondary text-sm">加载知识库…</span>
        </div>
      </Layout>
    )
  }

  // 容器入场动画 variants
  const containerVariants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
  }
  const itemVariants = {
    hidden: { opacity: 0, y: 12 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: 'easeOut' as const } },
  }

  // 无智能体时引导创建
  if (agents.length === 0) {
    return (
      <Layout>
        <div className="max-w-3xl mx-auto w-full px-4 sm:px-6 py-8">
          <PageHeader
            title="知识库"
            subtitle="管理企业知识资产，为智能体提供精准上下文检索"
          />
          <EmptyState
            icon={Brain}
            title="暂无关联智能体"
            description="知识库以智能体为单位组织文件、向量索引与授权。请先在「协作」或「设置」中创建一个智能体，再来这里上传知识文件。"
            variant="brand"
            action={{ label: '前往协作创建', to: '/chat' }}
          />
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <motion.div
        variants={containerVariants}
        initial="hidden"
        animate="visible"
                className="w-full space-y-7 pb-4"

      >
        {/* 隐藏的文件输入（由按钮触发） */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleUploadFiles}
          disabled={uploading}
        />
        <input
          ref={folderInputRef}
          type="file"
          className="hidden"
          onChange={handleUploadFiles}
          disabled={uploading}
          {...{ webkitdirectory: 'true', directory: '' }}
        />
        {/* ========== 1. 页面首屏：任务说明、上传动作与安全边界 ========== */}
        <motion.section variants={itemVariants} className="ui-card overflow-hidden rounded-2xl border border-border-default">
          <div className="grid gap-6 p-5 sm:p-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
            <div className="min-w-0">
              <p className="ui-context-kicker">企业知识中枢</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-[-0.04em] text-text-primary">把资料沉淀为可信、可检索的上下文</h2>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-text-secondary">
                上传后会进入分块与索引流程；知识资产仅在已授权的企业与智能体范围内使用，失败文件可在下方定位并重试。
              </p>
              <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-text-tertiary">
                <span className="inline-flex items-center gap-1.5"><span className="dot dot-success" />企业级隔离</span>
                <span className="inline-flex items-center gap-1.5"><span className="dot dot-info" />异步索引</span>
                <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />涉密授权可控</span>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 lg:justify-end">
              <Button variant="secondary" size="md" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                {uploading ? <Spinner size="sm" /> : <FileUp className="w-4 h-4" />}
                上传文件
              </Button>
              <Button variant="secondary" size="md" onClick={() => folderInputRef.current?.click()} disabled={uploading}>
                {uploading ? <Spinner size="sm" /> : <FolderUp className="w-4 h-4" />}
                上传文件夹
              </Button>
              <Button variant="primary" size="md" onClick={handleAddSource}>
                <Upload className="w-4 h-4" />
                添加知识源
              </Button>
            </div>
          </div>
        </motion.section>

        {/* ========== 页面级 Tab：知识库 / 企业认知 ========== */}
        <InlineTabs
          variant="underline"
          tabs={[
            { key: 'knowledge', label: '知识库', icon: FileText },
            { key: 'enterprise', label: '企业认知', icon: Brain },
          ]}
          activeKey={mainTab}
          onChange={(k) => setMainTab(k as 'knowledge' | 'enterprise')}
        />

        {mainTab === 'enterprise' ? (
          <EnterpriseCognition enterpriseId={enterpriseId} />
        ) : (
        <>
                {/* ========== 智能体选择器 ========== */}
        <div className="ui-card flex flex-wrap items-center gap-3 rounded-2xl border border-border-default p-4">
          <div className="min-w-0 mr-1">
            <p className="text-sm font-semibold text-text-primary">选择知识使用范围</p>
            <p className="mt-0.5 text-xs text-text-tertiary">为指定智能体查看、维护与授权知识资产。</p>
          </div>
          <label htmlFor="agent-select" className="sr-only">关联智能体</label>

          {agents.length > 0 ? (
            <select
              id="agent-select"
              value={selectedAgentId}
              onChange={(e) => setSelectedAgentId(e.target.value)}
                            className="min-h-10 min-w-[220px] rounded-xl border border-border-default bg-[var(--surface-raised)] px-3 text-sm text-text-primary transition-colors focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/12"

            >
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          ) : (
            <span className="text-sm text-text-tertiary">
              暂无智能体，请先创建
            </span>
          )}
          {stats && (
            <span className="text-xs text-text-tertiary flex items-center gap-1.5">
              <span className="dot dot-success" />
              当前：{stats.agentName}
            </span>
          )}
          {selectedAgentId && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleRefresh}
              disabled={filesLoading}
              aria-label="刷新文件列表"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${filesLoading ? 'animate-spin' : ''}`} />
              刷新
            </Button>
          )}
        </div>

        {/* ========== 知识库运维面板（增量同步 + 版本回滚） ========== */}
        {selectedAgentId && (
          <KnowledgeMaintenancePanel agentId={selectedAgentId} />
        )}

        {/* ========== 错误提示 ========== */}
        {(error || filesError) && (
          <div className="p-3 rounded-lg bg-error/10 border border-error/30 text-error text-sm flex items-center justify-between">
            <span className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {error || filesError}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  setError(null)
                  setFilesError(null)
                  // 重新加载当前选中 Agent 的文件列表与统计；
                  // 若未选中 Agent（agent 列表加载失败），需用户刷新页面
                  if (selectedAgentId) {
                    loadFilesAndStats(selectedAgentId)
                  }
                }}
                className="text-error/80 hover:text-error text-xs px-2 py-1 rounded border border-error/30 hover:bg-error/10 transition-colors"
                aria-label="重试"
              >
                重试
              </button>
              <button onClick={() => { setError(null); setFilesError(null) }} className="text-error/60 hover:text-error transition-colors" aria-label="关闭">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* ========== Toast 提示 ========== */}
        {toast && (
          <div
            role="status"
            aria-live="polite"
            className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg border text-sm flex items-center gap-2 ${
              toast.type === 'success'
                ? 'bg-success/10 border-success/30 text-success'
                : 'bg-error/10 border-error/30 text-error'
            }`}
          >
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span className="max-w-sm">{toast.message}</span>
            <button onClick={() => setToast(null)} className="ml-2 opacity-60 hover:opacity-100" aria-label="关闭">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* ========== 2. 统计概览卡片（MetricGrid，对标 prototype 知识库页）========== */}
        {overviewMetrics.length > 0 && (
          <motion.div variants={itemVariants}>
            <MetricGrid metrics={overviewMetrics} columns={4} />
          </motion.div>
        )}

        {/* ========== 3. 可视化区：文件类型分布 ========== */}
        <motion.div variants={itemVariants}>
          <Card>
            <CardBody>
              <div className="mb-5">
                <h3 className="text-h4 text-text-primary">知识资产可视化</h3>
                <p className="text-sm text-text-tertiary mt-1">
                  按文件类型统计知识资产分布
                </p>
              </div>

              {fileTypeDistribution.length > 0 ? (
                <div className="space-y-4">
                  {fileTypeDistribution.map((item, idx) => {
                    const maxCount = Math.max(...fileTypeDistribution.map((t) => t.count), 1)
                    const barColor = fileTypeBarColor[item.name] || 'bg-text-muted'
                    const pct = (item.count / maxCount) * 100
                    return (
                      <motion.div
                        key={item.name}
                        initial={{ opacity: 0, x: -8 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ duration: 0.3, delay: idx * 0.05 }}
                      >
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-sm text-text-secondary">{item.name}</span>
                          <span className="text-sm font-medium text-text-primary">
                            {item.count} 个
                            <span className="text-xs text-text-tertiary ml-1.5">
                              ({Math.round((item.count / files.length) * 100)}%)
                            </span>
                          </span>
                        </div>
                        <div className="h-2.5 bg-elevated rounded-full overflow-hidden">
                          <motion.div
                            className={`h-full ${barColor} rounded-full`}
                            initial={{ width: 0 }}
                            animate={{ width: `${pct}%` }}
                            transition={{ duration: 0.5, delay: 0.1 + idx * 0.05, ease: 'easeOut' }}
                          />
                        </div>
                      </motion.div>
                    )
                  })}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-12 text-text-tertiary">
                  <BarChart3 className="w-10 h-10 text-text-muted mb-3" />
                  <p className="text-sm">暂无文件数据</p>
                  <p className="text-xs mt-1">上传文件后将自动统计类型分布</p>
                </div>
              )}
            </CardBody>
          </Card>
        </motion.div>

        {/* ========== 5. 文件列表表格 ========== */}
                <motion.div variants={itemVariants} className="ui-card overflow-hidden rounded-2xl border border-border-default">
          {/* 表格标题栏 */}

          <div className="px-5 py-4 border-b border-border-subtle flex items-center justify-between gap-4 flex-wrap">
            <div className="space-y-1">
                            <h2 className="text-base font-semibold tracking-[-0.02em] text-text-primary">
                知识文件列表

              </h2>
              <p className="text-xs text-text-tertiary">
                共 {formatCount(filteredFiles.length)} 条记录
                {filteredFiles.length !== files.length && `（已从 ${formatCount(files.length)} 条筛选）`}
              </p>
            </div>
          </div>

          {/* 工具栏：搜索 + 筛选 */}
                    <div className="flex flex-wrap items-center gap-3 border-b border-border-subtle bg-[var(--surface-sunken)] px-5 py-3">
            <div className="flex-1 min-w-[200px] relative">

              <Search className="w-4 h-4 text-text-tertiary absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                                placeholder="搜索文件名…"
                className="w-full min-h-10 rounded-xl border border-border-default bg-[var(--surface-raised)] pl-9 pr-3 text-sm text-text-primary placeholder:text-text-tertiary transition-colors focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/12"

              />
            </div>
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value as FileType | 'all')}
              className="min-h-10 rounded-xl border border-border-default bg-[var(--surface-raised)] px-3 text-sm text-text-primary transition-colors focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/12"
            >
              {fileTypeOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value as FileStatus | 'all')}
              className="min-h-10 rounded-xl border border-border-default bg-[var(--surface-raised)] px-3 text-sm text-text-primary transition-colors focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/12"
            >
              {statusOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <select
              value={filterSource}
              onChange={(e) => setFilterSource(e.target.value)}
              className="min-h-10 rounded-xl border border-border-default bg-[var(--surface-raised)] px-3 text-sm text-text-primary transition-colors focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/12"
            >
              {sourceOptions.map((opt) => (
                <option key={opt} value={opt}>{opt}</option>
              ))}
            </select>
            {(searchQuery || filterType !== 'all' || filterStatus !== 'all' || filterSource !== '全部来源') && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearchQuery('')
                  setFilterType('all')
                  setFilterStatus('all')
                  setFilterSource('全部来源')
                }}
              >
                <X className="w-3.5 h-3.5" />
                清除筛选
              </Button>
            )}
          </div>

          {/* 表格主体 */}
          <div className="overflow-x-auto scrollbar-thin">
            {filesLoading ? (
              <div className="flex items-center justify-center py-16">
                <Spinner className="text-brand-500" />
                <span className="ml-2 text-text-secondary text-sm">加载文件列表…</span>
              </div>
            ) : (
              <>
              {/* S14: 桌面端表格（≥640px，横向滚动兜底） */}
              <div className="hidden sm:block overflow-x-auto scrollbar-thin">
              <table className="w-full">
                <thead>
                                    <tr className="bg-[var(--surface-sunken)]">
                    <th className="px-5 py-3 text-xs font-medium text-text-tertiary text-left">文件名</th>

                    <th className="px-5 py-3 text-xs font-medium text-text-tertiary text-left">类型</th>
                    <th className="px-5 py-3 text-xs font-medium text-text-tertiary text-left">来源</th>
                    <th className="px-5 py-3 text-xs font-medium text-text-tertiary text-right">大小</th>
                    <th className="px-5 py-3 text-xs font-medium text-text-tertiary text-left">状态</th>
                    <th className="px-5 py-3 text-xs font-medium text-text-tertiary text-right">分块</th>
                    <th className="px-5 py-3 text-xs font-medium text-text-tertiary text-left">更新时间</th>
                    <th className="px-5 py-3 text-xs font-medium text-text-tertiary text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {paginatedFiles.length > 0 ? (
                    paginatedFiles.map((file, index) => {
                      const Icon = fileTypeIcon[file.type] ?? FileText
                      const sConfig = statusConfig[file.status] ?? statusConfig.pending
                      return (
                        <motion.tr
                          key={file.id}
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          transition={{ duration: 0.2, delay: index * 0.03 }}
                                                    className="border-b border-border-subtle transition-colors hover:bg-[var(--surface-tint)]"

                        >
                          <td className="px-5 py-3 text-sm text-text-primary">
                            <span className="inline-flex items-center gap-2">
                              <Icon className="w-4 h-4 text-text-tertiary flex-shrink-0" />
                              <span className="truncate max-w-[240px]" title={file.name}>{file.name}</span>
                            </span>
                          </td>
                          <td className="px-5 py-3 text-sm text-text-secondary">{file.type}</td>
                          <td className="px-5 py-3 text-sm text-text-secondary">{file.source}</td>
                          <td className="px-5 py-3 text-sm font-mono tabular-nums text-text-secondary text-right">{file.size}</td>
                          <td className="px-5 py-3 text-sm">
                            <span className={`inline-flex items-center gap-1.5 ${sConfig.text}`}>
                              <span className={`dot ${sConfig.dot}`} />
                              {sConfig.label}
                            </span>
                            {/* 失败原因此前只存在于数据里，页面一个字不显示，
                                用户只能看到一个红点却不知道为什么失败 */}
                            {file.status === 'failed' && file.errorMessage && (
                              <p
                                className="text-xs text-error/80 mt-0.5 truncate max-w-[180px]"
                                title={file.errorMessage}
                              >
                                {file.errorMessage}
                              </p>
                            )}
                          </td>
                          <td className="px-5 py-3 text-sm font-mono tabular-nums text-right text-text-primary">{file.chunks}</td>
                          <td className="px-5 py-3 text-sm font-mono tabular-nums text-text-tertiary">{file.updatedAt}</td>
                          <td className="px-5 py-3 text-sm text-right">
                            <span className="inline-flex items-center gap-1">
                              {file.status === 'failed' ? (
                                <button
                                  onClick={() => handleRetryFile(file)}
                                  className="w-8 h-8 inline-flex items-center justify-center rounded text-warning hover:bg-elevated transition-colors"
                                  title="重试索引"
                                  aria-label="重试索引"
                                >
                                  <RotateCw className="w-4 h-4" />
                                </button>
                              ) : (
                                <button
                                  onClick={() => handleViewFileDetail(file)}
                                  className="w-8 h-8 inline-flex items-center justify-center rounded text-text-tertiary hover:text-brand-500 hover:bg-elevated transition-colors"
                                  title="预览内容"
                                  aria-label="预览内容"
                                >
                                  <Eye className="w-4 h-4" />
                                </button>
                              )}
                              {file.isHighlyConfidential && (
                                <button
                                  onClick={() => handleManageConfidential(file)}
                                  className="w-8 h-8 inline-flex items-center justify-center rounded text-text-tertiary hover:text-brand-500 hover:bg-elevated transition-colors"
                                  title="涉密文件授权管理"
                                  aria-label="涉密文件授权管理"
                                >
                                  <ShieldCheck className="w-4 h-4" />
                                </button>
                              )}
                              <button
                                onClick={() => handleDeleteFile(file)}
                                className="w-8 h-8 inline-flex items-center justify-center rounded text-text-tertiary hover:text-error hover:bg-elevated transition-colors"
                                title="删除"
                                aria-label="删除"
                              >
                                <Trash2 className="w-4 h-4" />
                              </button>
                            </span>
                          </td>
                        </motion.tr>
                      )
                    })
                  ) : (
                    <tr>
                      <td colSpan={8} className="px-5 py-8">
                        {!selectedAgentId ? (
                          /* 未选择智能体时不得误示「无文档」引导：
                             上传 CTA 会先被 toast 拦截，引导语义失真 */
                          <div className="flex flex-col items-center gap-2 text-text-tertiary py-10">
                            <Inbox className="w-10 h-10 text-text-muted" />
                            <p className="text-sm">请先在上方选择一个智能体</p>
                          </div>
                        ) : files.length === 0 ? (
                          /* §4.4.3 降低配置门槛：无文档时引导「拖入文件 → 自动关联
                             → 后台静默处理 → 完成通知」的简化流程，双 CTA 直达动作 */
                          <EmptyState
                            icon={FileUp}
                            title="该智能体还没有知识文档"
                            description="上传后自动关联到当前智能体，后台静默完成分块与向量索引，无需手动配置；完成后即可在对话中被检索引用。"
                            variant="brand"
                            action={{ label: '上传文件', onClick: () => fileInputRef.current?.click() }}
                            secondaryAction={{ label: '添加知识源（本地路径）', onClick: handleAddSource }}
                          >
                            <ol className="mb-6 grid grid-cols-2 sm:grid-cols-4 gap-2 w-full max-w-xl text-left">                              { [
                                { step: '1', label: '拖入 / 选择文件' },
                                { step: '2', label: '自动关联智能体' },
                                { step: '3', label: '后台静默索引' },
                                { step: '4', label: '索引完成，对话可引用' },
                              ].map((item) => (
                                <li
                                  key={item.step}
                                  className="flex items-center gap-2 rounded-lg border border-border-subtle bg-[var(--surface-raised)] px-2.5 py-2"
                                >
                                  <span className="flex-shrink-0 w-5 h-5 rounded-full bg-brand-50 text-brand-500 text-xs font-semibold flex items-center justify-center">
                                    {item.step}
                                  </span>
                                  <span className="text-xs text-text-secondary leading-snug">{item.label}</span>
                                </li>
                              ))}
                            </ol>
                          </EmptyState>
                        ) : (
                          <div className="flex flex-col items-center gap-3 text-text-tertiary py-8">
                            <Inbox className="w-10 h-10 text-text-muted" />
                            <div>
                              <p className="text-sm font-medium text-text-secondary">未找到匹配的文件</p>
                              <p className="text-xs text-text-tertiary mt-1">尝试调整搜索关键词或筛选条件</p>
                            </div>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                setSearchQuery('')
                                setFilterType('all')
                                setFilterStatus('all')
                                setFilterSource('全部来源')
                              }}
                            >
                              清除所有筛选
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              </div>
              {/* S14: 移动端卡片视图（<640px） */}
              <div className="sm:hidden divide-y divide-border-subtle">
                {paginatedFiles.length > 0 ? (
                  paginatedFiles.map((file) => {
                    const Icon = fileTypeIcon[file.type] ?? FileText
                    const sConfig = statusConfig[file.status] ?? statusConfig.pending
                    return (
                      <div key={file.id} className="px-5 py-4 flex flex-col gap-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="inline-flex items-center gap-2 min-w-0">
                            <Icon className="w-4 h-4 text-text-tertiary flex-shrink-0" />
                            <span className="truncate text-sm text-text-primary" title={file.name}>{file.name}</span>
                          </span>
                          <span className="inline-flex items-center gap-1 flex-shrink-0">
                            {file.status === 'failed' ? (
                              <button
                                onClick={() => handleRetryFile(file)}
                                className="w-8 h-8 inline-flex items-center justify-center rounded text-warning hover:bg-elevated transition-colors"
                                title="重试索引"
                                aria-label="重试索引"
                              >
                                <RotateCw className="w-4 h-4" />
                              </button>
                            ) : (
                              <button
                                onClick={() => handleViewFileDetail(file)}
                                className="w-8 h-8 inline-flex items-center justify-center rounded text-text-tertiary hover:text-brand-500 hover:bg-elevated transition-colors"
                                title="预览内容"
                                aria-label="预览内容"
                              >
                                <Eye className="w-4 h-4" />
                              </button>
                            )}
                            {file.isHighlyConfidential && (
                              <button
                                onClick={() => handleManageConfidential(file)}
                                className="w-8 h-8 inline-flex items-center justify-center rounded text-text-tertiary hover:text-brand-500 hover:bg-elevated transition-colors"
                                title="涉密文件授权管理"
                                aria-label="涉密文件授权管理"
                              >
                                <ShieldCheck className="w-4 h-4" />
                              </button>
                            )}
                            <button
                              onClick={() => handleDeleteFile(file)}
                              className="w-8 h-8 inline-flex items-center justify-center rounded text-text-tertiary hover:text-error hover:bg-elevated transition-colors"
                              title="删除"
                              aria-label="删除"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </span>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-text-tertiary flex-wrap">
                          <span>{file.type}</span>
                          <span aria-hidden="true">·</span>
                          <span>{file.source}</span>
                          <span aria-hidden="true">·</span>
                          <span className="font-mono">{file.size}</span>
                          <span aria-hidden="true">·</span>
                          <span className="font-mono">{file.chunks} 块</span>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                          <span className={`inline-flex items-center gap-1.5 ${sConfig.text}`}>
                            <span className={`dot ${sConfig.dot}`} />
                            {sConfig.label}
                          </span>
                          <span className="font-mono text-text-tertiary">{file.updatedAt}</span>
                        </div>
                        {file.status === 'failed' && file.errorMessage && (
                          <p className="text-xs text-error/80 leading-relaxed">
                            {file.errorMessage}
                          </p>
                        )}
                      </div>
                    )
                  })
                ) : !selectedAgentId ? (
                  <div className="px-5 py-16 text-center text-text-tertiary">
                    <Inbox className="w-10 h-10 mx-auto mb-3 text-text-muted" />
                    <p className="text-sm">请先在上方选择一个智能体</p>
                  </div>
                ) : files.length === 0 ? (
                  /* §4.4.3：移动端同样给出引导，保持与桌面端一致的动作闭环 */
                  <div className="px-5 py-10">
                    <EmptyState
                      icon={FileUp}
                      title="该智能体还没有知识文档"
                      description="上传后自动关联、后台静默索引，完成后即可在对话中被检索引用。"
                      action={{ label: '上传文件', onClick: () => fileInputRef.current?.click() }}
                      secondaryAction={{ label: '添加知识源', onClick: handleAddSource }}
                    />
                  </div>
                ) : (
                  <div className="px-5 py-16 text-center text-text-tertiary">
                    <Inbox className="w-10 h-10 mx-auto mb-3 text-text-muted" />
                    <p className="text-sm">未找到匹配的文件</p>
                    <p className="text-xs mt-1">尝试调整搜索关键词或筛选条件</p>
                  </div>
                )}
              </div>
              </>
            )}
          </div>

          {/* 分页 */}
          {filteredFiles.length > 0 && (
            <div className="flex items-center justify-between px-5 py-3 border-t border-border-default flex-wrap gap-3">
              <span className="text-sm text-text-tertiary">
                共 {formatCount(filteredFiles.length)} 条，第 {currentPageSafe}/{totalPages} 页
              </span>
              {renderPagination()}
            </div>
          )}
        </motion.div>

        {/* ========== 7. 分块与索引配置面板（SectionGroup 折叠，默认收起）========== */}
        <motion.div variants={itemVariants}>
          <SectionGroup
            title="分块与索引配置"
            icon={<Settings2 className="w-4 h-4" aria-hidden="true" />}
            description="调整向量分块、检索 Top-K 与相似度阈值（高级配置）"
            defaultOpen={false}
          >
            <div className="p-6">
              {/* 诚实标注生效范围：分块类参数只在重建索引时读取，
                  保存后不会立刻改变已有向量；Top-K 则每次检索都生效。
                  此前面板未作区分，用户改了分块大小却看不到任何变化。 */}
              <div className="mb-5 flex items-start gap-2 rounded-md bg-elevated border border-border-subtle px-3 py-2.5">
                <AlertCircle className="w-4 h-4 text-text-tertiary flex-shrink-0 mt-0.5" aria-hidden="true" />
                <p className="text-xs text-text-tertiary leading-relaxed">
                  <span className="text-text-secondary font-medium">Top-K</span> 保存后立即作用于后续每次检索；
                  <span className="text-text-secondary font-medium">分块大小 / 重叠 / 向量模型</span>
                  仅在重建索引时读取 —— 已入库的向量不会因保存而改变，需在上方
                  「知识库运维」执行增量同步后才对新增与变更文件生效。
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
                {/* 分块大小 */}
                <div className="space-y-1.5">
                  <label className="text-xs text-text-tertiary">分块大小</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      value={chunkSize}
                      onChange={(e) => setChunkSize(parseInt(e.target.value) || 0)}
                      className="flex-1 min-w-0 font-mono bg-elevated border border-border-default rounded-md px-3 py-2 text-sm text-text-primary outline-none focus:border-brand-500 transition-colors"
                    />
                    <span className="text-xs text-text-tertiary whitespace-nowrap">tokens</span>
                  </div>
                </div>
                {/* 分块重叠 */}
                <div className="space-y-1.5">
                  <label className="text-xs text-text-tertiary">分块重叠</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      value={chunkOverlap}
                      onChange={(e) => setChunkOverlap(parseInt(e.target.value) || 0)}
                      className="flex-1 min-w-0 font-mono bg-elevated border border-border-default rounded-md px-3 py-2 text-sm text-text-primary outline-none focus:border-brand-500 transition-colors"
                    />
                    <span className="text-xs text-text-tertiary whitespace-nowrap">tokens</span>
                  </div>
                </div>
                {/* 向量模型 */}
                <div className="space-y-1.5">
                  <label className="text-xs text-text-tertiary">向量模型</label>
                  <select
                    value={embeddingModel}
                    onChange={(e) => setEmbeddingModel(e.target.value)}
                    className="w-full bg-elevated border border-border-default rounded-md px-3 py-2 text-sm text-text-primary outline-none focus:border-brand-500 transition-colors cursor-pointer"
                  >
                    <option value="text-embedding-3-small">text-embedding-3-small</option>
                    <option value="text-embedding-3-large">text-embedding-3-large</option>
                    <option value="bge-large-zh">bge-large-zh</option>
                  </select>
                </div>
                {/* Top-K 检索 */}
                <div className="space-y-1.5">
                  <label className="text-xs text-text-tertiary">Top-K 检索</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      value={topK}
                      onChange={(e) => setTopK(parseInt(e.target.value) || 0)}
                      className="flex-1 min-w-0 font-mono bg-elevated border border-border-default rounded-md px-3 py-2 text-sm text-text-primary outline-none focus:border-brand-500 transition-colors"
                    />
                    <span className="text-xs text-text-tertiary whitespace-nowrap">条</span>
                  </div>
                </div>
                {/* 相似度阈值 */}
                <div className="space-y-1.5 sm:col-span-2 lg:col-span-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs text-text-tertiary">相似度阈值</label>
                    <span className="text-xs font-mono tabular-nums text-text-primary">
                      {similarityThreshold.toFixed(2)}
                    </span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={similarityThreshold}
                    onChange={(e) => setSimilarityThreshold(parseFloat(e.target.value))}
                    className="w-full accent-brand-500 cursor-pointer"
                  />
                </div>
              </div>
              <div className="mt-5 flex items-center gap-2">
                <Button variant="primary" size="md" onClick={handleSaveConfig} disabled={savingConfig}>
                  {savingConfig ? '保存中...' : '保存配置'}
                </Button>
                {/* 原「重新索引」按钮实际只调 loadFilesAndStats 刷新列表，
                    并不触发任何索引重建，属于名不副实。真正的重建入口在
                    上方「知识库运维」面板（增量同步），此处只保留如实的刷新。 */}
                <Button variant="outline" size="md" onClick={handleRefresh} disabled={filesLoading}>
                  <RefreshCw className={`w-3.5 h-3.5 ${filesLoading ? 'animate-spin' : ''}`} />
                  刷新状态
                </Button>
              </div>
            </div>
          </SectionGroup>
        </motion.div>
        </>
        )}
      </motion.div>

      {/* ========== 文件预览对话框 ========== */}
      {previewing && (
        <ModalShell
          open={Boolean(previewing)}
          onClose={() => setPreviewing(null)}
          labelledBy="preview-dialog-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
          panelClassName="max-w-3xl w-full max-h-[80vh] bg-surface border border-border-default rounded-xl shadow-2xl flex flex-col"
        >
          <>
            <div className="flex items-center justify-between px-6 py-4 border-b border-border-subtle">
              <div className="min-w-0">
                <h2 id="preview-dialog-title" className="text-h3 text-text-primary truncate">
                  {previewing.name}
                </h2>
                <p className="text-xs text-text-tertiary mt-0.5">
                  {previewing.type} · {previewing.size} · {previewing.chunks} 分块
                </p>
              </div>
              <button
                onClick={() => setPreviewing(null)}
                className="w-8 h-8 inline-flex items-center justify-center rounded text-text-tertiary hover:bg-elevated transition-colors flex-shrink-0"
                aria-label="关闭预览"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-6">
              {previewLoading ? (
                <div className="flex items-center justify-center py-12">
                  <Spinner className="text-brand-500" />
                  <span className="ml-2 text-text-secondary text-sm">加载文件内容…</span>
                </div>
              ) : (
                <>
                  <pre className="text-sm text-text-primary whitespace-pre-wrap break-words font-mono leading-relaxed">
                    {previewContent || '（空文件）'}
                  </pre>
                  {previewTruncated && (
                    <p className="text-xs text-text-tertiary mt-4 italic">
                      内容已截断，仅显示前 2000 字符。完整内容请通过下载获取。
                    </p>
                  )}
                </>
              )}
            </div>
          </>
        </ModalShell>
      )}

      {/* ========== 添加知识源对话框（§7：本地路径授权优先，上传降级）========== */}
      {showSourceDialog && (
        <ModalShell
          open={showSourceDialog}
          onClose={() => setShowSourceDialog(false)}
          labelledBy="add-source-dialog-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
          panelClassName="bg-surface border border-border-default rounded-xl shadow-xl max-w-md w-full max-h-[85vh] overflow-hidden flex flex-col"
        >
          <>
            <div className="flex items-center justify-between px-5 py-4 border-b border-border-default">
              <div className="flex items-center gap-2">
                <HardDrive className="w-5 h-5 text-brand-500" />
                <h2 id="add-source-dialog-title" className="font-serif-display text-base font-semibold text-text-primary">
                  添加知识源
                </h2>
              </div>
              <button
                onClick={() => setShowSourceDialog(false)}
                className="text-text-tertiary hover:text-text-primary transition-colors"
                aria-label="关闭"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-5 space-y-3">
              <p className="text-xs text-text-muted">
                请选择知识来源方式。推荐使用「本地路径授权」，让 AI 员工在你的本地文件夹内就地读写，无需上传。
              </p>
              {/* 本地路径授权（推荐优先） */}
              <button
                onClick={handleAddLocalSource}
                className="w-full flex items-start gap-3 text-left border border-brand-200 bg-brand-50/50 rounded-lg p-3.5 hover:bg-brand-50 hover:border-brand-300 transition-colors"
              >
                <div className="w-9 h-9 rounded-lg bg-brand-500 text-white flex items-center justify-center flex-shrink-0">
                  <HardDrive className="w-4 h-4" aria-hidden="true" />
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-medium text-text-primary">本地路径授权</span>
                    <span className="text-[10px] px-1.5 py-px rounded-full bg-brand-100 text-brand-700">推荐</span>
                  </div>
                  <p className="text-xs text-text-muted mt-0.5">
                    授权本机文件夹，AI 员工在本地直接读写文件；连接本地守护进程后即可用「本地执行」模式协作。
                  </p>
                </div>
              </button>
              {/* 上传文件/文件夹（降级通道） */}
              <button
                onClick={handleUploadSource}
                className="w-full flex items-start gap-3 text-left border border-border-default rounded-lg p-3.5 hover:bg-elevated transition-colors"
              >
                <div className="w-9 h-9 rounded-lg bg-elevated border border-border-default text-text-secondary flex items-center justify-center flex-shrink-0">
                  <Upload className="w-4 h-4" aria-hidden="true" />
                </div>
                <div className="min-w-0">
                  <span className="text-sm font-medium text-text-primary">上传文件 / 文件夹</span>
                  <p className="text-xs text-text-muted mt-0.5">
                    将文件上传到云端沙箱作为知识源（未连接本地 Runner 时的备用方式）。
                  </p>
                </div>
              </button>
            </div>
          </>
        </ModalShell>
      )}

      {/* ========== 删除确认对话框 ========== */}
      <Dialog
        open={!!deleting}
        title="确认删除文件"
        description={`即将删除文件「${deleting?.name ?? ''}」，该操作会同时清理向量索引，且不可恢复。`}
        confirmText={deleteLoading ? '删除中…' : '确认删除'}
        variant="danger"
        onConfirm={handleConfirmDelete}
        onCancel={() => !deleteLoading && setDeleting(null)}
      />

      {/* ========== 涉密文件授权管理对话框（P1-SANDBOX） ========== */}
      {confidentialFile && (
        <ModalShell
          open={Boolean(confidentialFile)}
          onClose={handleCloseConfidentialDialog}
          labelledBy="confidential-dialog-title"
          overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
          panelClassName="bg-surface border border-border-default rounded-xl shadow-xl max-w-lg w-full max-h-[85vh] overflow-hidden flex flex-col"
        >
          <>
            <div className="flex items-center justify-between px-5 py-4 border-b border-border-default">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-5 h-5 text-brand-500" />
                <h2 id="confidential-dialog-title" className="font-serif-display text-base font-semibold text-text-primary">
                  涉密文件授权管理
                </h2>
              </div>
              <button
                onClick={handleCloseConfidentialDialog}
                className="text-text-tertiary hover:text-text-primary transition-colors"
                aria-label="关闭"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="px-5 py-4 overflow-y-auto flex-1">
              <div className="bg-brand-50 rounded-md p-3 mb-4 flex items-start gap-2">
                <ShieldCheck className="w-4 h-4 text-brand-500 flex-shrink-0 mt-0.5" />
                <div className="text-xs text-brand-500 space-y-1">
                  <p className="font-medium">文件：{confidentialFile.name}</p>
                  <p>该文件被标记为"极度私密"，仅被显式授权的用户可访问。</p>
                  <p className="opacity-75">授权后，目标用户可通过预览/检索访问该文件内容。</p>
                </div>
              </div>

              {/* 授予新授权 */}
              <div className="mb-5">
                <label htmlFor="target-user-id" className="block text-sm font-medium text-text-primary mb-2">
                  授予新用户访问权限
                </label>
                <div className="flex gap-2">
                  <input
                    id="target-user-id"
                    type="text"
                    value={confidentialTargetUserId}
                    onChange={(e) => setConfidentialTargetUserId(e.target.value)}
                    placeholder="请输入用户 ID（uuid）"
                    className="flex-1 bg-surface-2 border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder-text-tertiary font-mono"
                    disabled={confidentialActionLoading}
                  />
                  <Button
                    onClick={handleGrantConfidential}
                    disabled={confidentialActionLoading || !confidentialTargetUserId.trim()}
                  >
                    {confidentialActionLoading ? '处理中…' : '授权'}
                  </Button>
                </div>
                <p className="text-xs text-text-tertiary mt-1.5">
                  提示：用户 ID 可在「设置 → 成员管理」中查看。
                </p>
              </div>

              {/* 已授权列表 */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-medium text-text-primary">已授权用户</h3>
                  <span className="text-xs text-text-tertiary">
                    {confidentialLoading ? '加载中…' : `${confidentialAccessList.length} 人`}
                  </span>
                </div>
                {confidentialLoading ? (
                  <div className="py-8 text-center text-sm text-text-tertiary bg-surface-2 rounded-md">
                    加载中…
                  </div>
                ) : confidentialAccessList.length === 0 ? (
                  <div className="py-8 text-center text-sm text-text-tertiary bg-surface-2 rounded-md">
                    暂无授权记录
                  </div>
                ) : (
                  <ul className="divide-y divide-border-subtle border border-border-subtle rounded-md overflow-hidden">
                    {confidentialAccessList.map((acc) => (
                      <li key={acc.id} className="flex items-center justify-between px-3 py-2.5 bg-surface-2">
                        <div className="min-w-0 flex-1">
                          <div className="text-sm text-text-primary truncate">
                            {acc.user_id === user?.id ? (
                              '我'
                            ) : (
                              <IdChip id={acc.user_id} maxLength={14} tone="info" label={formatUserLabel(acc.user_id)} />
                            )}
                          </div>
                          <div className="text-xs text-text-tertiary mt-0.5">
                            由 {acc.granted_by === user?.id ? '我' : <IdChip id={acc.granted_by} maxLength={14} tone="info" label={formatUserLabel(acc.granted_by)} />} 授权
                            {acc.granted_at ? ` · ${formatDate(acc.granted_at)}` : ''}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleRevokeConfidential(acc.user_id)}
                          disabled={confidentialActionLoading}
                          className="text-error/70 hover:text-error text-xs px-2 py-1 rounded border border-error/30 hover:bg-error/5 disabled:opacity-50 transition-colors flex-shrink-0 ml-3"
                          title="撤销授权"
                        >
                          撤销
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>

            <div className="px-5 py-3 border-t border-border-default bg-surface-2 flex justify-end">
              <Button variant="outline" onClick={handleCloseConfidentialDialog}>
                关闭
              </Button>
            </div>
          </>
        </ModalShell>
      )}
      {confirmDialog.dialog}
    </Layout>
  )
}
