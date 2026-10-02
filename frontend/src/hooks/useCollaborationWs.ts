/**
 * useCollaborationWs — 协作工作台 WebSocket Hook
 *
 * 对接 collaboration-service 的 WebSocket 端点，提供：
 * - 自动连接 / 断线重连
 * - sendPrompt(sessionId, message) — 发送消息并流式接收 AI 响应
 * - abort(sessionId) — 中止当前生成
 * - 实时状态：isStreaming, streamingText, connectionStatus
 *
 * WebSocket 协议（与 collaboration-service/src/types.ts 对齐）：
 * 客户端 → 服务端: prompt | abort | ping
 * 服务端 → 客户端: agent_start | text_delta | message_end | agent_end | error | connected | pong
 */
import { useState, useRef, useCallback, useEffect } from 'react'
import { getCollabWsUrl } from '@/config/runtimeConfig'

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error'

/** 一次工具调用的轨迹条目 */
export interface ToolCallEvent {
  toolCallId: string
  toolName: string
  args: unknown
  status: 'running' | 'success' | 'error'
  result?: unknown
  /** 累积的流式输出/diff 文本（bash 输出、edit diff 等） */
  output?: string
}

/** 一次多步推理的 turn 进度 */
export interface ReasoningStep {
  turnIndex: number
  status: 'running' | 'done'
}

/** 交付成果物（agent 在沙箱内生成的文件） */
export interface Artifact {
  path: string
  name: string
  content?: string
}

/** 从工具流式增量中提取可读文本（bash→output，edit/write→diff/content） */
function extractOutputDelta(partialResult: unknown): string {
  if (partialResult == null) return ''
  if (typeof partialResult === 'string') return partialResult
  if (typeof partialResult === 'object') {
    const obj = partialResult as Record<string, unknown>
    if (typeof obj.output === 'string') return obj.output
    if (typeof obj.diff === 'string') return obj.diff
    if (typeof obj.content === 'string') return obj.content
  }
  try {
    return JSON.stringify(partialResult)
  } catch {
    return String(partialResult)
  }
}

interface ServerMessage {
  type:
    | 'pong'
    | 'agent_start'
    | 'text_delta'
    | 'message_end'
    | 'agent_end'
    | 'error'
    | 'connected'
    | 'runner_status'
    | 'reasoning_start'
    | 'reasoning_end'
    | 'tool_start'
    | 'tool_update'
    | 'tool_end'
    | 'artifact'
  sessionId?: string
  /** runner_status 事件：当前在线（就绪）的本地 Runner 数量 */
  online?: number
  delta?: string
  message?: string
  clientId?: string
  turnIndex?: number
  toolName?: string
  args?: unknown
  partialResult?: unknown
  result?: unknown
  isError?: boolean
  toolCallId?: string
  path?: string
  name?: string
  content?: string
}

interface UseCollaborationWsOptions {
  /** 是否自动连接（默认 true） */
  autoConnect?: boolean
  /** 收到完整 AI 消息时的回调 */
  onMessageComplete?: (sessionId: string, fullText: string) => void
  /** 错误回调 */
  onError?: (error: string) => void
}

interface UseCollaborationWsReturn {
  /** 连接状态 */
  connectionStatus: ConnectionStatus
  /** 是否正在流式生成 */
  isStreaming: boolean
  /** 当前流式文本（实时更新） */
  streamingText: string
  /** 当前在线（就绪）的本地 Runner 数量（由 runner_status 事件实时推送） */
  runnerOnline: number
  /** 当前会话的工具调用轨迹 */
  toolCalls: ToolCallEvent[]
  /** 当前会话的多步推理进度 */
  reasoningSteps: ReasoningStep[]
  /** 当前会话的交付成果物 */
  artifacts: Artifact[]
  /** 发送消息 */
  sendPrompt: (sessionId: string, message: string) => void
  /** 中止生成 */
  abort: (sessionId: string) => void
  /** 手动连接 */
  connect: () => void
  /** 手动断开 */
  disconnect: () => void
}

export function useCollaborationWs(
  options: UseCollaborationWsOptions = {},
): UseCollaborationWsReturn {
  const { autoConnect = true, onMessageComplete, onError } = options

  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected')
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [runnerOnline, setRunnerOnline] = useState(0)
  const [toolCalls, setToolCalls] = useState<ToolCallEvent[]>([])
  const [reasoningSteps, setReasoningSteps] = useState<ReasoningStep[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const streamingTextRef = useRef('')
  const currentSessionIdRef = useRef<string | null>(null)
  const callbacksRef = useRef({ onMessageComplete, onError })
  // connect 自引用修复：用 ref 存储 latest connect，避免闭包捕获 TDZ 变量
  const connectRef = useRef<() => void>(() => {})

  // 保持回调引用最新
  callbacksRef.current = { onMessageComplete, onError }

  /** 发送 JSON 消息到 WebSocket */
  const sendRaw = useCallback((msg: Record<string, unknown>) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      callbacksRef.current.onError?.('WebSocket 未连接')
      return
    }
    ws.send(JSON.stringify(msg))
  }, [])

  /** 处理服务端消息 */
  const handleMessage = useCallback((data: string) => {
    let msg: ServerMessage
    try {
      msg = JSON.parse(data)
    } catch {
      return
    }

    switch (msg.type) {
      case 'connected':
        setConnectionStatus('connected')
        break

      case 'runner_status':
        // 在线 Runner 数变化（连接时也会主动推送一次当前值）
        if (typeof msg.online === 'number') {
          setRunnerOnline(msg.online)
        }
        break

      case 'pong':
        break

      case 'agent_start':
        setIsStreaming(true)
        streamingTextRef.current = ''
        setStreamingText('')
        setToolCalls([])
        setReasoningSteps([])
        setArtifacts([])
        currentSessionIdRef.current = msg.sessionId || null
        break

      case 'reasoning_start':
        if (msg.turnIndex !== undefined) {
          setReasoningSteps((prev) => [
            ...prev,
            { turnIndex: msg.turnIndex as number, status: 'running' },
          ])
        }
        break

      case 'reasoning_end':
        if (msg.turnIndex !== undefined) {
          setReasoningSteps((prev) =>
            prev.map((s) =>
              s.turnIndex === msg.turnIndex ? { ...s, status: 'done' as const } : s,
            ),
          )
        }
        break

      case 'tool_start':
        setToolCalls((prev) => [
          ...prev,
          {
            toolCallId: msg.toolCallId || `${Date.now()}-${prev.length}`,
            toolName: msg.toolName || 'tool',
            args: msg.args,
            status: 'running',
          },
        ])
        break

      case 'tool_update': {
        const toolCallId = msg.toolCallId
        setToolCalls((prev) =>
          prev.map((tc) => {
            if (tc.toolCallId !== toolCallId) return tc
            const delta = extractOutputDelta(msg.partialResult)
            if (!delta) return tc
            return { ...tc, output: (tc.output || '') + delta }
          }),
        )
        break
      }

      case 'tool_end': {
        const toolCallId = msg.toolCallId
        setToolCalls((prev) =>
          prev.map((tc) =>
            tc.toolCallId === toolCallId
              ? { ...tc, status: msg.isError ? ('error' as const) : ('success' as const), result: msg.result }
              : tc,
          ),
        )
        break
      }

      case 'artifact':
        if (msg.path) {
          const path = msg.path
          setArtifacts((prev) => {
            if (prev.some((a) => a.path === path)) return prev
            return [...prev, { path, name: msg.name || path, content: msg.content }]
          })
        }
        break

      case 'message_end':
        // 消息完成，但 agent 可能还在继续（如工具调用）
        break

      case 'text_delta':
        if (msg.delta) {
          streamingTextRef.current += msg.delta
          setStreamingText(streamingTextRef.current)
        }
        break

      case 'agent_end':
        setIsStreaming(false)
        if (currentSessionIdRef.current && streamingTextRef.current) {
          callbacksRef.current.onMessageComplete?.(
            currentSessionIdRef.current,
            streamingTextRef.current,
          )
        }
        streamingTextRef.current = ''
        setStreamingText('')
        currentSessionIdRef.current = null
        break

      case 'error':
        setIsStreaming(false)
        streamingTextRef.current = ''
        setStreamingText('')
        setToolCalls([])
        setReasoningSteps([])
        setArtifacts([])
        currentSessionIdRef.current = null
        callbacksRef.current.onError?.(msg.message || '未知错误')
        break
    }
  }, [])

  /** 连接 WebSocket */
  const connect = useCallback(() => {
    // 清理旧连接
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    // 清理重连定时器
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    setConnectionStatus('connecting')

    try {
      const ws = new WebSocket(getCollabWsUrl())
      wsRef.current = ws

      ws.onopen = () => {
        // connected 状态由服务端 'connected' 消息确认
        // 这里先设为 connecting，等服务端确认后设为 connected
      }

      ws.onmessage = (event) => {
        handleMessage(typeof event.data === 'string' ? event.data : '')
      }

      ws.onerror = () => {
        setConnectionStatus('error')
      }

      ws.onclose = () => {
        setConnectionStatus('disconnected')
        wsRef.current = null
        // 自动重连（3 秒延迟）— 通过 ref 调用 latest connect，避免自引用 TDZ
        reconnectTimerRef.current = setTimeout(() => {
          connectRef.current()
        }, 3000)
      }
    } catch {
      setConnectionStatus('error')
    }
  }, [handleMessage])

  // 保持 connectRef 指向 latest connect（修复自引用 TDZ）
  connectRef.current = connect

  /** 断开 WebSocket */
  const disconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setConnectionStatus('disconnected')
  }, [])

  /** 发送 prompt */
  const sendPrompt = useCallback(
    (sessionId: string, message: string) => {
      // 重置流式状态
      streamingTextRef.current = ''
      setStreamingText('')
      currentSessionIdRef.current = sessionId
      sendRaw({ type: 'prompt', sessionId, message })
    },
    [sendRaw],
  )

  /** 中止生成 */
  const abort = useCallback(
    (sessionId: string) => {
      sendRaw({ type: 'abort', sessionId })
      setIsStreaming(false)
      streamingTextRef.current = ''
      setStreamingText('')
      currentSessionIdRef.current = null
    },
    [sendRaw],
  )

  // 自动连接
  useEffect(() => {
    if (autoConnect) {
      connect()
    }
    return () => {
      disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoConnect])

  // 心跳保活（30 秒发送 ping）
  useEffect(() => {
    if (connectionStatus !== 'connected') return
    const timer = setInterval(() => {
      sendRaw({ type: 'ping' })
    }, 30000)
    return () => clearInterval(timer)
  }, [connectionStatus, sendRaw])

  return {
    connectionStatus,
    isStreaming,
    streamingText,
    runnerOnline,
    toolCalls,
    reasoningSteps,
    artifacts,
    sendPrompt,
    abort,
    connect,
    disconnect,
  }
}
