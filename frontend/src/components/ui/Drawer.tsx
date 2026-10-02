/**
 * Drawer — 右侧滑出抽屉组件。
 *
 * 用于详情内容较长（如员工档案、文件预览、版本 diff）的场景，
 * 替代 Dialog 避免内容过长导致滚动疲劳。
 *
 * 实现：
 * - Framer Motion 右滑入（x: 100% → 0）+ 背景遮罩淡入
 * - ESC 关闭 + 点击遮罩关闭 + body 滚动锁定
 * - 焦点陷阱：打开时聚焦抽屉内首个元素，Tab 循环不逃逸到背景，关闭后还焦
 * - 内部支持嵌套 Tab/折叠（由 children 自行组合）
 *
 * 对标苹果级抽屉交互：缓动曲线 easeInOut，过渡 280ms。
 */
import { type ReactNode, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import { useFocusTrap } from '@/hooks/useFocusTrap'

interface DrawerProps {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  /** 右侧操作区（如「应用」按钮） */
  actions?: ReactNode
  children: ReactNode
  /** 抽屉宽度（默认 480px） */
  width?: number
  className?: string
}

export function Drawer({
  open,
  onClose,
  title,
  description,
  actions,
  children,
  width = 480,
  className = '',
}: DrawerProps) {
  const drawerRef = useRef<HTMLDivElement>(null)

  // 焦点陷阱 + Escape 关闭 + 关闭后还焦（与 Dialog 共用同一实现）
  useFocusTrap({ active: open, containerRef: drawerRef, onClose })

  // body 滚动锁定
  useEffect(() => {
    if (!open) return

    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    return () => {
      document.body.style.overflow = prevOverflow
    }
  }, [open])

  return (
    <AnimatePresence>
      {open && (
        <div
          className="fixed inset-0 z-50 flex justify-end"
          role="dialog"
          aria-modal="true"
          aria-labelledby="drawer-title"
        >
          {/* 背景遮罩 */}
          <motion.div
            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
          />

          {/* 抽屉主体 */}
          <motion.div
            ref={drawerRef}
            className="relative h-full bg-surface border-l border-border-default shadow-2xl flex flex-col"
            style={{ width: `min(${width}px, 100vw)` }}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'tween', ease: 'easeInOut', duration: 0.28 }}
          >
            {/* 头部：标题 + 描述 + 关闭按钮 */}
            <div className="flex items-start justify-between gap-3 px-6 pt-6 pb-4 border-b border-border-subtle flex-shrink-0">
              <div className="min-w-0 flex-1">
                <h2
                  id="drawer-title"
                  className="font-serif-display text-xl font-semibold text-text-primary"
                >
                  {title}
                </h2>
                {description && (
                  <p className="text-sm text-text-tertiary mt-1">{description}</p>
                )}
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭"
                className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-md text-text-tertiary hover:text-text-primary hover:bg-elevated transition-colors"
              >
                <X className="w-5 h-5" aria-hidden="true" />
              </button>
            </div>

            {/* 内容区：可滚动 */}
            <div className={`flex-1 overflow-y-auto px-6 py-5 ${className}`}>
              {children}
            </div>

            {/* 底部操作区（可选） */}
            {actions && (
              <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-border-subtle bg-elevated/30 flex-shrink-0">
                {actions}
              </div>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}

export default Drawer
