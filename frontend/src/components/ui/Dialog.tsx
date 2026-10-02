import { ReactNode, useRef } from 'react'
import { X } from 'lucide-react'
import { Button } from './Button'
import { useFocusTrap } from '@/hooks/useFocusTrap'

interface DialogProps {
  open: boolean
  title: string
  description?: string
  confirmText?: string
  cancelText?: string
  variant?: 'default' | 'danger'
  /** 自定义最大宽度（详情弹窗用 lg，默认 md） */
  size?: 'md' | 'lg' | 'xl'
  /** 详情模式：仅展示内容 + 右上角关闭按钮，不显示底部确认/取消按钮 */
  detailMode?: boolean
  onConfirm: () => void
  onCancel: () => void
  children?: ReactNode
}

const SIZE_CLASS: Record<NonNullable<DialogProps['size']>, string> = {
  md: 'max-w-md',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
}

export function Dialog({
  open,
  title,
  description,
  confirmText = '确认',
  cancelText = '取消',
  variant = 'default',
  size = 'md',
  detailMode = false,
  onConfirm,
  onCancel,
  children,
}: DialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const descriptionId = description ? 'dialog-description' : undefined

  // P1-04-D: 焦点陷阱 + Escape 关闭 + 关闭后还焦（实现已抽取为共用 hook）
  useFocusTrap({ active: open, containerRef: dialogRef, onClose: onCancel })

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-labelledby="dialog-title"
      aria-describedby={descriptionId}
    >
      <div
        ref={dialogRef}
        className={`${SIZE_CLASS[size]} w-full max-h-[90vh] flex flex-col bg-surface border border-border-default rounded-xl shadow-2xl overflow-hidden`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部：标题 + 右上角关闭按钮（统一图标框尺寸 32x32） */}
        <div className="flex items-start justify-between gap-3 px-6 pt-6 pb-2 flex-shrink-0">
          <div className="min-w-0 flex-1">
            <h2 id="dialog-title" className="text-h3 text-text-primary">
              {title}
            </h2>
            {description && (
              <p id={descriptionId} className="text-sm text-text-secondary mt-1">
                {description}
              </p>
            )}
          </div>
          <button
            onClick={onCancel}
            aria-label="关闭"
            className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-md text-text-tertiary hover:text-text-primary hover:bg-elevated transition-colors"
          >
            <X className="w-5 h-5" aria-hidden="true" />
          </button>
        </div>

        {/* 内容区：可滚动 */}
        <div className="px-6 py-4 overflow-y-auto flex-1">
          {children}
        </div>

        {/* 底部按钮区（详情模式隐藏） */}
        {!detailMode && (
          <div className="flex gap-3 justify-end px-6 py-4 bg-elevated/50 rounded-b-xl flex-shrink-0">
            <Button variant="ghost" size="sm" onClick={onCancel}>
              {cancelText}
            </Button>
            <Button variant={variant === 'danger' ? 'danger' : 'primary'} size="sm" onClick={onConfirm}>
              {confirmText}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

export default Dialog
