/**
 * LocalConnectionPanel 真实挂载回归测试。
 *
 * 为什么必须挂载真实 React 组件而不是只测纯函数：本次要守护的缺陷全部发生在
 * 「异步请求 + React 状态 + 定时器」的交叉处——
 * - 变更（注册/撤销）发生在列表请求在途时，旧响应会覆盖或复活状态；
 * - 凭据作废后只是隐藏会被陈旧数据复活，必须从状态里清除；
 * - 令牌过期依赖独立定时器与复制瞬间的 Date.now 兜底（轮询失败/标签页休眠也要生效）；
 * - 剪贴板失败必须可见。
 *
 * 全部用例只 mock 网络层（@/api/localPaths），组件本身真实渲染、真实响应状态更新。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LocalConnectionPanel from './LocalConnectionPanel'
import {
  listLocalPaths, registerLocalPath, revokeLocalPath,
  type LocalPathGrant, type RegisterLocalPathResult,
} from '@/api/localPaths'

vi.mock('@/api/localPaths', () => ({
  listLocalPaths: vi.fn(),
  registerLocalPath: vi.fn(),
  revokeLocalPath: vi.fn(),
  expandLocalTools: () => [],
}))

const listMock = vi.mocked(listLocalPaths)
const registerMock = vi.mocked(registerLocalPath)
const revokeMock = vi.mocked(revokeLocalPath)

/** 轮询间隔需与 LocalConnectionPanel.POLL_INTERVAL_MS 一致。 */
const POLL_MS = 5000
const SETUP_COMMAND = 'autoteams-runner connect --token tok-1 --grant grant-1'

/** React 18 要求显式声明 act 环境，否则会警告并可能不同步。 */
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function makeGrant(over: Partial<LocalPathGrant> = {}): LocalPathGrant {
  return {
    id: 'grant-1',
    enterprise_id: 'ent-1',
    user_id: 'user-1',
    label: '销售资料库',
    local_path: 'D:\\项目\\销售资料',
    scope: 'read_write',
    status: 'pending',
    runner_id: null,
    tool_manifest: null,
    resolved_path: null,
    created_at: '2026-09-30T09:59:00Z',
    updated_at: '2026-09-30T09:59:00Z',
    ...over,
  }
}

function makeRegisterResult(ttlMs = 10 * 60_000): RegisterLocalPathResult {
  return {
    grant: makeGrant(),
    setup_token: 'tok-1',
    setup_command: SETUP_COMMAND,
    setup_token_expires_at: new Date(Date.now() + ttlMs).toISOString(),
  }
}

const writeText = vi.fn()

let container: HTMLDivElement
let root: Root

/** 面板渲染出的纯文本，用于断言用户实际能看到什么。 */
const visible = () => container.textContent ?? ''

function button(label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll('button'))
    .find((b) => (b.textContent ?? '').trim() === label)
  if (!found) throw new Error(`未找到按钮：${label}`)
  return found as HTMLButtonElement
}

function setInputValue(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

/** 走完真实交互：打开表单 → 填路径 → 点「生成连接命令」。 */
async function fillFormAndRegister() {
  await act(async () => { button('添加').click() })
  const input = container.querySelector<HTMLInputElement>('input[placeholder^="如 D:"]')!
  await act(async () => { setInputValue(input, 'D:\\项目\\销售资料') })
  await act(async () => { button('生成连接命令').click() })
}

/** 推进假时钟，让轮询/过期定时器触发，并冲刷随之产生的 Promise。 */
async function tick(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

beforeEach(() => {
  vi.useFakeTimers()
  writeText.mockReset().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  listMock.mockReset().mockResolvedValue([])
  registerMock.mockReset()
  revokeMock.mockReset().mockResolvedValue(undefined)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => { root.unmount() })
  container.remove()
  vi.useRealTimers()
})

async function mount() {
  await act(async () => { root.render(<LocalConnectionPanel />) })
}

describe('LocalConnectionPanel · 刷新竞态与一次性凭据', () => {
  it('撤销请求未完成时不能注册新授权，旧撤销不会清除后来生成的命令', async () => {
    listMock.mockResolvedValue([makeGrant()])
    registerMock.mockResolvedValue(makeRegisterResult())
    await mount()
    await fillFormAndRegister()
    expect(visible()).toContain(SETUP_COMMAND)

    await act(async () => { button('添加').click() })
    const input = container.querySelector<HTMLInputElement>('input[placeholder^="如 D:"]')!
    await act(async () => { setInputValue(input, 'D:\\项目\\合同') })
    const pendingRevoke = deferred<void>()
    revokeMock.mockReturnValueOnce(pendingRevoke.promise)
    await act(async () => { button('撤销').click() })

    expect(button('生成连接命令').disabled).toBe(true)
    expect(button('取消').disabled).toBe(true)
    await act(async () => { button('生成连接命令').click() })
    expect(registerMock).toHaveBeenCalledTimes(1)

    await act(async () => { pendingRevoke.resolve(); await pendingRevoke.promise })
    expect(visible()).not.toContain(SETUP_COMMAND)
    await act(async () => { button('生成连接命令').click() })
    expect(registerMock).toHaveBeenCalledTimes(2)
    expect(visible()).toContain(SETUP_COMMAND)
  })

  it('注册请求未完成时不能撤销任何授权', async () => {
    listMock.mockResolvedValue([makeGrant()])
    await mount()
    const pendingRegister = deferred<RegisterLocalPathResult>()
    registerMock.mockReturnValueOnce(pendingRegister.promise)
    await act(async () => { button('添加').click() })
    const input = container.querySelector<HTMLInputElement>('input[placeholder^="如 D:"]')!
    await act(async () => { setInputValue(input, 'D:\\项目\\合同') })
    await act(async () => { button('生成连接命令').click() })

    expect(button('取消').disabled).toBe(true)
    expect(button('撤销').disabled).toBe(true)
    await act(async () => { button('撤销').click() })
    expect(revokeMock).not.toHaveBeenCalled()

    await act(async () => { pendingRegister.resolve(makeRegisterResult()); await pendingRegister.promise })
    expect(visible()).toContain(SETUP_COMMAND)
  })

  it('注册发生在列表请求在途时：新授权与命令不会被旧列表抹掉', async () => {
    // 首次列表请求一直不返回；随后的请求返回含新授权的服务端真值。
    const inFlight = deferred<LocalPathGrant[]>()
    listMock.mockReturnValueOnce(inFlight.promise).mockResolvedValue([makeGrant()])
    await mount()

    registerMock.mockResolvedValueOnce(makeRegisterResult())
    await fillFormAndRegister()
    expect(visible()).toContain(SETUP_COMMAND)

    // 在途旧列表此刻才返回，且不含刚创建的授权。
    await act(async () => { inFlight.resolve([]); await inFlight.promise })
    expect(visible()).toContain('销售资料库')
    expect(visible()).toContain(SETUP_COMMAND)
  })

  it('撤销发生在列表请求在途时：已撤销的行不会被旧列表复活', async () => {
    listMock.mockResolvedValueOnce([makeGrant()])
    await mount()
    expect(visible()).toContain('待连接')

    // 轮询请求在途（迟迟不返回），撤销必须落在这个窗口内完成。
    const inFlight = deferred<LocalPathGrant[]>()
    listMock.mockReturnValueOnce(inFlight.promise).mockResolvedValue([])
    await tick(POLL_MS)
    await act(async () => { button('撤销').click() })
    expect(visible()).not.toContain('销售资料库')

    // 在途旧列表返回的仍是撤销前的 pending 行。
    await act(async () => { inFlight.resolve([makeGrant()]); await inFlight.promise })
    expect(visible()).not.toContain('销售资料库')
  })

  it.each(['connected', 'offline'] as const)(
    '授权变为 %s 后：命令从面板永久清除，旧列表也无法让它复活',
    async (status) => {
      listMock.mockResolvedValue([makeGrant()])
      registerMock.mockResolvedValueOnce(makeRegisterResult())
      await mount()
      await fillFormAndRegister()
      expect(visible()).toContain(SETUP_COMMAND)

      listMock.mockResolvedValue([makeGrant({ status })])
      await tick(POLL_MS)
      expect(visible()).not.toContain(SETUP_COMMAND)
      expect(visible()).toContain('一次性命令已失效')

      // 旧列表数据（仍是 pending）不得把凭据「复活」。
      listMock.mockResolvedValue([makeGrant()])
      await tick(POLL_MS)
      expect(visible()).not.toContain(SETUP_COMMAND)
    },
  )

  it('claimed=true 但状态仍是 pending：命令立即收回并显示「连接中」，陈旧列表不得让它复活', async () => {
    listMock.mockResolvedValue([makeGrant()])
    registerMock.mockResolvedValueOnce(makeRegisterResult())
    await mount()
    await fillFormAndRegister()
    expect(visible()).toContain(SETUP_COMMAND)
    expect(visible()).toContain('待连接')

    // 认领发生、但 mark_connected 还没到：这是 pending 无法区分的那个窗口。
    listMock.mockResolvedValue([makeGrant({ status: 'pending', claimed: true })])
    await tick(POLL_MS)
    expect(visible()).not.toContain(SETUP_COMMAND)
    expect(visible()).not.toContain('待连接')
    expect(visible()).toContain('连接中')
    expect(visible()).toContain('一次性命令已失效')
    expect(visible()).toContain('撤销此授权后重新「添加」')

    // 迟到的旧列表（pending 且没有 claimed 字段）不得把一次性命令「复活」。
    // 徽章回到「待连接」是跟随最新服务端数据的诚实呈现；凭据本身必须已从状态中清除。
    listMock.mockResolvedValue([makeGrant()])
    await tick(POLL_MS)
    expect(visible()).not.toContain(SETUP_COMMAND)
    expect(visible()).toContain('待连接')
    expect(writeText).not.toHaveBeenCalled()
  })

  it('已连接的授权也持续轮询：守护进程掉线后状态变为已离线', async () => {
    listMock.mockResolvedValueOnce([makeGrant({ status: 'connected' })])
    await mount()
    expect(visible()).toContain('已连接')

    listMock.mockResolvedValue([makeGrant({ status: 'offline' })])
    await tick(POLL_MS)
    expect(visible()).toContain('已离线')
  })

  it('轮询失败时保留上次状态但必须显式报错，授权行不被清空', async () => {
    listMock.mockResolvedValueOnce([makeGrant({ status: 'connected' })])
    await mount()
    listMock.mockRejectedValue(new Error('network down'))
    await tick(POLL_MS)
    expect(visible()).toContain('network down')
    expect(visible()).toContain('销售资料库')
  })

  it('令牌过期：独立定时器生效，过期命令不可复制', async () => {
    listMock.mockResolvedValue([makeGrant()])
    registerMock.mockResolvedValueOnce(makeRegisterResult(60_000))
    await mount()
    await fillFormAndRegister()
    expect(visible()).toContain(SETUP_COMMAND)

    // 轮询持续失败：没有任何服务端数据能把过期状态带回来。
    listMock.mockRejectedValue(new Error('network down'))
    await tick(POLL_MS)
    await tick(60_000)
    expect(visible()).not.toContain(SETUP_COMMAND)
    expect(visible()).toContain('已过期')
    expect(writeText).not.toHaveBeenCalled()
  })

  it('标签页休眠：定时器未跑但时钟已过期，复制按钮也不得写出过期令牌', async () => {
    listMock.mockResolvedValue([makeGrant()])
    registerMock.mockResolvedValueOnce(makeRegisterResult(60_000))
    await mount()
    await fillFormAndRegister()

    // 只推进系统时间、不推进定时器（等价于后台标签页定时器被节流/延迟）。
    await act(async () => { vi.setSystemTime(Date.now() + 120_000) })
    await act(async () => { button('复制').click() })

    expect(writeText).not.toHaveBeenCalled()
    expect(visible()).not.toContain(SETUP_COMMAND)
    expect(visible()).toContain('已过期')
  })

  it('再次注册一开始就清除上一条凭据，失败的新请求不会留下旧的失效命令', async () => {
    listMock.mockResolvedValue([makeGrant()])
    registerMock.mockResolvedValueOnce(makeRegisterResult())
    await mount()
    await fillFormAndRegister()
    expect(visible()).toContain(SETUP_COMMAND)

    const pendingRegister = deferred<RegisterLocalPathResult>()
    registerMock.mockReturnValueOnce(pendingRegister.promise)
    await act(async () => { button('添加').click() })
    const input = container.querySelector<HTMLInputElement>('input[placeholder^="如 D:"]')!
    await act(async () => { setInputValue(input, 'D:\\项目\\合同') })
    await act(async () => { button('生成连接命令').click() })
    // 新的注册请求还没回来，旧凭据已不可见、不可复制。
    expect(visible()).not.toContain(SETUP_COMMAND)
  })

  it('撤销成功后：命令与令牌立即清除并说明原因', async () => {
    listMock.mockResolvedValue([makeGrant()])
    registerMock.mockResolvedValueOnce(makeRegisterResult())
    await mount()
    await fillFormAndRegister()
    expect(visible()).toContain(SETUP_COMMAND)

    revokeMock.mockRejectedValueOnce(new Error('revoke failed'))
    await act(async () => { button('撤销').click() })
    expect(visible()).toContain('revoke failed')
    expect(visible()).toContain(SETUP_COMMAND)

    revokeMock.mockResolvedValueOnce(undefined)
    await act(async () => { button('撤销').click() })
    expect(visible()).not.toContain(SETUP_COMMAND)
    expect(visible()).toContain('一次性命令已失效')
  })

  it('剪贴板失败：显式报错且不谎报「已复制」', async () => {
    listMock.mockResolvedValue([makeGrant()])
    registerMock.mockResolvedValueOnce(makeRegisterResult())
    writeText.mockRejectedValueOnce(new Error('denied'))
    await mount()
    await fillFormAndRegister()
    await act(async () => { button('复制').click() })
    expect(visible()).toContain('复制失败')
    expect(visible()).not.toContain('已复制')
  })

  it('复制成功才提示「已复制」，并写入原始命令文本', async () => {
    listMock.mockResolvedValue([makeGrant()])
    registerMock.mockResolvedValueOnce(makeRegisterResult())
    await mount()
    await fillFormAndRegister()
    await act(async () => { button('复制').click() })
    expect(writeText).toHaveBeenCalledWith(SETUP_COMMAND)
    expect(visible()).toContain('已复制')
  })
})
