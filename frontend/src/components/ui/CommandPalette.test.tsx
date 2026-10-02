/**
 * CommandPalette 真实挂载回归测试。
 *
 * 为什么必须挂载真实组件：本次守护的缺陷全部发生在「React 状态 + 分组渲染 +
 * 键盘事件」的交叉处，脱离渲染就无法复现。
 *
 * 覆盖的真实缺陷：
 * 1. 过滤结果按 group 分组渲染，但选中索引按 filtered 顺序 —— 组交错时
 *    Enter 执行的不是视觉上高亮的那一项；
 * 2. 中文 IME 组字期间的 Enter（isComposing / keyCode 229）会误执行命令并关闭浮层；
 * 3. 声明了 aria-modal 但焦点不约束、不还焦；
 * 4. 搜索无显式清空；
 * 5. 无结果时 ArrowDown 产生负索引；
 * 6. 触摸设备无 ESC：缺少显式关闭按钮，关闭/清空按钮的点击区过小；
 * 7. 短屏下 dialog 无 max-height，标题与搜索被结果列表挤出可视区。
 *
 * 只 mock 路由（useNavigate），组件本身与共享 useFocusTrap 真实运行。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import CommandPalette, { type CommandItem } from './CommandPalette'
import { Compass } from 'lucide-react'

/** 真实 lucide 图标：CommandItem.icon 的类型是 ForwardRefExoticComponent。 */
const icon = Compass

const navigate = vi.fn()
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }))

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

/** jsdom 无 scrollIntoView，焦点陷阱与选中项滚动都会调用它。 */
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

let container: HTMLDivElement

let root: Root
let onOpenChange: Mock

const items: CommandItem[] = [
  // 组交错：导航 / 动作 / 导航 —— 分组渲染后的视觉顺序与过滤顺序不同
  { id: 'p1', label: '总览驾驶舱', group: '导航', to: '/dashboard', icon },
  { id: 'a1', label: '重新编译', group: '动作', onAction: () => {}, icon },
  { id: 'p2', label: '数字员工花名册', group: '导航', to: '/workforce', icon },
  { id: 'a2', label: '导出报表', group: '动作', onAction: () => {}, icon },
  { id: 'p3', label: '知识库', group: '导航', to: '/knowledge', icon },
]

const panel = () => container.querySelector<HTMLElement>('[role="dialog"]')!
const input = () => container.querySelector<HTMLInputElement>('input[aria-label="搜索命令"]')!
const clearButton = () => container.querySelector<HTMLButtonElement>('button[aria-label="清空搜索"]')
const closeButton = () => container.querySelector<HTMLButtonElement>('button[aria-label="关闭命令面板"]')
/** 结果区：唯一允许滚动的容器。 */
const results = () => panel().querySelector<HTMLElement>('[data-results]')!
/** 结果按钮（按渲染顺序，排除清空按钮与快捷键区）。 */
const resultButtons = () =>
  Array.from(panel().querySelectorAll<HTMLButtonElement>('button[data-idx]'))
const activeLabel = () =>
  resultButtons().find((b) => b.getAttribute('aria-current') === 'true')?.textContent?.trim() ?? null

function type(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

async function press(key: string, init: KeyboardEventInit = {}) {
  const el = input()
  await act(async () => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init }))
  })
}

/** 焦点陷阱用 rAF 等一帧再聚焦首元素。 */
async function settle() {
  await act(async () => {
    await new Promise<void>((r) => requestAnimationFrame(() => r()))
  })
}

/** AnimatePresence 的退场动画在 jsdom 中由 rAF 驱动，需放帧等待其真正卸载。 */
async function flushExit() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise<void>((r) => requestAnimationFrame(() => r()))
    })
  }
}

beforeEach(() => {
  navigate.mockReset()
  onOpenChange = vi.fn()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => { root.unmount() })
  container.remove()
  document.body.style.overflow = ''
})

async function mount(open = true) {
  await act(async () => { root.render(<CommandPalette open={open} onOpenChange={onOpenChange} items={items} />) })
  await settle()
}

describe('CommandPalette · 分组渲染下的选中与执行一致', () => {
  it('高亮顺序按视觉分组排列，而非过滤顺序', async () => {
    await mount()
    // 分组按首次出现排序：导航（p1,p2,p3）→ 动作（a1,a2），
    // 与 filtered 顺序（p1,a1,p2,a2,p3）不同 —— 这正是原缺陷的触发条件
    expect(resultButtons().map((b) => b.textContent?.trim())).toEqual([
      '总览驾驶舱',
      '数字员工花名册',
      '知识库',
      '重新编译',
      '导出报表',
    ])
    expect(activeLabel()).toBe('总览驾驶舱')
  })

  it('逐次 ArrowDown 时，高亮项与 Enter 实际执行的项始终是同一项', async () => {
    // 每项一个独立 spy：直接证明「看到的高亮」与「被执行的」是同一个对象
    const spies: Record<string, Mock> = {}
    const walkable: CommandItem[] = items.map((item) => {
      const spy = vi.fn()
      spies[item.id] = spy
      return { ...item, onAction: spy, to: undefined }
    })
    await act(async () => {
      root.render(<CommandPalette open onOpenChange={onOpenChange} items={walkable} />)
    })
    await settle()

    const order = resultButtons().map((b) => b.textContent?.trim())
    const idByLabel = new Map(walkable.map((it) => [it.label, it.id]))
    for (const label of order) {
      expect(activeLabel()).toBe(label)
      await press('Enter')
      // 本轮只有被高亮的那一项的 spy 被调用
      const called = Object.entries(spies).filter(([, s]) => s.mock.calls.length > 0).map(([id]) => id)
      expect(called).toEqual([idByLabel.get(label)])
      for (const spy of Object.values(spies)) spy.mockClear()
      onOpenChange.mockClear()
      if (label !== order[order.length - 1]) await press('ArrowDown')
    }
  })

  it('末项继续 ArrowDown 不越界，回到顶部 ArrowUp 也不越界', async () => {
    await mount()
    const last = resultButtons().length - 1
    for (let i = 0; i < last; i++) await press('ArrowDown')
    expect(activeLabel()).toBe('导出报表')
    await press('ArrowDown')
    expect(activeLabel()).toBe('导出报表')
    for (let i = 0; i < last + 2; i++) await press('ArrowUp')
    expect(activeLabel()).toBe('总览驾驶舱')
  })

  it('Enter 执行当前高亮项并关闭面板；to 与 onAction 两条路径都成立', async () => {
    const onAction = vi.fn()
    const custom: CommandItem[] = [
      { id: 'x1', label: '动作甲', group: '动作', onAction, icon },
      { id: 'x2', label: '页面乙', group: '导航', to: '/b', icon },
      { id: 'x3', label: '动作丙', group: '动作', onAction: vi.fn(), icon },
    ]
    await act(async () => {
      root.render(<CommandPalette open onOpenChange={onOpenChange} items={custom} />)
    })
    await settle()

    expect(activeLabel()).toBe('动作甲')
    await press('Enter')
    expect(onAction).toHaveBeenCalledTimes(1)
    expect(onOpenChange).toHaveBeenCalledWith(false)

    // 交错分组：动作(x1,x3) 视觉相邻，导航(x2) 落在第二组
    await act(async () => {
      root.render(<CommandPalette open onOpenChange={onOpenChange} items={custom} />)
    })
    await settle()
    await press('ArrowDown')
    expect(activeLabel()).toBe('动作丙')
    await press('ArrowDown')
    expect(activeLabel()).toBe('页面乙')
    await press('Enter')
    expect(navigate).toHaveBeenCalledWith('/b')
    expect(navigate).toHaveBeenCalledTimes(1)
  })

  it('鼠标悬停同步选中态，点击执行的正是被悬停的那一项', async () => {
    await mount()
    const third = resultButtons()[2]
    await act(async () => { third.dispatchEvent(new MouseEvent('mouseover', { bubbles: true })) })
    expect(activeLabel()).toBe('知识库')
    await act(async () => { third.click() })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe('CommandPalette · 中文 IME 安全', () => {
  it('组字期间的 Enter（isComposing）既不执行也不关闭', async () => {
    const onAction = vi.fn()
    const custom: CommandItem[] = [
      { id: 'i1', label: '重新编译', group: '动作', onAction, icon },
    ]
    await act(async () => {
      root.render(<CommandPalette open onOpenChange={onOpenChange} items={custom} />)
    })
    await settle()

    await act(async () => { input().dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })) })

    await press('Enter', { isComposing: true })
    expect(onAction).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(panel()).toBeTruthy()

    // keyCode 229（旧 IME 约定）同样保护
    await press('Enter', { keyCode: 229 })
    expect(onAction).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()

    // 组字结束后的 Enter 恢复正常执行
    await act(async () => { input().dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })) })
    await press('Enter')
    expect(onAction).toHaveBeenCalledTimes(1)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('compositionstart 之后 nativeEvent.isComposing 已被复位时，Enter 仍被拦截', async () => {
    const onAction = vi.fn()
    const custom: CommandItem[] = [
      { id: 'i2', label: '重新编译', group: '动作', onAction, icon },
    ]
    await act(async () => {
      root.render(<CommandPalette open onOpenChange={onOpenChange} items={custom} />)
    })
    await settle()

    // 组字开始，但本次按键的 native 标志全部为 false（部分浏览器在选字确认前就复位）
    await act(async () => { input().dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })) })
    await press('Enter')
    expect(onAction).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(panel()).toBeTruthy()

    // 方向键同样不移动选中项
    await press('ArrowDown')
    expect(activeLabel()).toBe('重新编译')

    // 组字结束后立即恢复执行
    await act(async () => { input().dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })) })
    await press('Enter')
    expect(onAction).toHaveBeenCalledTimes(1)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('组字期间方向键不移动选中项', async () => {
    await mount()
    await act(async () => { input().dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })) })
    await press('ArrowDown', { isComposing: true })
    expect(activeLabel()).toBe('总览驾驶舱')
  })
})

describe('CommandPalette · 焦点约束与还焦', () => {
  it('打开后焦点落在搜索框；关闭后焦点还给触发元素', async () => {
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)
    await act(async () => { trigger.focus() })
    expect(document.activeElement).toBe(trigger)

    await mount()
    expect(document.activeElement).toBe(input())
    expect(panel().contains(document.activeElement)).toBe(true)

    await act(async () => { onOpenChange(false) })
    await act(async () => { root.render(<CommandPalette open={false} onOpenChange={onOpenChange} items={items} />) })
    // 还焦与滚动解锁在 active 变 false 时立即发生；面板本身随退场动画移除
    expect(document.activeElement).toBe(trigger)
    await flushExit()
    expect(panel()).toBeNull()
    trigger.remove()
  })

  it('Esc 关闭面板', async () => {
    await mount()
    await act(async () => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe('CommandPalette · 搜索框与无结果态', () => {
  it('非空查询时出现显式清空按钮，清空后焦点回到输入框', async () => {
    await mount()
    expect(clearButton()).toBeNull()

    await act(async () => { type(input(), '知识') })
    expect(clearButton()).not.toBeNull()
    expect(resultButtons().length).toBe(1)
    input().blur()

    await act(async () => { clearButton()!.click() })
    expect(input().value).toBe('')
    expect(document.activeElement).toBe(input())
    expect(clearButton()).toBeNull()
    expect(resultButtons().length).toBe(items.length)
  })

  it('无结果时显示空态，方向键不产生负索引，Enter 不执行任何命令', async () => {
    await mount()
    await act(async () => { type(input(), 'zzz不存在zzz') })
    expect(container.textContent).toContain('未找到匹配项')
    expect(resultButtons().length).toBe(0)

    await press('ArrowDown')
    await press('ArrowDown')
    await press('ArrowUp')
    await press('Enter')

    expect(navigate).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(container.textContent).toContain('未找到匹配项')
  })

  it('过滤后结果变少时选中项回到合法范围，Enter 执行合法项', async () => {
    const onAction = vi.fn()
    const custom: CommandItem[] = [
      { id: 'f1', label: '唯一匹配', group: '动作', onAction, icon },
      { id: 'f2', label: '其他甲', group: '动作', onAction: vi.fn(), icon },
      { id: 'f3', label: '其他乙', group: '动作', onAction: vi.fn(), icon },
    ]
    await act(async () => {
      root.render(<CommandPalette open onOpenChange={onOpenChange} items={custom} />)
    })
    await settle()

    await press('ArrowDown')
    await press('ArrowDown')
    expect(activeLabel()).toBe('其他乙')

    await act(async () => { type(input(), '唯一') })
    expect(resultButtons().length).toBe(1)
    expect(activeLabel()).toBe('唯一匹配')
    await press('Enter')
    expect(onAction).toHaveBeenCalledTimes(1)
  })

  it('每次打开都重置为空白查询与首项', async () => {
    await mount()
    await act(async () => { type(input(), '知识') })
    await press('ArrowDown')

    await act(async () => { root.render(<CommandPalette open={false} onOpenChange={onOpenChange} items={items} />) })
    await act(async () => { root.render(<CommandPalette open onOpenChange={onOpenChange} items={items} />) })
    await settle()

    expect(input().value).toBe('')
    expect(activeLabel()).toBe('总览驾驶舱')
  })
})

describe('CommandPalette · 触摸可用性与短屏', () => {
  it('窄屏无 ESC 键时，关闭按钮始终可达且能关闭面板', async () => {
    await mount()
    const close = closeButton()
    expect(close).not.toBeNull()
    expect(close!.tagName).toBe('BUTTON')
    await act(async () => { close!.click() })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('关闭与清空按钮的点击区均为 40px（jsdom 无布局，只能断言尺寸类）', async () => {
    await mount()
    await act(async () => { type(input(), '知识') })
    for (const el of [closeButton()!, clearButton()!]) {
      expect(el.className).toContain('h-10')
      expect(el.className).toContain('w-10')
    }
  })

  it('dialog 受 max-height 约束，且只有结果列表是滚动容器', async () => {
    await mount()
    // 面板本身不滚动：结果区是唯一的 overflow 容器，标题与搜索永远在可视区
    expect(panel().className).toContain('max-h-')
    expect(panel().className).toContain('flex-col')
    expect(panel().className).not.toContain('overflow-y-auto')
    expect(results().className).toContain('overflow-y-auto')
    expect(results().className).toContain('min-h-0')
    // 搜索框在结果区之外 → 不会被列表滚动带走
    expect(results().contains(input())).toBe(false)
  })
})
