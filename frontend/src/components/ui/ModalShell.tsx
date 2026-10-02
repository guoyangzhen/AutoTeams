import { type ReactNode, useRef } from 'react'
import { useFocusTrap } from '@/hooks/useFocusTrap'

interface ModalShellProps {
  open: boolean
  onClose: () => void
  /** 关联标题元素的 id，用于 aria-labelledby */
  labelledBy: string
  /** 遮罩层附加类名（用于调整背景明暗、内边距等） */
  overlayClassName?: string
  /** 面板容器类名 */
  panelClassName?: string
  /** 点击遮罩是否关闭（提交中等场景可置 false） */
  closeOnOverlayClick?: boolean
  /** 是否响应 Escape 关闭 */
  closeOnEscape?: boolean
  children: ReactNode
}

/**
 * ModalShell — 手写模态的可访问性外壳。
 *
 * 页面内若干模态此前直接手写遮罩 + 面板，缺少焦点陷阱与 Escape 关闭，
 * 键盘用户 Tab 会穿透到背景内容且无法关闭。本组件统一补齐这些行为，
 * 同时保留调用方原有的布局类名，避免大改页面结构。
 *
 * 已有完整交互语义的场景请优先使用 Dialog / Drawer；
 * 本组件用于结构特殊、不便直接替换为 Dialog 的既有模态。
 */
export function ModalShell({
  open,
  onClose,
  labelledBy,
  overlayClassName = '',
  panelClassName = '',
  closeOnOverlayClick = true,
  closeOnEscape = true,
  children,
}: ModalShellProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  useFocusTrap({ active: open, containerRef: panelRef, onClose, closeOnEscape })

  if (!open) return null

  return (
    <div
      className={overlayClassName}
      onClick={closeOnOverlayClick ? onClose : undefined}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
    >
      <div ref={panelRef} className={panelClassName} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}

export default ModalShell
