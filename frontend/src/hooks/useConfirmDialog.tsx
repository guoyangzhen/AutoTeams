import { useState, useCallback, type ReactNode } from 'react'
import { Dialog } from '@/components/ui/Dialog'

/**
 * 用 Dialog 组件替代 window.confirm 的轻量 hook。
 *
 * 用法：
 * 1. const confirm = useConfirmDialog()
 * 2. 在 JSX 中渲染 {confirm.dialog}
 * 3. 触发确认：
 *    const ok = await confirm.ask({
 *      title: '删除协作',
 *      description: '此操作不可恢复',
 *      confirmText: '删除',
 *      variant: 'danger',
 *    })
 *    if (!ok) return
 *
 * 多次调用 ask() 时，后一次会覆盖前一次（前一次 promise 会被 reject）。
 * 在同一页面同时需要多种确认时，可使用多个 useConfirmDialog 实例。
 */
export interface ConfirmOptions {
  title: string
  description?: string
  confirmText?: string
  cancelText?: string
  variant?: 'default' | 'danger'
  /** 自定义内容（如风险提示列表） */
  children?: ReactNode
}

export function useConfirmDialog() {
  const [open, setOpen] = useState(false)
  const [options, setOptions] = useState<ConfirmOptions | null>(null)
  const [resolver, setResolver] = useState<((v: boolean) => void) | null>(null)

  const ask = useCallback((opts: ConfirmOptions): Promise<boolean> => {
    // 若上一次 promise 未结束，先 resolve(false) 避免泄漏
    if (resolver) resolver(false)
    setOptions(opts)
    setOpen(true)
    return new Promise<boolean>((resolve) => {
      setResolver(() => resolve)
    })
  }, [resolver])

  const handleConfirm = useCallback(() => {
    setOpen(false)
    if (resolver) {
      resolver(true)
      setResolver(null)
    }
  }, [resolver])

  const handleCancel = useCallback(() => {
    setOpen(false)
    if (resolver) {
      resolver(false)
      setResolver(null)
    }
  }, [resolver])

  const dialog = (
    <Dialog
      open={open}
      title={options?.title ?? ''}
      description={options?.description}
      confirmText={options?.confirmText}
      cancelText={options?.cancelText}
      variant={options?.variant}
      onConfirm={handleConfirm}
      onCancel={handleCancel}
    >
      {options?.children}
    </Dialog>
  )

  return { ask, dialog }
}
