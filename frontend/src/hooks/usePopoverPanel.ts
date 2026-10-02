import { RefObject, useEffect, useRef } from 'react'

const FOCUSABLE_SELECTORS = [
  'button:not([disabled])', 'a[href]', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

/**
 * 浮层关闭的原因。焦点该还给谁，取决于关闭方式：
 * - `escape` / `sibling`：焦点必须回到触发按钮，否则键盘用户掉到 <body>；
 * - `outside` / `focus-out`：焦点已经由用户的新目标接管，hook 不得再抢。
 */
export type PopoverCloseReason = 'escape' | 'outside' | 'focus-out' | 'sibling'

interface PopoverPanelOptions {
  open: boolean
  panelRef: RefObject<HTMLElement>
  triggerRef: RefObject<HTMLElement>
  onClose: (reason: PopoverCloseReason) => void
}

// 同一时刻只允许一个浮层打开：模块级登记当前持有者，打开新浮层即让位。
let holder: ((reason: PopoverCloseReason) => void) | null = null

function focusableIn(panel: HTMLElement): HTMLElement[] {
  return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTORS))
    .filter((element) => {
      if (element.tabIndex < 0 || element.matches(':disabled') || element.closest('[inert], [hidden]')) return false
      for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent)
        if (style.display === 'none' || style.visibility === 'hidden') return false
        if (parent === panel) break
      }
      return true
    })
}

/**
 * 顶栏下拉浮层（通知 / 历史 / 用户）的共享行为。
 *
 * 这些面板既不是 menu（没有菜单式方向键模型），也不是 modal（不该把背景 inert、
 * 锁滚动、循环 Tab）。因此这里只做语义 popover 该做的事：
 * 打开后焦点进入面板首个有效控件、Escape 关闭并还焦、Tab 或指针移出即关闭、
 * 同一时刻只有一个浮层打开。
 */
export function usePopoverPanel({ open, panelRef, triggerRef, onClose }: PopoverPanelOptions): void {
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const triggerHolder = useRef(triggerRef)
  triggerHolder.current = triggerRef

  // 关闭回调必须跨 rerender 保持同一引用：模块级 holder 与下面的 effect 会一直持有它。
  // 若每次 render 都换成新函数，holder 的身份比对会错位——实例卸载后仍被登记为持有者，
  // 下一个浮层打开时就会去通知一个已销毁的实例，并尝试还焦到已脱离文档的按钮。
  const closeRef = useRef<((reason: PopoverCloseReason) => void) | null>(null)
  if (!closeRef.current) {
    closeRef.current = (reason: PopoverCloseReason) => {
      if (holder === closeRef.current) holder = null
      onCloseRef.current(reason)
      const trigger = triggerHolder.current.current
      if ((reason === 'escape' || reason === 'sibling') && trigger?.isConnected) trigger.focus()
    }
  }
  const close = closeRef.current

  // 互斥：新浮层打开时让位给旧的；旧浮层关闭后把焦点交还自己的触发按钮。
  useEffect(() => {
    if (!open) return
    const previous = holder
    holder = close
    if (previous && previous !== close) previous('sibling')
  }, [open, close])

  useEffect(() => {
    if (!open) return
    const panel = panelRef.current
    const trigger = triggerRef.current
    if (!panel || !trigger) return

    const raf = requestAnimationFrame(() => {
      const first = focusableIn(panel)[0]
      if (first) first.focus()
      else panel.focus()
    })
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229) return
      event.preventDefault()
      event.stopPropagation()
      close('escape')
    }
    const handlePointerDown = (event: Event) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (panel.contains(target) || trigger.contains(target)) return
      close('outside')
    }
    const handleFocusOut = (event: FocusEvent) => {
      const next = event.relatedTarget
      if (next instanceof Node && (panel.contains(next) || trigger.contains(next))) return
      close('focus-out')
    }
    document.addEventListener('keydown', handleKeyDown)
    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('focusout', handleFocusOut)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', handleKeyDown)
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('focusout', handleFocusOut)
      if (holder === close) holder = null
    }
  }, [open, panelRef, triggerRef, close])
}
