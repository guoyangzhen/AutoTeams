import { RefObject, useEffect, useRef } from 'react'

const FOCUSABLE_SELECTORS = [
  'button:not([disabled])', 'a[href]', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

interface FocusTrapOptions {
  active: boolean
  containerRef: RefObject<HTMLElement>
  onClose: () => void
  closeOnEscape?: boolean
}

// 嵌套模态共享锁和栈；关闭内层不能解除外层的背景隔离或滚动锁。
const traps: symbol[] = []
const inertLocks = new Map<HTMLElement, { count: number; original: string | null }>()
let scrollLocks = 0
let originalOverflow = ''

function isolateBackground(container: HTMLElement): () => void {
  const siblings = new Set<HTMLElement>()
  let branch: HTMLElement | null = container.closest<HTMLElement>('[role="dialog"], [role="alertdialog"]') ?? container
  while (branch && branch !== document.body) {
    const parent: HTMLElement | null = branch.parentElement
    if (!parent) break
    for (const sibling of Array.from(parent.children)) {
      if (sibling !== branch && sibling instanceof HTMLElement) siblings.add(sibling)
    }
    branch = parent
  }
  for (const sibling of siblings) {
    const lock = inertLocks.get(sibling)
    if (lock) lock.count++
    else {
      inertLocks.set(sibling, { count: 1, original: sibling.getAttribute('inert') })
      sibling.setAttribute('inert', '')
    }
  }
  return () => {
    for (const sibling of siblings) {
      const lock = inertLocks.get(sibling)
      if (!lock || --lock.count > 0) continue
      if (lock.original === null) sibling.removeAttribute('inert')
      else sibling.setAttribute('inert', lock.original)
      inertLocks.delete(sibling)
    }
  }
}

/** 共享模态焦点、背景隔离与滚动锁；只有最上层模态响应 Tab/Escape。 */
export function useFocusTrap({ active, containerRef, onClose, closeOnEscape = true }: FocusTrapOptions): void {
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const escapeRef = useRef(closeOnEscape)
  escapeRef.current = closeOnEscape

  useEffect(() => {
    const container = containerRef.current
    if (!active || !container) return
    const token = Symbol('modal')
    let addedTabIndex = false
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    traps.push(token)
    const isTop = () => traps[traps.length - 1] === token
    const restoreBackground = isolateBackground(container)
    if (scrollLocks++ === 0) {
      originalOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
    }
    const focusable = (): HTMLElement[] => Array.from(
      container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTORS),
    ).filter((element) => {
      if (element.tabIndex < 0 || element.matches(':disabled') || element.closest('[inert], [hidden]')) return false
      for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent)
        if (style.display === 'none' || style.visibility === 'hidden') return false
        if (parent === container) break
      }
      return true
    })
    const focusFirst = () => {
      const first = focusable()[0]
      if (first) first.focus()
      else {
        if (!container.hasAttribute('tabindex')) {
          container.setAttribute('tabindex', '-1')
          addedTabIndex = true
        }
        container.focus()
      }
    }
    const raf = requestAnimationFrame(() => { if (isTop()) focusFirst() })
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!isTop() || event.isComposing || event.keyCode === 229) return
      if (event.key === 'Escape' && escapeRef.current) {
        event.preventDefault()
        event.stopPropagation()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const elements = focusable()
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (!first || !last) {
        event.preventDefault()
        focusFirst()
      } else if (!container.contains(document.activeElement)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus()
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    const handleFocus = (event: FocusEvent) => {
      if (isTop() && event.target instanceof Node && !container.contains(event.target)) focusFirst()
    }
    document.addEventListener('keydown', handleKeyDown)
    document.addEventListener('focusin', handleFocus)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', handleKeyDown)
      document.removeEventListener('focusin', handleFocus)
      const wasTop = isTop()
      traps.splice(traps.indexOf(token), 1)
      restoreBackground()
      if (addedTabIndex) container.removeAttribute('tabindex')
      if (--scrollLocks === 0) document.body.style.overflow = originalOverflow
      if (wasTop && previousFocus?.isConnected && !previousFocus.closest('[inert]')) previousFocus.focus()
    }
  }, [active, containerRef])
}
