/**
 * ApprovalPanel — 通用审批面板组件。
 *
 * 可复用于两个场景：
 * 1. AgentCanvasPage 的 HITL 构建审批（resumeBuild，对应 spec.md §10.7 WT3）
 * 2. CollaborationFlow 的 7 步演示案例审批节点（collaboration approve/reject，对应 WT4）
 *
 * 功能：
 * - 展示审批请求详情（标题/描述/金额/申请人）
 * - 批准按钮 + 拒绝按钮（拒绝时展开原因输入）
 * - 处理中 loading 状态
 * - 已处理状态展示
 * - 响应式：移动端纵向、桌面端横向
 *
 * 满足约束 #7：AgentCanvasPage 在构建暂停于审批节点时必须显示批准/拒绝按钮。
 */
import { useState } from 'react'
import { CheckCircle2, XCircle, ShieldCheck, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'

interface ApprovalPanelProps {
  /** 审批标题 */
  title: string
  /** 审批描述 */
  description?: string
  /** 金额（如适用） */
  amount?: number
  /** 申请人名称 */
  requesterName?: string
  /** 审批类型标签 */
  typeLabel?: string
  /** 是否处理中 */
  processing?: boolean
  /** 是否已处理 */
  handled?: boolean
  /** 已处理的结果（approved/rejected） */
  handledResult?: 'approved' | 'rejected'
  /** 批准回调 */
  onApprove?: () => void
  /** 拒绝回调（参数为拒绝原因） */
  onReject?: (reason: string) => void
  /** 是否紧凑模式（用于工具栏内嵌） */
  compact?: boolean
}

export function ApprovalPanel({
  title,
  description,
  amount,
  requesterName,
  typeLabel = '审批',
  processing = false,
  handled = false,
  handledResult,
  onApprove,
  onReject,
  compact = false,
}: ApprovalPanelProps) {
  const [showRejectInput, setShowRejectInput] = useState(false)
  const [rejectReason, setRejectReason] = useState('')

  const handleReject = () => {
    if (onReject) {
      onReject(rejectReason || '用户拒绝')
      setRejectReason('')
      setShowRejectInput(false)
    }
  }

  const handleCancel = () => {
    setShowRejectInput(false)
    setRejectReason('')
  }

  // 已处理状态
  if (handled) {
    return (
      <div
        className={`inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm ${
          handledResult === 'approved'
            ? 'bg-success/10 text-success'
            : 'bg-error/10 text-error'
        }`}
      >
        {handledResult === 'approved' ? (
          <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
        ) : (
          <XCircle className="w-4 h-4" aria-hidden="true" />
        )}
        {handledResult === 'approved' ? '已批准' : '已拒绝'}
      </div>
    )
  }

  // 紧凑模式（用于工具栏）
  if (compact) {
    return (
      <div className="flex items-center gap-1.5">
        <Button
          variant="primary"
          size="sm"
          onClick={onApprove}
          disabled={processing || !onApprove}
          className="!py-1 !px-2.5"
        >
          {processing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
          批准
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onReject?.('用户拒绝')}
          disabled={processing || !onReject}
          className="!py-1 !px-2.5 !text-error !border-error/30 hover:!bg-error/10"
        >
          <XCircle className="w-3.5 h-3.5" />
          拒绝
        </Button>
      </div>
    )
  }

  // 完整模式
  return (
    <div className="rounded-lg border border-brand-200 bg-brand-50/30 p-4 space-y-3">
      {/* 头部 */}
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-full bg-brand-50 flex items-center justify-center flex-shrink-0">
          <ShieldCheck className="w-5 h-5 text-brand-500" aria-hidden="true" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs px-1.5 py-0.5 rounded bg-brand-100 text-brand-500">
              {typeLabel}
            </span>
            {requesterName && (
              <span className="text-xs text-text-tertiary">申请人：{requesterName}</span>
            )}
          </div>
          <h4 className="text-sm font-medium text-text-primary mt-1">{title}</h4>
          {description && (
            <p className="text-sm text-text-secondary mt-1 leading-relaxed">{description}</p>
          )}
          {amount !== undefined && (
            <div className="mt-2 inline-flex items-center gap-1 text-lg font-bold text-brand-500">
              ¥{amount.toLocaleString('zh-CN')}
            </div>
          )}
        </div>
      </div>

      {/* 操作区：#4 缩小按钮尺寸、提高对比度，与整体设计统一（完整模式） */}
      {!showRejectInput ? (
        <div className="flex items-center gap-2 pt-1 border-t border-brand-100">
          <Button
            variant="primary"
            size="sm"
            onClick={onApprove}
            disabled={processing || !onApprove}
            className="!h-7 !px-3 !py-0 !text-xs !font-semibold shadow-sm"
            title="批准该审批事项"
          >
            {processing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
            {processing ? '处理中...' : '批准'}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowRejectInput(true)}
            disabled={processing || !onReject}
            className="!h-7 !px-3 !py-0 !text-xs !font-semibold !text-error !border-error/40 hover:!bg-error/10"
            title="拒绝该审批事项"
          >
            <XCircle className="w-3.5 h-3.5" />
            拒绝
          </Button>
        </div>
      ) : (
        <div className="space-y-2 pt-1 border-t border-brand-100">
          <textarea
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            placeholder="请输入拒绝原因（可选）..."
            rows={2}
            className="w-full rounded border border-border-default bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-disabled focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
          />
          <div className="flex items-center gap-2">
            <Button variant="danger" size="sm" className="!h-7 !px-3 !py-0 !text-xs !font-semibold" onClick={handleReject} disabled={processing}>
              确认拒绝
            </Button>
            <Button variant="ghost" size="sm" className="!h-7 !px-3 !py-0 !text-xs" onClick={handleCancel} disabled={processing}>
              取消
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

export default ApprovalPanel
