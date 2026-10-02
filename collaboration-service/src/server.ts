/**
 * 协作服务 — Express HTTP + WebSocket 服务器
 *
 * 端点：
 * - POST   /api/sessions          创建会话
 * - GET    /api/sessions          列出会话
 * - GET    /api/sessions/:id      获取会话详情
 * - DELETE /api/sessions/:id      销毁会话
 * - GET    /api/sessions/:id/messages  获取消息历史
 * - GET    /api/local/status      本地 Runner 连接状态
 * - WS     /bridge                本地守护进程（Runner）外连端点
 * - WS     /ws                    WebSocket 双向通信（prompt/abort/streaming）
 * - GET    /api/health            健康检查
 */
import { loadSandboxBashPolicy } from './sandbox-tools.js'
import dotenv from 'dotenv'
import express from 'express'
import type { NextFunction, Request, Response } from 'express'
import cors from 'cors'
import { WebSocketServer, WebSocket } from 'ws'
import { createServer } from 'http'
import { fileURLToPath } from 'url'
import { dirname, join } from 'path'
// 统一使用项目根目录的 .env（单文件配置），避免各服务各自维护 .env
dotenv.config({ path: join(dirname(fileURLToPath(import.meta.url)), '..', '..', '.env') })
import {
  createSession,
  prompt,
  abort,
  getMessages,
  listSessions,
  getSession,
  disposeSession,
  ownsSession,
  getSessionCwd,
} from './session-manager.js'
import { authenticate, authorizeAgent, isAllowedOrigin, validateCsrf } from './auth.js'
import { fetchAgentKnowledge, writeAttachmentToKnowledge } from './knowledge-loader.js'
import {
  configureRunnerSession,
  dispatchTask,
  handleRunnerConnection,
  isRunnerReady,
  runnerCount,
  onlineCount,
} from './runner-session.js'
import type { ClientMessage, ServerMessage } from './types.js'
import { markActiveSessionsInterrupted } from './session-store.js'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const PORT = parseInt(process.env.COLLAB_PORT || process.env.PORT || '3001', 10)
const app = express()

const LOCAL_FILE_TOOLS = new Set(['list', 'read', 'write', 'delete'])

type LocalFileTool = 'list' | 'read' | 'write' | 'delete'

function isLocalFileTool(tool: unknown): tool is LocalFileTool {
  return typeof tool === 'string' && LOCAL_FILE_TOOLS.has(tool)
}

// ============================================================
// 本地工具桥接：后端内部端点回调
// ============================================================
const backendApiUrl = (process.env.AUTOTEAMS_BACKEND_URL || process.env.AUTOFDE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const bridgeSecret = process.env.BRIDGE_INTERNAL_SECRET || ''

/** 已连接的前端 WebSocket 客户端（用于广播 runner_status 等全局事件） */
const wsClients = new Set<WebSocket>()

/** 带桥接密钥的内部请求（Backend ↔ 协作服务互相鉴权） */
async function backendInternalRequest(path: string, body: unknown): Promise<unknown> {
  const response = await fetch(`${backendApiUrl}/api/v1${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(bridgeSecret ? { 'X-Bridge-Secret': bridgeSecret } : {}),
    },
    body: JSON.stringify(body),
  })
  const payload = (await response.json().catch(() => ({}))) as { data?: unknown; error?: string; detail?: string }
  if (!response.ok) {
    throw new Error(payload.error || payload.detail || `内部请求失败 (${response.status})`)
  }
  return payload.data
}

/** 以当前用户身份读取一条本地路径授权（用于校验本地模式会话的 grant 归属/在线状态） */
async function fetchLocalGrant(
  cookie: string | undefined,
  grantId: string,
): Promise<{ user_id: string; scope: string; status: string; local_path: string } | null> {
  try {
    const response = await fetch(
      `${backendApiUrl}/api/v1/local-paths/${encodeURIComponent(grantId)}`,
      { headers: cookie ? { Cookie: cookie } : {} },
    )
    if (!response.ok) return null
    const body = (await response.json()) as {
      data?: { user_id?: string; scope?: string; status?: string; local_path?: string }
    }
    const data = body.data
    if (!data) return null
    return {
      user_id: String(data.user_id ?? ''),
      scope: data.scope || 'read',
      status: data.status || 'pending',
      local_path: data.local_path || '',
    }
  } catch {
    return null
  }
}

// 配置 Runner 会话服务的中后端回调
configureRunnerSession({
  onClaim: async (grantId, setupToken) => {
    const data = (await backendInternalRequest(`/local-paths/${encodeURIComponent(grantId)}/claim`, {
      setup_token: setupToken,
    })) as {
      grant_id: string
      scope: string
      local_path: string
      enterprise_id: string
      user_id: string
    }
    if (!data) throw new Error('认领失败')
    return {
      grantId: data.grant_id,
      scope: data.scope,
      localPath: data.local_path,
      enterpriseId: data.enterprise_id,
      userId: data.user_id,
    }
  },
  onReady: async (grantId, runnerId, resolvedPath, toolManifest) => {
    await backendInternalRequest(`/local-paths/${encodeURIComponent(grantId)}/connected`, {
      runner_id: runnerId,
      resolved_path: resolvedPath,
      tool_manifest: toolManifest,
    })
  },
  onOffline: async (grantId) => {
    await backendInternalRequest(`/local-paths/${encodeURIComponent(grantId)}/offline`, {})
  },
  // 在线 Runner 数变化时向前端广播 runner_status（仅就绪的 Runner 计入在线数）
  onStatusChange: (online) => {
    const msg: ServerMessage = { type: 'runner_status', online }
    const payload = JSON.stringify(msg)
    for (const client of wsClients) {
      if (client.readyState === WebSocket.OPEN) {
        try {
          client.send(payload)
        } catch {
          // 单条发送失败不影响其余客户端
        }
      }
    }
  },
})

app.use(cors({ origin: (origin, callback) => callback(null, !origin || isAllowedOrigin(origin)), credentials: true }))
app.use(express.json({ limit: '10mb' }))

app.use('/api/sessions', async (req, res, next) => {
  try {
    const user = await authenticate(req)
    if (['POST', 'DELETE'].includes(req.method) && !validateCsrf(req)) {
      res.status(403).json({ error: 'CSRF token missing or invalid' })
      return
    }
    res.locals.user = user
    next()
  } catch {
    res.status(401).json({ error: '认证已失效' })
  }
})

// ============================================================
// HTTP 端点
// ============================================================


/**
 * Express 4 不会捕获 async handler 抛出的 rejection（请求会一直挂起）。
 * 状态存储在损坏/不可读时会抛出明确错误，这里统一转交末尾的错误中间件。
 */
function asyncRoute(handler: (req: Request, res: Response) => Promise<unknown>) {
  return (req: Request, res: Response, next: NextFunction): void => {
    handler(req, res).catch(next)
  }
}

/** 健康检查 */
app.get('/api/health', (_req, res) => {
  res.json({ status: 'ok', service: 'autoteams-collaboration', port: PORT })
})

/** 创建会话 */
app.post('/api/sessions', async (req, res) => {
  try {
    const { agentId, positionLabel, mode, grantId, localScope } = req.body
    const user = res.locals.user
    if (!agentId || typeof agentId !== 'string') {
      res.status(400).json({ error: '缺少必需字段: agentId' })
      return
    }

    const isLocal = mode === 'local'
    let resolvedScope: string | undefined
    let resolvedGrantId: string | null = null
    let resolvedLocalPath: string | undefined
    if (isLocal) {
      // 本地模式：必须绑定一个属于当前用户且处于 connected 状态的授权。
      if (!grantId || typeof grantId !== 'string') {
        res.status(400).json({ error: '本地模式必须指定 grantId' })
        return
      }
      const grant = await fetchLocalGrant(req.headers.cookie, grantId)
      if (!grant) {
        res.status(404).json({ error: '授权不存在或无权访问' })
        return
      }
      if (grant.user_id !== user.id) {
        res.status(403).json({ error: '无权使用该授权创建会话' })
        return
      }
      if (grant.status !== 'connected') {
        res.status(409).json({ error: '本地守护进程未在线，请先连接后再创建本地会话' })
        return
      }
      resolvedGrantId = grantId
      resolvedScope = grant.scope
      resolvedLocalPath = grant.local_path
    }

    const agent = await authorizeAgent(req, agentId)
    // 拉取该 AI 员工的企业知识文档，物化到会话沙箱（后端无知识时服务端回退内置种子）
    const knowledgeFiles = await fetchAgentKnowledge(req.headers.cookie, agent.id)
    const session = await createSession(
      agent.id,
      agent.name,
      positionLabel || '',
      agent.systemPrompt,
      user.id,
      user.enterpriseId,
      knowledgeFiles,
      {
        mode: isLocal ? 'local' : 'sandbox',
        grantId: resolvedGrantId ?? undefined,
        localScope: resolvedScope,
        localPath: resolvedLocalPath,
      },
    )
    res.json(session)
  } catch (err) {
    console.error('[创建会话失败]', err)
    if (err instanceof Error && (err.message === '无权访问该资源' || err.message === '认证已失效')) {
      res.status(403).json({ error: err.message })
      return
    }
    res.status(500).json({
      error: err instanceof Error ? err.message : '创建会话失败',
    })
  }
})

/** 列出会话 */
app.get('/api/sessions', asyncRoute(async (_req, res) => {
  res.json(await listSessions(res.locals.user.id))
}))

/** 获取会话详情 */
app.get('/api/sessions/:id', asyncRoute(async (req, res) => {
  const session = await getSession(req.params.id, res.locals.user.id)

  if (!session) {
    res.status(404).json({ error: '会话不存在' })
    return
  }
  res.json(session)
}))

/** 销毁会话 */
app.delete('/api/sessions/:id', asyncRoute(async (req, res) => {
  const ok = await disposeSession(req.params.id, res.locals.user.id)

  if (!ok) {
    res.status(404).json({ error: '会话不存在' })
    return
  }
  res.json({ success: true })
}))

/** 获取消息历史 */
app.get('/api/sessions/:id/messages', asyncRoute(async (req, res) => {
  const messages = await getMessages(req.params.id, res.locals.user.id)

  if (messages === null) {
    res.status(404).json({ error: '会话不存在' })
    return
  }
  res.json(messages)
}))

/**
 * 驱动本地守护进程执行任务（Backend → 协作服务 → 本地 Runner）。
 *
 * 由后端 /api/v1/local-paths/{id}/run 使用内部密钥（X-Bridge-Secret）调用，
 * 协作服务找到已就绪的 Runner，经 /bridge WebSocket 下发任务并等待结果。
 */
app.post('/api/local/run', async (req, res) => {
  try {
    // service-to-service 鉴权：必须携带匹配的桥接密钥（失败关闭，不允许空密钥放行）。
    // 显式判断密钥为空也拒绝，避免「未配置密钥 + 空值头」时 `'' !== ''` 放行的边界绕过。
    if (!bridgeSecret || req.headers['x-bridge-secret'] !== bridgeSecret) {
      res.status(403).json({ error: '内部鉴权失败' })
      return
    }
        const { grantId, tool, path, content } = req.body || {}
    if (!grantId || !isLocalFileTool(tool)) {
      res.status(400).json({ error: '仅支持受限文件操作: grantId / list|read|write|delete' })
      return
    }

    if (!isRunnerReady(grantId)) {
      res.status(409).json({ error: '本地守护进程不在线' })
      return
    }
        const result = await dispatchTask(grantId, {
      tool,
      path: typeof path === 'string' ? path : '',
      content: typeof content === 'string' ? content : null,
    })

    res.json({ success: true, data: result })
  } catch (err) {
    console.error('[本地任务执行失败]', err)
    res.status(400).json({ error: err instanceof Error ? err.message : '本地任务执行失败' })
  }
})

/**
 * 本地 Runner 连接状态（诊断/前端轮询用）。
 *
 * P2-9 安全修复：改为要求登录会话（复用 /api/sessions 的鉴权中间件），
 * 避免匿名探测在线 Runner 数量（侦察价值）。前端轮询本就携带登录 Cookie。
 */
app.get('/api/local/status', async (req, res) => {
  try {
    await authenticate(req)
  } catch {
    res.status(401).json({ error: '认证已失效' })
    return
  }
  res.json({ online: onlineCount(), connected: runnerCount() })
})

/**
 * 上传附件到会话沙箱知识目录（纳入 AI 员工的检索上下文）。
 *
 * 请求体为原始二进制（application/octet-stream），文件名通过 `?filename=` 查询参数传递，
 * 避免 multipart 序列化带来的兼容与依赖问题。服务端将文件写入
 * {会话沙箱}/knowledge/ 目录，供该 AI 员工在后续回答中检索引用。
 */
app.post(
  '/api/sessions/:id/attachments',
  express.raw({ type: 'application/octet-stream', limit: '20mb' }),
  (req, res) => {
    try {
      const user = res.locals.user
      const cwd = getSessionCwd(req.params.id, user.id)
      if (!cwd) {
        res.status(404).json({ error: '会话不存在' })
        return
      }
      const filename = String(req.query.filename || req.headers['x-filename'] || 'attachment.txt')
      const data = Buffer.isBuffer(req.body) ? req.body : Buffer.from(req.body || '')
      if (data.length === 0) {
        res.status(400).json({ error: '文件内容为空' })
        return
      }
      const target = writeAttachmentToKnowledge(cwd, filename, data)
      if (!target) {
        res.status(400).json({ error: '文件名不合法或写入失败' })
        return
      }
      console.log(`[附件] 会话 ${req.params.id} 已收纳附件 → ${target}`)
      res.json({ success: true, name: filename, path: target })
    } catch (err) {
      console.error('[上传附件失败]', err)
      res.status(500).json({ error: err instanceof Error ? err.message : '上传附件失败' })
    }
  },
)

// 兜底错误处理：把 async handler / 状态存储的明确错误转成 500 + 可读消息，
// 而不是留下一个挂起的请求或进程级 unhandled rejection。
app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  console.error('[请求处理失败]', err)
  if (res.headersSent) return
  res.status(500).json({ error: err instanceof Error ? err.message : '内部错误' })
})

// ============================================================
// WebSocket 服务器
// ============================================================

const server = createServer(app)
// 同一 HTTP server 上挂载多个 WebSocket 端点时，ws 库要求使用 noServer 模式，
// 并在 server 的 'upgrade' 事件里按 pathname 手动路由；否则先注册的 server 会
// 把不匹配自身 path 的升级请求直接以 400 拒绝，导致 /bridge 无法生效。
const wss = new WebSocketServer({ noServer: true })
const bridgeWss = new WebSocketServer({ noServer: true })

server.on('upgrade', (request, socket, head) => {
  const pathname = new URL(request.url || '', 'http://localhost').pathname
  if (pathname === '/bridge') {
    bridgeWss.handleUpgrade(request, socket, head, (ws) => {
      bridgeWss.emit('connection', ws, request)
    })
  } else if (pathname === '/ws') {
    wss.handleUpgrade(request, socket, head, (ws) => {
      wss.emit('connection', ws, request)
    })
  } else {
    socket.destroy()
  }
})

// ============================================================
// 本地工具桥接：/bridge WebSocket（本地 Runner 主动外连）
// ============================================================
//
// 本地守护进程（Local Runner）作为 WS 客户端主动连入 /bridge，携带
// ?grant=<grantId>&token=<setupToken> 完成一次性认领后，即可接收云端下发的
// 本地任务（list/read/write/delete/cli）。这样无需内网隧道即可桥接云端与本地。
bridgeWss.on('connection', (ws, request) => {
  const url = new URL(request.url || '', 'http://localhost')
  const grantId = url.searchParams.get('grant') || ''
  const setupToken = url.searchParams.get('token') || ''
  const requestedOrigin = request.headers.origin
  // Runner 是命令行程序，不携带 Origin；若携带则校验（浏览器场景下防御）
  if (requestedOrigin && !isAllowedOrigin(requestedOrigin)) {
    ws.close(1008, '来源不受信任')
    return
  }
  handleRunnerConnection(ws, grantId, setupToken)
})

 wss.on('connection', async (ws: WebSocket, request) => {
  if (!isAllowedOrigin(request.headers.origin)) {
    ws.close(1008, '来源不受信任')
    return
  }
  let user
  try {
    user = await authenticate(request)
  } catch {
    ws.close(1008, '认证已失效')
    return
  }
  const clientId = Math.random().toString(36).substring(2, 10)
  console.log(`[WebSocket] 客户端已连接: ${clientId}`)

  // 加入广播集合（接收 runner_status 等全局事件）
  wsClients.add(ws)

  // 连接建立后主动推送一次当前在线 Runner 数，避免前端等待事件
  const statusMsg: ServerMessage = { type: 'runner_status', online: onlineCount() }
  ws.send(JSON.stringify(statusMsg))

  // 发送连接确认
  const connectedMsg: ServerMessage = { type: 'connected', clientId }
  ws.send(JSON.stringify(connectedMsg))

  ws.on('message', async (data: Buffer) => {
    let msg: ClientMessage
    try {
      msg = JSON.parse(data.toString())
    } catch {
      const errMsg: ServerMessage = { type: 'error', message: '无效的 JSON 消息' }
      ws.send(JSON.stringify(errMsg))
      return
    }

    switch (msg.type) {
      case 'ping': {
        const pong: ServerMessage = { type: 'pong' }
        ws.send(JSON.stringify(pong))
        break
      }

      case 'prompt': {
        const { sessionId, message } = msg
        if (!ownsSession(sessionId, user.id) || typeof message !== 'string' || !message.trim()) {
          const errMsg: ServerMessage = { type: 'error', sessionId, message: '无权访问该会话或消息无效' }
          ws.send(JSON.stringify(errMsg))
          break
        }
        // 发送 prompt 并流式返回事件
        await prompt(sessionId, message, {
          onAgentStart: () => {
            const m: ServerMessage = { type: 'agent_start', sessionId }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onTextDelta: (delta) => {
            const m: ServerMessage = { type: 'text_delta', sessionId, delta }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onMessageEnd: () => {
            const m: ServerMessage = { type: 'message_end', sessionId }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onAgentEnd: () => {
            const m: ServerMessage = { type: 'agent_end', sessionId }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onError: (error) => {
            const m: ServerMessage = { type: 'error', sessionId, message: error }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onReasoningStart: (turnIndex) => {
            const m: ServerMessage = { type: 'reasoning_start', sessionId, turnIndex }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onReasoningEnd: (turnIndex) => {
            const m: ServerMessage = { type: 'reasoning_end', sessionId, turnIndex }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onToolStart: (toolName, args, toolCallId) => {
            const m: ServerMessage = { type: 'tool_start', sessionId, toolName, args, toolCallId }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onToolUpdate: (toolName, partialResult, toolCallId) => {
            const m: ServerMessage = { type: 'tool_update', sessionId, toolName, partialResult, toolCallId }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onToolEnd: (toolName, result, isError, toolCallId) => {
            const m: ServerMessage = { type: 'tool_end', sessionId, toolName, result, isError, toolCallId }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
          onArtifact: (path, name, content) => {
            const m: ServerMessage = { type: 'artifact', sessionId, path, name, content }
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m))
          },
        })
        break
      }

      case 'abort': {
        if (!ownsSession(msg.sessionId, user.id)) break
        await abort(msg.sessionId)
        break
      }
    }
  })

  ws.on('close', () => {
    wsClients.delete(ws)
    console.log(`[WebSocket] 客户端已断开: ${clientId}`)
  })

  ws.on('error', (err) => {
    console.error(`[WebSocket] 客户端错误 ${clientId}:`, err.message)
  })
})

// ============================================================
// 启动服务
// ============================================================

async function startServer(): Promise<void> {
  // Reject unsafe production command opt-ins before accepting requests.
  loadSandboxBashPolicy()
  // pi.dev 的运行时上下文无法跨进程安全恢复；把上次仍 active 的会话显式标记为
  // interrupted，保留历史而不伪造一个可继续执行的 AgentSession。
  await markActiveSessionsInterrupted()
  server.listen(PORT, () => {
    console.log(`╔══════════════════════════════════════════════════╗`)
    console.log(`║  AutoTeams 协作服务已启动                          ║`)
    console.log(`║  HTTP:   http://localhost:${PORT}                  ║`)
    console.log(`║  WebSocket: ws://localhost:${PORT}/ws              ║`)
    console.log(`║  Bridge:  ws://localhost:${PORT}/bridge            ║`)
    console.log(`║  LLM: AgnesAI (${process.env.AGNES_TEXT_MODEL || 'agnes-2.5-flash'})  ║`)
    console.log(`╚══════════════════════════════════════════════════╝`)
  })
}

void startServer().catch((error) => {
  console.error('[协作服务启动失败]', error)
  process.exitCode = 1
})
