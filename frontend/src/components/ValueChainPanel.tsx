/**
 * ValueChainPanel — V3.1 §6.2 价值链实时面板
 *
 * 展示 AutoTeams 核心价值链：文件处理 → AI 员工 → 运行时
 * 让用户一眼看到"企业 AI 正在运转"的状态。
 */
import { FileText, Users, Zap, ArrowRight } from 'lucide-react'

interface ValueChainPanelProps {
  /** 文件总数 */
  fileCount: number
  /** 文件处理状态 */
  fileStatus: 'idle' | 'processing' | 'done'
  /** AI 员工就绪数 */
  agentReady: number
  /** AI 员工总数 */
  agentTotal: number
  /** 运行时状态 */
  runtimeStatus: 'offline' | 'compiling' | 'online'
  /** 运行时版本 */
  runtimeVersion?: string
}

const STATUS_CONFIG = {
  done: { dot: 'bg-success', label: '完成', text: 'text-success' },
  processing: { dot: 'bg-warning animate-pulse', label: '处理中', text: 'text-warning' },
  idle: { dot: 'bg-text-muted', label: '待处理', text: 'text-text-tertiary' },
  online: { dot: 'bg-success animate-pulse', label: '在线', text: 'text-success' },
  compiling: { dot: 'bg-warning animate-pulse', label: '编译中', text: 'text-warning' },
  offline: { dot: 'bg-text-muted', label: '离线', text: 'text-text-tertiary' },
} as const

function ChainNode({
  icon,
  title,
  value,
  status,
}: {
  icon: React.ReactNode
  title: string
  value: string
  status: { dot: string; label: string; text: string }
}) {
  return (
    <div className="flex-1 flex items-center gap-3 px-4 py-3 bg-surface rounded-md border border-border-default">
      <span className="flex-shrink-0 w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center text-brand-500">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-caption text-text-tertiary">{title}</div>
        <div className="text-body font-medium text-text-primary truncate">{value}</div>
      </div>
      <div className="flex-shrink-0 flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full ${status.dot}`} />
        <span className={`text-caption ${status.text}`}>{status.label}</span>
      </div>
    </div>
  )
}

export function ValueChainPanel({
  fileCount,
  fileStatus,
  agentReady,
  agentTotal,
  runtimeStatus,
  runtimeVersion,
}: ValueChainPanelProps) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-stretch gap-2">
      <ChainNode
        icon={<FileText className="w-5 h-5" aria-hidden="true" />}
        title="文件处理"
        value={`${fileCount} 个文件`}
        status={STATUS_CONFIG[fileStatus]}
      />
      <div className="hidden sm:flex items-center text-text-muted">
        <ArrowRight className="w-4 h-4" aria-hidden="true" />
      </div>
      <ChainNode
        icon={<Users className="w-5 h-5" aria-hidden="true" />}
        title="AI 员工"
        value={`${agentReady}/${agentTotal} 就绪`}
        status={agentReady > 0 ? STATUS_CONFIG.online : STATUS_CONFIG.idle}
      />
      <div className="hidden sm:flex items-center text-text-muted">
        <ArrowRight className="w-4 h-4" aria-hidden="true" />
      </div>
      <ChainNode
        icon={<Zap className="w-5 h-5" aria-hidden="true" />}
        title="运行时"
        // #19 去重前缀：后端版本号可能已带 v，避免渲染成 vv1.1.0
        value={
          runtimeVersion
            ? runtimeVersion.startsWith('v') || runtimeVersion.startsWith('V')
              ? runtimeVersion
              : `v${runtimeVersion}`
            : '未编译'
        }
        status={STATUS_CONFIG[runtimeStatus]}
      />
    </div>
  )
}
