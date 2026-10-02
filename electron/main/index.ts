/**
 * AutoTeams 桌面端主进程入口。
 *
 * 安全姿态：
 * - contextIsolation 开启、nodeIntegration 关闭、sandbox 开启
 * - 导航守卫在**加载任何内容之前**安装（`loadFile` 之后再装，首跳重定向不受控）
 * - 只允许 http/https 交给系统浏览器，其余 scheme 一律拒绝
 *
 * AUD-28：渲染层不再用 `loadFile` 走 `file:`，改由主进程内的回环服务提供，
 * 详见 loopback-server.ts。
 */
import { app, BrowserWindow, ipcMain, dialog, shell, Notification } from 'electron'
import * as fs from 'node:fs'
import * as path from 'node:path'
import * as http from 'node:http'
import { LocalAgentBridge } from './agent-bridge.js'
import { classifyNavigation, isSafeExternalUrl, normalizeAllowedOrigins } from './navigation-policy.js'
import { resolveDesktopConfig, userConfigPath } from './desktop-config.js'
import { activeDesktopServer, ensureDesktopServer, shutdownDesktopServer } from './server-lifecycle.js'

/**
 * 开发模式加载前端 Vite 开发服务器。
 * 端口与 frontend/vite.config.ts 的 `server.port` 保持一致（3000），
 * 之前这里写死 5173，与前端实际端口不符，dev 时会白屏。
 * 需要换端口时用环境变量覆盖，不必改代码。
 */
const DEV_SERVER_URL = process.env.ELECTRON_DEV_SERVER_URL || 'http://localhost:3000'

/** 等待开发服务器就绪的上限（毫秒）。 */
const DEV_SERVER_WAIT_MS = 30_000

let mainWindow: BrowserWindow | null = null
const bridge = new LocalAgentBridge()

/**
 * 随包静态资源根目录。
 * 打包后由 electron-builder 放在 `resources/frontend`；
 * 未打包时回落到仓库内的 `frontend/dist`。
 */
function resolveRendererRoot(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'frontend')
  }
  // dist-electron/main → dist-electron → electron → 仓库根
  return path.resolve(__dirname, '..', '..', '..', 'frontend', 'dist')
}

/**
 * 探测开发服务器是否已经监听。
 * 主进程通常比 Vite 先起来，没有这一步就会打开一个「无法连接」错误页。
 */
async function waitForDevServer(url: string, timeoutMs: number): Promise<boolean> {
  const target = new URL(url)
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const reachable = await new Promise<boolean>((resolve) => {
      const req = http.get(
        { hostname: target.hostname, port: target.port, path: '/', timeout: 1000 },
        (res) => {
          res.resume()
          resolve(true)
        },
      )
      req.on('error', () => resolve(false))
      req.on('timeout', () => {
        req.destroy()
        resolve(false)
      })
    })
    if (reachable) return true
    await new Promise((resolve) => setTimeout(resolve, 300))
  }
  return false
}

/**
 * 交给系统浏览器的最后一道闸门。
 * `shell.openExternal` 会按 scheme 唤起系统处理器，file:、smb:、ms-msdt: 等都能
 * 触发本地程序，因此只放行 http/https。
 */
function openExternalSafely(url: string): void {
  if (!isSafeExternalUrl(url)) {
    console.warn(`[desktop] 拒绝用系统浏览器打开非 http(s) 链接：${url}`)
    return
  }
  void shell.openExternal(url)
}

/** 在加载任何内容之前安装导航守卫：内部 origin 放行，外部 http(s) 交给系统浏览器，其余拒绝。 */
function installNavigationGuards(window: BrowserWindow, allowedOrigins: string[]): void {
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (classifyNavigation(url, allowedOrigins) === 'external') {
      openExternalSafely(url)
    } else {
      console.warn(`[desktop] 拒绝在应用内打开链接：${url}`)
    }
    return { action: 'deny' }
  })

  window.webContents.on('will-navigate', (event, url) => {
    if (classifyNavigation(url, allowedOrigins) === 'internal') return
    event.preventDefault()
    if (classifyNavigation(url, allowedOrigins) === 'external') {
      openExternalSafely(url)
    } else {
      console.warn(`[desktop] 拒绝导航到非 http(s) 地址：${url}`)
    }
  })

  // 服务端 3xx 不会触发 will-navigate，必须单独拦：否则后端返回跨源 Location
  // 就能把应用窗口导航到外部站点（桌面端被当成跳板）。
  window.webContents.on('will-redirect', (event, url) => {
    if (classifyNavigation(url, allowedOrigins) === 'internal') return
    event.preventDefault()
    console.warn(`[desktop] 拒绝跟随重定向到非内部地址：${url}`)
  })
}

/**
 * 启动渲染层：开发时连 Vite，否则由回环服务提供随包产物。
 * 返回加载地址与允许的内部 origin。
 */
async function resolveRendererEntry(): Promise<{ url: string; allowedOrigins: string[] }> {
  // 未打包即视为开发态：先试 Vite（npm run dev 会一并拉起），打好的安装包一律走回环服务。
  if (!app.isPackaged) {
    const devServerReady = await waitForDevServer(DEV_SERVER_URL, DEV_SERVER_WAIT_MS)
    if (devServerReady) {
      return { url: DEV_SERVER_URL, allowedOrigins: normalizeAllowedOrigins([DEV_SERVER_URL]) }
    }
  }

  const rootDir = resolveRendererRoot()
  if (!fs.existsSync(path.join(rootDir, 'index.html'))) {
    throw new Error(
      `找不到前端构建产物：${path.join(rootDir, 'index.html')}\n` +
        '请先在 frontend 目录执行 npm run build（或使用 npm run dev 连接 Vite 开发服务器）。',
    )
  }

  const config = resolveDesktopConfig({ env: process.env, userConfigPath: userConfigPath(app.getPath('userData')) })
  // 单例：macOS 关窗再打开会再次走这里，必须复用同一个服务，origin 才不会变。
  const server = await ensureDesktopServer({
    rootDir,
    api: config.api,
    collab: config.collab,
    runtimeConfig: (origin) => ({
      // 强制同源：即使构建时内联了绝对 VITE_API_BASE_URL，桌面端也一律走回环代理，
      // 保证 Cookie/CSRF 仍然是同源语义（不去掉 Secure，也不放宽后端 CORS）。
      apiBaseUrl: '/api/v1',
      collabWsUrl: `${origin.replace(/^http/, 'ws')}/collab-ws`,
    }),
  })
  console.log(
    `[desktop] 渲染层回环服务 ${server.origin}（后端 ${config.api.origin} 来源 ${config.apiSource}，` +
      `协作服务 ${config.collab.origin} 来源 ${config.collabSource}，静态资源 ${rootDir}）`,
  )
  return { url: `${server.origin}/`, allowedOrigins: normalizeAllowedOrigins([server.origin]) }
}

async function createWindow(): Promise<void> {
  let entry: { url: string; allowedOrigins: string[] }
  try {
    entry = await resolveRendererEntry()
  } catch (error) {
    dialog.showErrorBox('AutoTeams 桌面端启动失败', (error as Error).message)
    app.quit()
    return
  }

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 1024,
    minHeight: 700,
    title: 'AutoTeams — 中小企业 AI 数字员工工作台',
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  // 守卫必须先于加载安装：否则首跳重定向可以在守卫生效前把窗口导航到外部站点。
  installNavigationGuards(mainWindow, entry.allowedOrigins)

  await mainWindow.loadURL(entry.url)
  if (!app.isPackaged) {
    mainWindow.webContents.openDevTools()
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// 注册 IPC 处理程序
ipcMain.handle('agent-bridge:detect', async () => {
  return bridge.detectAgents()
})

ipcMain.handle('agent-bridge:connect', async (_event, { agentId, options }) => {
  return bridge.connectAgent(agentId, options)
})

ipcMain.handle('agent-bridge:prompt', async (_event, { agentId, prompt }) => {
  return bridge.dispatchTask(agentId, prompt)
})

ipcMain.handle('agent-bridge:disconnect', async (_event, { agentId }) => {
  return bridge.disconnectAgent(agentId)
})

ipcMain.on('window:minimize-tray', () => {
  mainWindow?.hide()
})

ipcMain.on('app:notification', (_event, { title, body }: { title: string; body: string }) => {
  if (Notification.isSupported()) {
    new Notification({ title, body }).show()
  }
})

ipcMain.handle('fs:open-dialog', async () => {
  if (!mainWindow) return []
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: '业务文档与表格', extensions: ['pdf', 'docx', 'xlsx', 'csv', 'txt', 'md'] },
    ],
  })
  return result.filePaths
})

app.whenReady().then(() => {
  void createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void createWindow()
    }
  })
})


app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// 退出前关闭回环服务：断开存量连接并释放监听，避免端口残留。
// 只拦一次，关闭完成后用 app.exit() 收尾，不会再次触发 before-quit 形成循环。
let shuttingDown = false
app.on('before-quit', (event) => {
  if (shuttingDown || activeDesktopServer() === null) return
  shuttingDown = true
  event.preventDefault()
  void shutdownDesktopServer().finally(() => app.exit(0))
})
