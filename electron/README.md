# AutoTeams 桌面端（Electron）

AutoTeams 的桌面外壳：主进程负责窗口生命周期、本地 Agent 调度桥接，以及**把随包前端产物
通过一个只绑定 127.0.0.1 的本地服务提供给渲染层**；预加载脚本以 `contextBridge`
白名单方式向前端暴露受控能力。渲染层复用仓库内的 Web 前端（`frontend/`），
本目录**不包含**独立的渲染层源码。

> **渲染层为什么不用 `loadFile`（AUD-28）**
>
> Vite 默认 `base='/'`，产物 `index.html` 里写的是 `src="/assets/index-*.js"`。
> 在 `file:` 协议下这会按本地文件规则解析到**盘符根目录**（`E:/assets/index-*.js`），
> 必然 404；`/config.json`、`/api/v1` 这类同源相对请求在 `file:` 下也没有同源语义，
> BrowserRouter 刷新也没有服务端回退。
>
> 因此桌面端改为：主进程启动一个只监听 `127.0.0.1` 的静态 + 代理服务，
> 渲染层用 `loadURL(http://127.0.0.1:<port>/)` 加载。**Web 部署（nginx / Docker /
> Vercel）与 `frontend/` 一行未改**，开发时仍然连 Vite。

> **当前状态（务必先读）**
>
> - 可独立构建：`npm ci` → `npm run build` → `npm run package`。
> - 渲染层内容来自 `../frontend`。**打包前必须先构建前端**（`cd ../frontend && npm run build`），
>   否则 `electron-builder` 的 `extraResources` 会因源目录缺失而失败。
> - 主进程逻辑有单测覆盖：`npm test`（node:test，真实套接字，不 mock http）。
> - `main/agent-bridge.ts` 会**真实派生** Codex CLI / Claude Code 子进程执行任务
>   （参数数组、不经 shell、净化环境变量、120s 超时与进程回收）。找不到可执行文件、
>   退出码非零或超时时返回 `ok: false` 并附原因，**不伪造成功回执**。
>   stdio 双向流式协议（增量回填、进程复用）尚未实现。
> - **签名与自动更新未接入**（`publish: null`），产出的安装包未签名。
> - **未在真实 Electron 运行时 + 真实后端上验收过登录**：`npm test` 全部基于
>   HTTP 层的真实请求/响应，`Secure` Cookie 在真实 Chromium 中的落地行为、
>   安装包安装后的启动，都还没有真机证据。见 `docs/AUDIT_OMP_DESKTOP_2026-09-29.md`。

## 目录结构

| 路径 | 作用 | 构建产物 |
|---|---|---|
| `main/index.ts` | 主进程：窗口、IPC、导航守卫 | `dist-electron/main/index.js` |
| `main/loopback-server.ts` | 回环静态 + 固定上游代理 + WebSocket 透传 | `dist-electron/main/loopback-server.js` |
| `main/static-assets.ts` | 随包资源路径解析（穿越/编码/符号链接） | `dist-electron/main/static-assets.js` |
| `main/navigation-policy.ts` | 导航与 `shell.openExternal` 策略 | `dist-electron/main/navigation-policy.js` |
| `main/desktop-config.ts` | 运行时后端地址解析与校验 | `dist-electron/main/desktop-config.js` |
| `main/server-lifecycle.ts` | 回环服务单例与关闭 | `dist-electron/main/server-lifecycle.js` |
| `main/agent-bridge.ts` | 本地外部 Agent 子进程桥接 | `dist-electron/main/agent-bridge.js` |
| `preload/index.ts` | `contextBridge` 白名单 API | `dist-electron/preload/index.js` |
| `main/tests/*.test.ts` | node:test 用例（不进安装包） | `dist-test/` |
| `tsconfig.json` | 类型检查（`noEmit`），不参与产物 | — |
| `tsconfig.build.json` | 产物构建（CommonJS → `dist-electron/`） | — |
| `tsconfig.test.json` | 测试构建（→ `dist-test/`） | — |

主进程与预加载脚本都用 `tsc` 直接产出 CommonJS，不用 Vite 打包：Electron 主进程是
Node 产物，走 Vite 的 browser 目标会把 `node:*` 内建模块 externalize 掉，构建会失败。

## 前置条件

- Node.js ≥ 18、npm ≥ 9
- 前端开发服务器：仓库根 `frontend/vite.config.ts` 中 `server.port = 3000`。
  桌面端开发模式连接的正是这个端口（可用 `ELECTRON_DEV_SERVER_URL` 覆盖）。

## 安装

```bash
cd electron
npm ci        # 严格按 package-lock.json 安装；CI 与干净机器请用这个
```

`npm ci` 会下载 Electron 运行时二进制（约 100MB）。无外网时可设置
`ELECTRON_SKIP_BINARY_DOWNLOAD=1` 只装 JS 依赖——此时**无法运行** `npm start`，
但 `npm run typecheck`、`npm run build`、`npm test` 仍可用。

## 运行时后端地址

优先级：**用户配置文件 > 环境变量 > 本机默认**。配置非法时**拒绝启动并弹窗说明原因**，
不会静默回落到别的后端（那会让用户以为连的是自己的服务器，实际打去了别处）。

| 字段 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `apiUrl` | `AUTOTEAMS_API_URL` | `http://127.0.0.1:8000` | 后端 origin，可带路径前缀 |
| `collabUrl` | `AUTOTEAMS_COLLAB_URL` | `http://127.0.0.1:3001` | 协作服务 origin |

用户配置文件位置：`<userData>/desktop-config.json`（Windows 为
`%APPDATA%\AutoTeams\desktop-config.json`），例如：

```json
{ "apiUrl": "https://api.example.com", "collabUrl": "https://collab.example.com" }
```

只接受 `http/https`，禁止内嵌凭据、query、fragment；`apiUrl`/`collabUrl` 字段一旦出现
就必须是可用的非空字符串（写错会直接报错，不会退回默认）。桌面端启动日志会打印
实际使用的上游与来源（`user-file` / `env` / `default`）。

## 开发

```bash
# 终端 1：前端开发服务器（必须先起，桌面端 dev 会等它）
cd ../frontend && npm run dev      # http://localhost:3000

# 终端 2：桌面端
cd electron
npm run dev
```

`npm run dev` = 先做一次完整 `build`，然后并行跑「main 监听构建 / 启动 Electron」。
Vite 30s 内没起来时，回落到用回环服务加载 `../frontend/dist`（而不是打开连接失败页）。

## 构建与测试

```bash
npm run typecheck   # tsc --noEmit，校验 main / preload / tests
npm run build       # typecheck + 构建 main 与 preload 的 CommonJS 产物
npm test            # 构建 dist-test 并运行 node:test
```

`npm test` 覆盖：随包资源解析、深链回退、Cookie/CSRF/Set-Cookie 往返、SSE 不缓冲、
开放代理反例、路径穿越、导航与 `shell.openExternal` 策略、上游地址校验、
服务单例与关闭、端口冲突、上游失败。存在 `frontend/dist` 时还会用**真实构建产物**
逐个校验 `index.html` 引用的绝对资源能被解析。

## 打包

```bash
cd ../frontend && npm run build    # 渲染层产物必须先存在
cd ../electron
npm run package                   # 按当前平台生成安装包，输出到 release/
npm run package:dir               # 只解包目录（不生成安装器），用于快速验证
```

`extraResources` 把 `../frontend/dist` 复制到 `resources/frontend`，主进程打包后从
`process.resourcesPath/frontend` 提供静态服务。

## 安全基线

渲染层服务（`main/loopback-server.ts`）是唯一入口，以下判定都在这里收口：

- 只允许绑定 `127.0.0.1`（传入其他地址直接抛错）
- `Host` 必须精确等于本服务（防 DNS rebinding 打到本机）
- 浏览器来源头 `Sec-Fetch-Site` 只接受 `same-origin` / `none`
- 状态变更方法必须携带与本服务同源的 `Origin`
- 代理上游在启动时固定：absolute-form 请求行 400，客户端无法用请求行/Host/查询参数
  指定别的上游；`/api`、`/collab-api`、`/collab-ws` 之外的路径不参与代理
- `Connection` 头点名的逐跳首部一并剔除（RFC 7230）
- 上游 3xx 只允许同源 `Location`，跨源重定向一律 502
- WebSocket 只升级固定的 `/collab-ws`，且校验 `Host` 与 `Origin`
- 静态资源：拒绝 `..`、编码/双重编码穿越、NUL、反斜杠、Windows 盘符段，
  并用 `realpath` 拦住指向包外的符号链接

主进程窗口：`contextIsolation` 开启、`nodeIntegration` 关闭、`sandbox` 开启；
`setWindowOpenHandler`、`will-navigate`、`will-redirect` 三个守卫在**加载任何内容之前**
安装；`shell.openExternal` 只接受无 userinfo 的 `http/https`。

**不作为安全手段做的事**：不关闭 `webSecurity`、不放宽后端 CORS 或 CSRF、
不在代理层改写或剥离 `Set-Cookie` 的 `Secure`/`SameSite`/`Domain`。
Cookie 属性由后端配置决定，桌面端只做原样透传。
