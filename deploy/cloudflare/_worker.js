// AutoTeams 纯反向代理 —— 部署到 Cloudflare Pages（Advanced mode `_worker.js`）
//
// 部署方式（两种任选）：
//   1. 在 Cloudflare 控制台创建 Pages 项目，选择 "Direct Upload" / "Upload assets"，
//      把本文件所在目录作为项目根上传（Advanced mode 会识别根下的 _worker.js）。
//   2. 或用 Wrangler CLI：
//        npx wrangler pages deploy <此目录> --project-name autoteams
//
// 功能：把 当前站点 的所有请求（HTTP / SSE 流 / WebSocket）转发到
// 云服务器，由服务器 nginx 完成内部反代（/api/ → backend、/collab-* → collab 等）。
//
// 注意：Cloudflare Worker 禁止向裸 IP 回源（Error 1003 "Direct IP access not allowed"），
// 回源目标必须是域名。故此处用 ngrok 固定域名作为内部回源通道（对外访问仍是 当前站点）。
// 服务器上须持续运行 ngrok 隧道（指向本机 nginx 80 端口）。
const BACKEND_ORIGIN = 'https://YOUR_NGROK_DOMAIN.ngrok-free.dev' // ngrok 内部回源通道（对外域名仍是 当前站点）

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url)

    // 本地守护进程安装资源（install.ps1 / local-runner.zip）直接从 Cloudflare Pages
    // 静态资源直出（env.ASSETS），不经过后端/ngrok 隧道。这样用户执行
    // `irm https://当前站点/local-runner/install.ps1 | iex` 时拿到的永远是
    // 真实的安装脚本，避免 ngrok 隧道抖动返回错误页导致 PowerShell 解析失败。
    if (url.pathname.startsWith('/local-runner/')) {
      const asset = await env.ASSETS.fetch(request)
      // env.ASSETS 对不存在的文件返回 404 时回退到代理，保证兼容性
      if (asset.status !== 404) {
        // 关键：必须返回文本 MIME，否则 PowerShell 的 irm|iex 会把 install.ps1
        // 当作 application/octet-stream 字节数组传入 iex，导致解析失败。
        let ct = 'application/octet-stream'
        if (url.pathname.endsWith('.ps1')) ct = 'text/plain; charset=utf-8'
        else if (url.pathname.endsWith('.zip')) ct = 'application/zip'
        return new Response(url.pathname.endsWith('.ps1') ? (await asset.text()).replaceAll('__AUTOTEAMS_DOMAIN__', url.origin) : asset.body, {
          status: asset.status,
          headers: {
            'Content-Type': ct,
            'Cache-Control': 'no-cache',
          },
        })
      }
    }

    const target = BACKEND_ORIGIN + url.pathname + url.search

    const isUpgrade = /websocket/i.test(request.headers.get('Upgrade') || '')

    const headers = new Headers(request.headers)
    headers.set('Host', url.host)                 // 当前站点
    headers.set('X-Forwarded-Host', url.host)
    headers.set('X-Forwarded-Proto', url.protocol === 'https:' ? 'https' : 'http')
    // 关键修复：ngrok 免费域会对浏览器 UA 且未带跳过头/跳过 cookie 的请求返回
    // 浏览器警告中间页（ERR_NGROK_6024 "You are about to visit..."），导致 API/SSE/WS
    // 及 `irm | iex` 拿到错误页而非真实内容。此处对 ngrok 回源显式声明跳过浏览器警告，
    // 保证 Worker 转发拿到的是后端真实响应。
    headers.set('ngrok-skip-browser-warning', 'true')

    if (isUpgrade) {
      return handleWebSocket(target, headers)
    }

    // 普通 HTTP / SSE 流：body 流式透传（不缓冲，保证 SSE 不断流）
    return fetch(target, {
      method: request.method,
      headers,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
      redirect: 'manual',
    })
  },
}

async function handleWebSocket(target, headers) {
  const [client, server] = Object.values(new WebSocketPair())
  server.binaryType = 'arraybuffer'
  server.accept({ allowHalfOpen: true })

  // 连到后端 WebSocket 服务（collaboration-service 的 /ws 与 /bridge）
  const upstream = await fetch(target, { headers, upgrade: 'websocket' })
  if (!upstream.webSocket) {
    // 后端未成功升级，返回后端原始响应
    return new Response(upstream.body, { status: upstream.status, headers: upstream.headers })
  }
  const backend = upstream.webSocket
  backend.binaryType = 'arraybuffer'
  backend.accept({ allowHalfOpen: true })

  // 双向转发消息
  server.addEventListener('message', (e) => backend.send(e.data))
  backend.addEventListener('message', (e) => server.send(e.data))

  // 一端关闭则同步另一端
  server.addEventListener('close', (e) => backend.close(e.code, e.reason))
  backend.addEventListener('close', (e) => server.close(e.code, e.reason))

  return new Response(null, { status: 101, webSocket: client })
}
