/**
 * 协作工作台 API 客户端 — 对接 collaboration-service (Node.js pi.dev SDK)
 *
 * 端点（通过 Vite 代理 /collab-api → localhost:3001）：
 * - POST   /collab-api/sessions          创建协作会话
 * - GET    /collab-api/sessions          列出协作会话
 * - GET    /collab-api/sessions/:id      获取会话详情
 * - DELETE /collab-api/sessions/:id      销毁会话
 * - GET    /collab-api/sessions/:id/messages  获取消息历史
 * - GET    /collab-api/health            健康检查
 *
 * WebSocket（通过 Vite 代理 /collab-ws → ws://localhost:3001/ws）：
 * 见 hooks/useCollaborationWs.ts
 */
import axios from 'axios'

// ============================================================
// 类型定义（与 collaboration-service/src/types.ts 对齐）
// ============================================================

export interface CollabSession {
  sessionId: string
  agentId: string
  agentName: string
  positionLabel: string
  createdAt: string
  lastActiveAt: string
  messageCount: number
  lastMessagePreview: string
  /** 执行模式：sandbox=云端沙箱（默认） / local=本地 Runner */
  mode?: 'sandbox' | 'local'
  /** 本地模式绑定的授权记录 ID（mode=local 时非空） */
  grantId?: string | null
  /** 本地模式授权范围（mode=local 时非空） */
  localScope?: string | null
}

export interface CreateSessionRequest {
  agentId: string
  positionLabel?: string
  /** 执行模式：sandbox=云端沙箱（默认） / local=本地 Runner */
  mode?: 'sandbox' | 'local'
  /** 本地模式绑定的授权记录 ID（mode=local 时必填） */
  grantId?: string
  /** 本地模式授权范围（mode=local 时必填） */
  localScope?: string
}

/** 本地 Runner 在线状态（/api/local/status） */
export interface RunnerStatus {
  online: number
  connected: number
}

export interface CollabMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

// ============================================================
// HTTP 客户端（独立于 apiClient，因为协作服务运行在不同端口）
// ============================================================

const collabHttp = axios.create({
  baseURL: '/collab-api',
  timeout: 15000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

collabHttp.interceptors.request.use((config) => {
  const match = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)
  if (['post', 'put', 'patch', 'delete'].includes(config.method?.toLowerCase() || '') && match) {
    config.headers['X-CSRF-Token'] = decodeURIComponent(match[1])
  }
  return config
})

// ============================================================
// API 方法
// ============================================================

/** 健康检查 */
export async function checkHealth(): Promise<{ status: string; service: string; port: number }> {
  const { data } = await collabHttp.get('/health')
  return data
}

/** 创建协作会话 */
export async function createSession(req: CreateSessionRequest): Promise<CollabSession> {
  const { data } = await collabHttp.post<CollabSession>('/sessions', req)
  return data
}

/** 列出所有协作会话 */
export async function listSessions(): Promise<CollabSession[]> {
  const { data } = await collabHttp.get<CollabSession[]>('/sessions')
  return data
}

/** 获取会话详情 */
export async function getSession(sessionId: string): Promise<CollabSession> {
  const { data } = await collabHttp.get<CollabSession>(`/sessions/${sessionId}`)
  return data
}

/** 销毁会话 */
export async function deleteSession(sessionId: string): Promise<void> {
  await collabHttp.delete(`/sessions/${sessionId}`)
}

/** 获取会话消息历史 */
export async function getMessages(sessionId: string): Promise<CollabMessage[]> {
  const { data } = await collabHttp.get<CollabMessage[]>(`/sessions/${sessionId}/messages`)
  return data
}

/** 获取本地 Runner 在线状态（诊断/前端轮询用） */
export async function getRunnerStatus(): Promise<RunnerStatus> {
  const { data } = await collabHttp.get<RunnerStatus>('/local/status')
  return data
}

/** 上传附件到会话沙箱知识目录（纳入 AI 员工的检索上下文） */
export async function uploadAttachment(
  sessionId: string,
  file: File,
): Promise<{ success: boolean; name: string; path: string }> {
  const { data } = await collabHttp.post<{ success: boolean; name: string; path: string }>(
    `/sessions/${sessionId}/attachments?filename=${encodeURIComponent(file.name)}`,
    file,
    {
      headers: { 'Content-Type': 'application/octet-stream' },
      timeout: 30000,
    },
  )
  return data
}

