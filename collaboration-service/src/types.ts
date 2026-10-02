/**
 * 协作服务类型定义 — WebSocket 消息协议 + 会话管理
 */

// ============================================================
// 会话管理类型
// ============================================================

export interface CollaborationSession {
  /** 会话 ID（UUID） */
  sessionId: string
  /** 关联的 AI 员工 ID */
  agentId: string
  /** AI 员工名称 */
  agentName: string
  /** 岗位标签 */
  positionLabel: string
  /** 创建时间 */
  createdAt: string
  /** 最后活跃时间 */
  lastActiveAt: string
  /** 消息数 */
  messageCount: number
  /** 最后一条消息摘要 */
  lastMessagePreview: string
  /** 执行模式：sandbox=云端沙箱（默认） / local=本地 Runner */
  mode: 'sandbox' | 'local'
  /** 本地模式绑定的授权记录 ID（mode=local 时非空） */
  grantId?: string | null
  /** 本地模式的授权范围（mode=local 时非空） */
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

// ============================================================
// WebSocket 消息协议
// ============================================================

/** 客户端 → 服务端 */
export type ClientMessage =
  | { type: 'prompt'; sessionId: string; message: string }
  | { type: 'abort'; sessionId: string }
  | { type: 'ping' }

/** 服务端 → 客户端 */
export type ServerMessage =
  | { type: 'pong' }
  | { type: 'agent_start'; sessionId: string }
  | { type: 'text_delta'; sessionId: string; delta: string }
  | { type: 'message_end'; sessionId: string }
  | { type: 'agent_end'; sessionId: string }
  | { type: 'error'; sessionId?: string; message: string }
  | { type: 'session_created'; session: CollaborationSession }
  | { type: 'connected'; clientId: string }
  // ---- 本地 Runner 在线状态（在线数变化时广播） ----
  | { type: 'runner_status'; online: number }
  // ---- 多步推理轨迹 ----
  | { type: 'reasoning_start'; sessionId: string; turnIndex: number }
  | { type: 'reasoning_end'; sessionId: string; turnIndex: number }
  // ---- 工具调用轨迹 ----
  | { type: 'tool_start'; sessionId: string; toolName: string; args: unknown; toolCallId?: string }
  | { type: 'tool_update'; sessionId: string; toolName: string; partialResult: unknown; toolCallId?: string }
  | { type: 'tool_end'; sessionId: string; toolName: string; result: unknown; isError: boolean; toolCallId?: string }
  // ---- 交付成果物（agent 在沙箱内生成的文件） ----
  | { type: 'artifact'; sessionId: string; path: string; name: string; content?: string }

// ============================================================
// 会话消息类型（从 pi.dev SDK 适配）
// ============================================================

export interface SessionMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}
