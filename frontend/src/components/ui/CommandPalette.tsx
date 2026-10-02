/**
 * CommandPalette — ⌘K 全局命令面板。
 *
 * 提供页面快捷导航 + 常用动作入口，键盘可达：
 * - ⌘K / Ctrl+K 打开（由 Layout 持有按键监听）
 * - ↑↓ 选择，Enter 执行，Esc 关闭
 * - 输入关键词本地过滤（子串 + 子序列），无远程请求，故无 debounce
 *
 * 可达性要点（此前版本的真实缺陷，均在本文件内修复）：
 * - 选中索引与渲染顺序同源：过滤结果先按分组归并，再由 grouped 展平为 ordered，
 *   高亮、data-idx、方向键与 Enter 全部读同一个 ordered，不再出现「视觉选中 A、
 *   Enter 执行 B」。绘制时在组内按 ordered 下标着色，而非用渲染计数器。
 * - 焦点真正被困住：复用共享 useFocusTrap（与 Dialog/Drawer 同一实现），
 *   关闭后焦点还给触发元素；打开期间锁定 body 滚动。
 * - 中文/日文 IME：组字期间的 Enter（isComposing / keyCode 229）不执行、不关闭，
 *   Escape 也不关闭浮层，避免选字确认被误当成「执行」。
 * - 无结果时方向键不产生负索引，Enter 静默无动作。
 * - 搜索框提供显式清空按钮，清空后焦点回到输入框。
 * - 窄屏与长文本：flex 子项统一 min-w-0，标签 truncate，只有结果区滚动。
 * - 触摸可用：关闭与清空都是 40px 实体按钮（窄屏无 ESC 键）；dialog 受
 *   max-height 约束，标题行与搜索行不参与滚动，短屏也永远可达。
 *
 * 语义选择：结果保持原生 <button>（每项可独立聚焦、可点击、可被读屏遍历），
 * 因此不使用 combobox/listbox 角色；选中态用 aria-current 暴露，
 * 结果数量通过 aria-live 播报。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Search, X, CornerDownLeft, ArrowUp, ArrowDown } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useFocusTrap } from '@/hooks/useFocusTrap'

export interface CommandItem {
  id: string
  label: string
  /** 副标题/分组 */
  group: string
  icon: LucideIcon
  /** 路由跳转（与 onAction 二选一） */
  to?: string
  /** 自定义动作 */
  onAction?: () => void
  /** 关键词（用于搜索匹配，默认用 label） */
  keywords?: string[]
}

interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  items: CommandItem[]
}

const TITLE_ID = 'command-palette-title'
const INPUT_ID = 'command-palette-input'

/** 键盘事件是否处于 IME 组字期：中文/日文输入法的 Enter 是「选字」而非「执行」。 */
function isComposingEvent(e: React.KeyboardEvent): boolean {
  return e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229
}

export function CommandPalette({ open, onOpenChange, items }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [composing, setComposing] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const reduceMotion = useReducedMotion()
  const navigate = useNavigate()

  // 每次打开都从空白查询、首个结果开始（浮层不保留上次会话的选择）
  useEffect(() => {
    if (!open) return
    setQuery('')
    setActiveIndex(0)
    setComposing(false)
  }, [open])

  // 滚动锁、背景 inert、顶层模态独占键盘由共享 useFocusTrap 统一提供，此处不重复实现。

  const close = useCallback(() => onOpenChange(false), [onOpenChange])

  // 复用共享焦点陷阱：打开聚焦首个可聚焦元素（搜索框）、Tab 循环、Esc 关闭、关闭还焦。
  // 组字期间关闭 Esc：避免中文选字确认直接把浮层关掉。
  useFocusTrap({ active: open, containerRef: panelRef, onClose: close, closeOnEscape: !composing })

  // 过滤：子串匹配优先，回退到子序列匹配（支持缩写/首字母）
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    const isSubsequence = (haystack: string, needle: string): boolean => {
      let i = 0
      for (const ch of haystack) {
        if (ch === needle[i]) i++
        if (i === needle.length) return true
      }
      return false
    }
    return items.filter((item) => {
      const haystack = [item.label, item.group, ...(item.keywords || [])]
        .join(' ')
        .toLowerCase()
      // 子串匹配（最常见：输入"员工"命中"AI 员工"）
      if (haystack.includes(q)) return true
      // 子序列匹配（缩写：输入"zsk"命中含"zsk"关键词；输入"yghl"等）
      if (isSubsequence(haystack, q)) return true
      return false
    })
  }, [items, query])

  // 分组与视觉顺序一次算出，导航下标在分组完成后按视觉顺序重新编号。
  // sections 的展开顺序即视觉顺序，也是键盘导航顺序 —— 高亮与 Enter 天然一致。
  const { sections, ordered } = useMemo(() => {
    const map = new Map<string, CommandItem[]>()
    for (const item of filtered) {
      const bucket = map.get(item.group)
      if (bucket) bucket.push(item)
      else map.set(item.group, [item])
    }
    const flat = Array.from(map.values()).flat()
    return {
      sections: Array.from(map, ([group, groupItems]) => ({ group, items: groupItems })),
      ordered: flat,
    }
  }, [filtered])

  // 下标必须由视觉顺序派生：分组后重新编号，不能沿用过滤顺序的计数
  const indexByItem = useMemo(() => {
    const map = new Map<CommandItem, number>()
    ordered.forEach((item, index) => map.set(item, index))
    return map
  }, [ordered])

  // 过滤后结果变少时把选中项拉回合法范围（避免高亮消失后 Enter 无声失效）
  useEffect(() => {
    if (activeIndex >= ordered.length) setActiveIndex(0)
  }, [ordered.length, activeIndex])

  // 键盘移动选中项时，把它滚进结果区可视范围（分组后下标即 data-idx）
  useEffect(() => {
    if (!open) return
    panelRef.current
      ?.querySelector<HTMLElement>(`[data-idx="${activeIndex}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [open, activeIndex, ordered.length])

  const execute = useCallback(
    (item: CommandItem | undefined) => {
      if (!item) return
      onOpenChange(false)
      if (item.to) {
        navigate(item.to)
      } else if (item.onAction) {
        item.onAction()
      }
    },
    [navigate, onOpenChange],
  )

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // 组字期间：Enter 属于输入法选字，任何键都不应触发命令。
    // 双保险：React 合成事件可能已清掉 nativeEvent.isComposing（部分浏览器在
    // compositionend 前就复位），因此 composing 状态与 native 标志必须同时检查。
    if (composing || isComposingEvent(e)) {
      if (e.key === 'Escape') e.stopPropagation()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => (ordered.length > 0 ? Math.min(i + 1, ordered.length - 1) : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => (ordered.length > 0 ? Math.max(i - 1, 0) : 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      execute(ordered[activeIndex])
    }
    // Escape 交由 useFocusTrap 在 document 上处理（组字期已在上方拦截）
  }

  const handleClear = () => {
    setQuery('')
    setActiveIndex(0)
    inputRef.current?.focus()
  }

  return (
    <AnimatePresence>
      {open && (
        <div
          className="fixed inset-0 z-[100] flex items-start justify-center px-4 pt-[10vh] sm:pt-[12vh]"
          onClick={close}
        >
          <div className="absolute inset-0 bg-black/40" aria-hidden="true" />
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={TITLE_ID}
            initial={reduceMotion ? false : { opacity: 0, y: -6, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduceMotion ? undefined : { opacity: 0, y: -6, scale: 0.985 }}
            transition={{ duration: reduceMotion ? 0 : 0.14, ease: 'easeOut' }}
            className="relative flex max-h-[88dvh] w-full max-w-xl flex-col overflow-hidden rounded-[10px] border border-border-default bg-surface shadow-lift sm:max-h-[86dvh]"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id={TITLE_ID} className="sr-only">
              命令面板
            </h2>

            {/* 标题行：面板名 + 结果计数（计数同时作为读屏播报） */}
            <div className="flex shrink-0 items-baseline justify-between gap-3 border-b border-border-subtle px-4 pt-3.5">
              <p className="min-w-0 truncate text-caption uppercase tracking-wide text-text-muted">命令面板</p>
              <p aria-live="polite" className="flex-shrink-0 text-caption tabular-nums text-text-muted">
                {query.trim() ? `${ordered.length} 项匹配` : `${ordered.length} 项`}
              </p>
            </div>

            {/* 搜索输入：flex-1 + min-w-0，窄屏也不会把清空/快捷键挤出可视区 */}
            <div className="flex shrink-0 items-center gap-3 border-b border-border-subtle px-4">
              <Search className="w-4 h-4 flex-shrink-0 text-text-tertiary" aria-hidden="true" />
              <input
                id={INPUT_ID}
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                  setActiveIndex(0)
                }}
                onKeyDown={handleKeyDown}
                onCompositionStart={() => {
                  setComposing(true)
                }}
                onCompositionEnd={() => {
                  setComposing(false)
                }}
                placeholder="搜索页面或动作…"
                aria-label="搜索命令"
                autoComplete="off"
                spellCheck={false}
                className="min-w-0 flex-1 bg-transparent py-3 text-sm text-text-primary placeholder:text-text-tertiary focus:outline-none"
              />
              {query !== '' && (
                <button
                  type="button"
                  onClick={handleClear}
                  aria-label="清空搜索"
                  className="-my-2 -mr-2 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[6px] text-text-tertiary transition-colors hover:bg-elevated hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
                >
                  <X className="w-4 h-4" aria-hidden="true" />
                </button>
              )}
              {/* 显式关闭：窄屏无 ESC 键，必须有 40px 可点的实体按钮；
                  放在搜索框之后，保证焦点陷阱打开时仍聚焦搜索框 */}
              <button
                type="button"
                onClick={close}
                aria-label="关闭命令面板"
                className="-my-2 -mr-2 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[6px] text-text-tertiary transition-colors hover:bg-elevated hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
              <kbd className="hidden flex-shrink-0 rounded border border-border-default bg-elevated px-1.5 py-0.5 font-mono text-caption text-text-tertiary sm:block">
                ESC
              </kbd>
            </div>

            {/* 结果列表：内部滚动，组标题与条目共用一个视觉顺序 */}
            <div data-results className="min-h-0 flex-1 overflow-y-auto py-2">
              {ordered.length === 0 ? (
                <div className="px-4 py-10 text-center">
                  <p className="text-sm text-text-tertiary">未找到匹配项</p>
                  <p className="mt-1 text-caption text-text-muted">试试输入「编译」「员工」「知识库」</p>
                </div>
              ) : (
                <ul className="m-0 list-none p-0">
                  {sections.map((section) => (
                    <li key={section.group} className="mb-1 last:mb-0">
                      <div className="px-4 py-1.5 text-caption font-medium text-text-muted">
                        {section.group}
                      </div>
                      <ul className="m-0 list-none p-0">
                        {section.items.map((item) => {
                          const idx = indexByItem.get(item) ?? 0
                          const active = idx === activeIndex
                          const Icon = item.icon
                          return (
                            <li key={item.id}>
                              <button
                                type="button"
                                data-idx={idx}
                                aria-current={active ? 'true' : undefined}
                                onMouseEnter={() => setActiveIndex(idx)}
                                onClick={() => execute(item)}
                                className={`flex w-full items-center gap-3 border-l-2 px-4 py-2.5 text-left transition-colors focus:outline-none focus-visible:bg-elevated ${
                                  active
                                    ? 'border-brand-500 bg-brand-50 text-brand-700'
                                    : 'border-transparent text-text-secondary hover:bg-elevated'
                                }`}
                              >
                                <Icon className="w-4 h-4 flex-shrink-0 opacity-70" aria-hidden="true" />
                                <span className="min-w-0 flex-1 truncate text-sm font-medium">
                                  {item.label}
                                </span>
                                {active && (
                                  <CornerDownLeft
                                    className="w-3.5 h-3.5 flex-shrink-0 text-brand-400"
                                    aria-hidden="true"
                                  />
                                )}
                              </button>
                            </li>
                          )
                        })}
                      </ul>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* 快捷键提示：仅宽屏显示，窄屏优先留给搜索与结果 */}
            <div className="hidden shrink-0 items-center justify-between border-t border-border-subtle bg-elevated/50 px-4 py-2 sm:flex">
              <div className="flex items-center gap-4 text-caption text-text-tertiary">
                <span className="flex items-center gap-1">
                  <ArrowUp className="w-3 h-3" aria-hidden="true" />
                  <ArrowDown className="w-3 h-3" aria-hidden="true" />
                  选择
                </span>
                <span className="flex items-center gap-1">
                  <CornerDownLeft className="w-3 h-3" aria-hidden="true" />
                  执行
                </span>
                <span className="flex items-center gap-1">
                  <kbd className="rounded border border-border-default bg-surface px-1 font-mono">ESC</kbd>
                  关闭
                </span>
              </div>
              <span className="text-caption text-text-muted">AutoTeams</span>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}

export default CommandPalette
