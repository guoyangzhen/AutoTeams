/**
 * Layout 顶栏浮层真实挂载回归测试。
 *
 * 守护的用户可见行为（旧实现各自的缺陷）：
 * 1. 三个浮层是无 menu 语义的内容面板，之前只是裸 div：键盘打开后焦点仍留在触发按钮，
 *    用户必须盲 Tab 才知道里面有什么；
 * 2. Escape 关闭后焦点没有还给触发按钮，键盘用户掉回 <body>；
 * 3. 三块「fixed inset-0」遮罩 + 各自独立的 open 状态，可以同时开两个浮层，
 *    而且遮罩把 Tab 挡在浮层外却让焦点任意落在背景上；
 * 4. 通知浮层与模态混用（focus trap 会把背景 inert、锁滚动），但它根本不是模态；
 * 5. 窄屏下历史 / 用户浮层固定 256px，容易越出视口。
 *
 * 只 mock 数据与路由来源，Layout、usePopoverPanel 与渲染逻辑全部真实运行。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Layout from './Layout'

const logout = vi.fn()
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ user: { id: 'u1', name: '张一', email: 'zhangyi@example.com', role: 'admin' }, logout }) }))
vi.mock('@/hooks/useTheme', () => ({ useTheme: () => ({ theme: 'light', toggleTheme: vi.fn() }) }))
vi.mock('@/hooks/useEnterpriseId', () => ({ useEnterpriseId: () => 'ent-1' }))
vi.mock('@/hooks/useAutonomyMode', () => ({ useAutonomyMode: () => 'full', isNavVisibleForMode: () => true }))
vi.mock('@/hooks/useZone', () => ({ useZone: () => ({ zoneClass: 'workspace' }), useZoneBoot: () => false }))
vi.mock('@/hooks/useNavigationData', () => ({
  useNavigationData: () => ({
    key: '["u1","ent-1"]',
    agents: [],
    files: [],
    approvalIds: ['ap-1', 'ap-2'],
    approvalsLoading: false,
    approvalsError: false,
  }),
}))
// 命令面板是另一条交互线（其行为由 CommandPalette 自己的测试守护），这里只保留挂载点。
vi.mock('@/components/ui/CommandPalette', () => ({ CommandPalette: () => null }))

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

/** jsdom 无 matchMedia：Layout 的断点监听与 framer-motion 的 reduced-motion 都要它。 */
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

let root: Root
let host: HTMLDivElement

const nextFrame = () =>
  new Promise<void>((resolve) => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve())
    else setTimeout(resolve, 0)
  })
/** AnimatePresence 的退出动画结束后元素才真正卸载，断言前必须等它跑完。 */
async function settle() {
  const { promise, resolve } = Promise.withResolvers<void>()
  setTimeout(resolve, 300)
  await act(async () => { await promise })
}

const byLabel = (label: string) =>
  Array.from(host.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.getAttribute('aria-label')?.startsWith(label))!
const notifTrigger = () => byLabel('通知')
const historyTrigger = () => byLabel('历史记录')
const userTrigger = () => byLabel('用户菜单')
const panel = (label: string) => host.querySelector<HTMLElement>(`[role="dialog"][aria-label="${label}"]`)
const openPanels = () => Array.from(host.querySelectorAll<HTMLElement>('[role="dialog"]'))

async function render(path = '/dashboard') {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <Layout>
          <p>页面正文</p>
        </Layout>
      </MemoryRouter>,
    )
  })
}

async function activate(trigger: HTMLButtonElement) {
  await act(async () => { trigger.focus(); trigger.click() })
  await act(async () => { await nextFrame() })
  await settle()
}

async function pressEscape(init: KeyboardEventInit = {}) {
  await act(async () => {
    document.activeElement?.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true, ...init }),
    )
  })
  await settle()
}


beforeEach(async () => {
  localStorage.clear()
  document.body.innerHTML = ''
  document.body.style.overflow = ''
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await render()
})

afterEach(async () => {
  await act(async () => { root.unmount() })
  host.remove()
})

describe('顶栏浮层的键盘模型', () => {
  it('打开后焦点进入面板内的有效控件，而不是留在触发按钮', async () => {
    await activate(notifTrigger())
    const notif = panel('通知中心')
    expect(notif).not.toBeNull()
    expect(document.activeElement).toBe(notif!.querySelector('a[href]'))
    expect(document.activeElement?.textContent).toContain('项待审批')

    await activate(historyTrigger())
    expect(document.activeElement).toBe(panel('最近操作')!.querySelector('a[href]'))
  })

  it('Escape 关闭浮层并把焦点还给各自的触发按钮', async () => {
    for (const [trigger, label] of [
      [notifTrigger, '通知中心'],
      [historyTrigger, '最近操作'],
      [userTrigger, '用户菜单'],
    ] as const) {
      await activate(trigger())
      expect(panel(label)).not.toBeNull()
      await pressEscape()
      expect(panel(label)).toBeNull()
      expect(document.activeElement).toBe(trigger())
      expect(trigger().getAttribute('aria-expanded')).toBe('false')
    }
  })

  it('中文输入法组字期间的 Escape 不关闭浮层', async () => {
    await activate(notifTrigger())
    await pressEscape({ isComposing: true })
    expect(panel('通知中心')).not.toBeNull()
    await pressEscape({ keyCode: 229 } as KeyboardEventInit)
    expect(panel('通知中心')).not.toBeNull()
  })

  it('再次点击触发按钮可重复开关', async () => {
    await activate(notifTrigger())
    expect(notifTrigger().getAttribute('aria-expanded')).toBe('true')
    await activate(notifTrigger())
    expect(panel('通知中心')).toBeNull()
    expect(notifTrigger().getAttribute('aria-expanded')).toBe('false')
  })
})

describe('浮层的互斥与背景行为', () => {
  it('同一时刻只开一个浮层，焦点交给新打开的那个', async () => {
    await activate(notifTrigger())
    await activate(historyTrigger())
    expect(openPanels().map((p) => p.getAttribute('aria-label'))).toEqual(['最近操作'])
    expect(document.activeElement).toBe(panel('最近操作')!.querySelector('a[href]'))

    await activate(userTrigger())
    expect(openPanels().map((p) => p.getAttribute('aria-label'))).toEqual(['用户菜单'])
    expect(notifTrigger().getAttribute('aria-expanded')).toBe('false')
    expect(historyTrigger().getAttribute('aria-expanded')).toBe('false')
    expect(userTrigger().getAttribute('aria-expanded')).toBe('true')
  })

  it('浮层不是模态：背景不被 inert，滚动不被锁', async () => {
    await activate(notifTrigger())
    expect(document.querySelectorAll('[inert]')).toHaveLength(0)
    expect(document.body.style.overflow).not.toBe('hidden')
    expect(panel('通知中心')!.getAttribute('aria-modal')).toBeNull()
  })

  it('移开焦点（Tab 离开）即关闭，且不把焦点从新目标抢回来', async () => {
    await activate(notifTrigger())
    const theme = host.querySelector<HTMLButtonElement>('button[aria-label^="切换到"]')!
    await act(async () => { theme.focus() })
    await settle()
    expect(panel('通知中心')).toBeNull()
    expect(document.activeElement).toBe(theme)
  })

  it('按外部可聚焦控件关闭浮层：不再依赖吞掉点击的全屏遮罩', async () => {
    await activate(userTrigger())
    const theme = host.querySelector<HTMLButtonElement>('button[aria-label^="切换到"]')!
    await act(async () => { theme.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })) })
    await settle()
    expect(panel('用户菜单')).toBeNull()
    expect(userTrigger().getAttribute('aria-expanded')).toBe('false')
  })

  it('循环开关后再开历史：面板不会被上一个浮层的过期关闭回调连带关掉', async () => {
    // 复现路径：历史 → Escape → 用户面板反复开关 → 再开历史。
    await activate(historyTrigger())
    await pressEscape()
    await activate(userTrigger())
    await activate(userTrigger())
    await activate(userTrigger())
    expect(panel('用户菜单')).not.toBeNull()

    await activate(historyTrigger())
    expect(openPanels().map((p) => p.getAttribute('aria-label'))).toEqual(['最近操作'])
    expect(historyTrigger().getAttribute('aria-expanded')).toBe('true')
    expect(document.activeElement).toBe(panel('最近操作')!.querySelector('a[href]'))
  })

  it('三个浮层连续互相切换时，每次打开的面板都保持打开并拿到焦点', async () => {
    for (const [trigger, label, firstText] of [
      [notifTrigger, '通知中心', '项待审批'],
      [historyTrigger, '最近操作', '总览驾驶舱'],
      [userTrigger, '用户菜单', '账户设置'],
      [historyTrigger, '最近操作', '总览驾驶舱'],
      [notifTrigger, '通知中心', '项待审批'],
    ] as const) {
      await activate(trigger())
      expect(openPanels().map((p) => p.getAttribute('aria-label'))).toEqual([label])
      expect(document.activeElement?.textContent).toContain(firstText)
    }
  })
})

describe('语义与窄屏约束', () => {
  it('触发按钮声明 popover 语义，面板是无 modal 的 dialog', async () => {
    expect(notifTrigger().getAttribute('aria-haspopup')).toBe('dialog')
    expect(notifTrigger().getAttribute('role')).toBeNull()
    await activate(notifTrigger())
    expect(panel('通知中心')!.id).toBe(notifTrigger().getAttribute('aria-controls'))
    expect(openPanels()).toHaveLength(1)
    expect(openPanels()[0].querySelector('[role="menu"]')).toBeNull()
  })

  it('三个浮层在窄屏都被限制在视口宽度内', async () => {
    const narrow = 'max-w-[calc(100vw-1.5rem)]'
    for (const [trigger, label] of [
      [notifTrigger, '通知中心'],
      [historyTrigger, '最近操作'],
      [userTrigger, '用户菜单'],
    ] as const) {
      await activate(trigger())
      expect(panel(label)!.className).toContain(narrow)
      await pressEscape()
    }
  })

  it('历史为空时焦点落在面板本身，键盘用户不会掉回 body', async () => {
    // 非导航路径不会写入访问历史，面板里因此一个可聚焦控件都没有。
    await act(async () => { root.unmount() })
    // 上一轮挂载写入的访问历史会让面板非空；空态要真正为空。
    localStorage.clear()
    host.remove()
    host = document.createElement('div')
    document.body.append(host)
    root = createRoot(host)
    await render('/unregistered-page')
    await activate(historyTrigger())
    expect(panel('最近操作')!.textContent).toContain('暂无历史记录')
    expect(document.activeElement).toBe(panel('最近操作'))
    expect(document.activeElement).not.toBe(document.body)
  })

  it('点击浮层内链接后关闭并完成跳转', async () => {
    await activate(userTrigger())
    const settings = panel('用户菜单')!.querySelector<HTMLAnchorElement>('a[href="/settings"]')!
    await act(async () => { settings.click() })
    await settle()
    expect(panel('用户菜单')).toBeNull()
  })
})
