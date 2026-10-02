import { act, useRef } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { useFocusTrap } from './useFocusTrap'

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
let root: Root
let host: HTMLDivElement
let trigger: HTMLButtonElement
const outerClose = vi.fn()
const innerClose = vi.fn()
function Harness({ outer, inner = false, empty = false }: { outer: boolean; inner?: boolean; empty?: boolean }) {
  const outerRef = useRef<HTMLDivElement>(null)
  const innerRef = useRef<HTMLDivElement>(null)
  useFocusTrap({ active: outer, containerRef: outerRef, onClose: outerClose })
  useFocusTrap({ active: inner, containerRef: innerRef, onClose: innerClose })
  return <>
    <button id="background">Background</button>
    {outer && <div ref={outerRef} role="dialog" aria-label="outer">
      {!empty && <><button id="hidden" hidden>Hidden</button><button id="first">First</button><button id="last">Last</button></>}
      {inner && <div ref={innerRef} role="dialog" aria-label="inner"><button id="inner">Inner</button></div>}
    </div>}
  </>
}
const element = (id: string) => document.getElementById(id) as HTMLElement
async function render(props: Parameters<typeof Harness>[0]) {
  await act(async () => { root.render(<Harness {...props} />) })
  await act(async () => vi.advanceTimersByTime(20))
}
async function key(name: string, options: KeyboardEventInit = {}) {
  await act(async () => document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', {
    key: name, bubbles: true, cancelable: true, ...options,
  })))
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  document.body.style.overflow = 'scroll'
  trigger = document.createElement('button')
  trigger.textContent = 'Open'
  document.body.append(trigger)
  trigger.focus()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  trigger.remove(); host.remove()
  document.body.style.overflow = ''
  vi.useRealTimers()
})

it('isolates the background, skips hidden controls, traps Tab and restores focus/scroll', async () => {
  await render({ outer: true })
  expect(document.activeElement).toBe(element('first'))
  expect(element('background').hasAttribute('inert')).toBe(true)
  expect(trigger.hasAttribute('inert')).toBe(true)
  expect(document.body.style.overflow).toBe('hidden')
  await key('Tab', { shiftKey: true })
  expect(document.activeElement).toBe(element('last'))
  await key('Tab')
  expect(document.activeElement).toBe(element('first'))
  await act(async () => trigger.focus())
  expect(document.activeElement).toBe(element('first'))
  await render({ outer: false })
  expect(trigger.hasAttribute('inert')).toBe(false)
  expect(document.body.style.overflow).toBe('scroll')
  expect(document.activeElement).toBe(trigger)
})

it('only the innermost dialog handles Escape, and closing it preserves outer locks', async () => {
  await render({ outer: true })
  await render({ outer: true, inner: true })
  expect(document.activeElement).toBe(element('inner'))
  await key('Escape')
  expect(innerClose).toHaveBeenCalledTimes(1)
  expect(outerClose).not.toHaveBeenCalled()
  await render({ outer: true, inner: false })
  expect(document.body.style.overflow).toBe('hidden')
  expect(trigger.hasAttribute('inert')).toBe(true)
  expect(element('first').hasAttribute('inert')).toBe(false)
  expect(document.activeElement).toBe(element('first'))
  await key('Escape')
  expect(outerClose).toHaveBeenCalledTimes(1)
})

it('ignores IME Escape and preserves preexisting inert state', async () => {
  const preexisting = document.createElement('aside')
  preexisting.setAttribute('inert', 'original')
  document.body.append(preexisting)
  try {
    await render({ outer: true })
    await key('Escape', { isComposing: true })
    await key('Escape', { keyCode: 229 })
    expect(outerClose).not.toHaveBeenCalled()
    await render({ outer: false })
    expect(preexisting.getAttribute('inert')).toBe('original')
  } finally { preexisting.remove() }
})

it('keeps an empty dialog focused when it has no interactive controls', async () => {
  await render({ outer: true, empty: true })
  const dialog = host.querySelector('[role="dialog"]')
  expect(document.activeElement).toBe(dialog)
  await key('Tab')
  expect(document.activeElement).toBe(dialog)
})
