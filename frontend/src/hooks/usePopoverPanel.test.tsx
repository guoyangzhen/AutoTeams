/**
 * usePopoverPanel 真实挂载回归测试。
 *
 * 守护的是顶栏三个浮层共享的行为契约：
 * 1. 打开后焦点必须落到面板内的有效控件（而不是留在触发按钮上让用户重新 Tab）；
 * 2. Escape 关闭并把焦点还给对应触发按钮；
 * 3. 同一时刻只开一个浮层，让位的一方把焦点交还自己的触发按钮；
 * 4. Tab 移出面板即关闭，且不把焦点从新目标抢回来；
 * 5. 中文输入法组字期间的 Escape 不关闭；
 * 6. 面板空到没有可聚焦控件时，焦点落在面板本身，键盘用户不会掉回 <body>。
 */
import { act, useRef, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { usePopoverPanel } from './usePopoverPanel'

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

let root: Root
let host: HTMLDivElement

/** rAF 存在与否取决于 jsdom 的 pretendToBeVisual；hook 用 rAF 等待面板渲染完再移焦。 */
const nextFrame = () =>
  new Promise<void>((resolve) => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve())
    else setTimeout(resolve, 0)
  })

function Popover({
  label,
  empty = false,
  log,
}: {
  label: string
  empty?: boolean
  log: string[]
}) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  usePopoverPanel({
    open,
    panelRef,
    triggerRef,
    onClose: (reason) => {
      log.push(`${label}:${reason}`)
      setOpen(false)
    },
  })
  return (
    <div>
      <button ref={triggerRef} onClick={() => setOpen((o) => !o)}>{`打开${label}`}</button>
      {open && (
        <div ref={panelRef} role="dialog" aria-label={label} tabIndex={-1}>
          {empty ? <p>{label}没有可聚焦控件</p> : (
            <>
              <button>{`${label}第一项`}</button>
              <button>{`${label}第二项`}</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function Harness(props: { empty?: boolean; log: string[]; mounted?: string[]; version?: number }) {
  const mounted = props.mounted ?? ['通知', '历史']
  return (
    <>
      <button id="outside">页面其它控件</button>
      {mounted.map((label) => <Popover key={label} label={label} empty={props.empty} log={props.log} />)}
      {/* version 只用来制造一次 rerender：popover 实例不重挂，关闭回调必须仍然有效。 */}
      <span data-testid="version">{props.version ?? 0}</span>
    </>
  )
}

const el = (id: string) => document.getElementById(id) as HTMLElement
const trigger = (label: string) =>
  Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent === `打开${label}`)!
const panel = (label: string) => document.querySelector<HTMLElement>(`[role="dialog"][aria-label="${label}"]`)
const panels = () => Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]'))

async function render(props: { empty?: boolean; log: string[]; mounted?: string[]; version?: number }) {
  await act(async () => { root.render(<Harness {...props} />) })
}

/** rerender 同一个实例：holder 与 effect 必须仍然指向当前这一份关闭回调。 */
async function rerender(props: { empty?: boolean; log: string[]; mounted?: string[]; version?: number }) {
  await act(async () => { root.render(<Harness {...props} />) })
}

async function activate(label: string) {
  await act(async () => { trigger(label).click() })
  await act(async () => { await nextFrame() })
}

async function pressEscape(init: KeyboardEventInit = {}) {
  await act(async () => {
    document.activeElement?.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true, ...init }),
    )
  })
}

beforeEach(() => {
  document.body.innerHTML = ''
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => { root.unmount() })
  host.remove()
})

it('opening moves focus to the first control inside the panel', async () => {
  const log: string[] = []
  await render({ log })
  await activate('通知')
  expect(document.activeElement?.textContent).toBe('通知第一项')
})

it('Escape closes the panel and returns focus to its own trigger', async () => {
  const log: string[] = []
  await render({ log })
  await activate('通知')
  await pressEscape()
  expect(panel('通知')).toBeNull()
  expect(log).toEqual(['通知:escape'])
  expect(document.activeElement).toBe(trigger('通知'))
})

it('opening a sibling closes the previous one and hands focus over', async () => {
  const log: string[] = []
  await render({ log })
  await activate('通知')
  await activate('历史')
  expect(panels().map((p) => p.getAttribute('aria-label'))).toEqual(['历史'])
  expect(log).toEqual(['通知:sibling'])
  expect(document.activeElement?.textContent).toBe('历史第一项')
})

it('leaving the panel with Tab closes it without stealing focus back', async () => {
  const log: string[] = []
  await render({ log })
  await activate('通知')
  const outside = el('outside')
  await act(async () => { outside.focus() })
  expect(panel('通知')).toBeNull()
  expect(log).toEqual(['通知:focus-out'])
  expect(document.activeElement).toBe(outside)
})

it('ignores Escape while an IME composition is in progress', async () => {
  const log: string[] = []
  await render({ log })
  await activate('通知')
  await pressEscape({ isComposing: true })
  expect(panel('通知')).not.toBeNull()
  await pressEscape({ keyCode: 229 } as KeyboardEventInit)
  expect(panel('通知')).not.toBeNull()
})

it('a click outside closes the panel and leaves focus where the user clicked', async () => {
  const log: string[] = []
  await render({ log })
  await activate('通知')
  const outside = el('outside')
  await act(async () => { outside.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })) })
  expect(panel('通知')).toBeNull()
  expect(log).toEqual(['通知:outside'])
})

it('falls back to the panel itself when it has nothing focusable', async () => {
  const log: string[] = []
  await render({ log, empty: true })
  await activate('通知')
  expect(document.activeElement).toBe(panel('通知'))
})

it('rerender 后 Escape 仍关闭并还焦，holder 不会变成过期引用', async () => {
  const log: string[] = []
  await render({ log })
  await activate('通知')
  await rerender({ log, version: 1 })
  await rerender({ log, version: 2 })
  await pressEscape()
  expect(panel('通知')).toBeNull()
  expect(document.activeElement).toBe(trigger('通知'))

  // holder 若还指着 rerender 之前的旧回调，打开下一个浮层时就会再通知一次已关闭的实例。
  await activate('历史')
  expect(log).toEqual(['通知:escape'])
  expect(panels().map((p) => p.getAttribute('aria-label'))).toEqual(['历史'])
  expect(document.activeElement?.textContent).toBe('历史第一项')
})

it('打开状态下 rerender 再卸载：重开第二个面板不通知旧实例、不还焦到已脱离文档的按钮', async () => {
  const log: string[] = []
  await render({ log })
  await activate('通知')
  await rerender({ log, version: 1 })
  await rerender({ log, mounted: ['历史'] })
  expect(panel('通知')).toBeNull()

  await activate('历史')
  expect(log).toEqual([])
  expect(panels().map((p) => p.getAttribute('aria-label'))).toEqual(['历史'])
  expect(document.activeElement?.textContent).toBe('历史第一项')
  expect(document.activeElement).not.toBe(document.body)
})
