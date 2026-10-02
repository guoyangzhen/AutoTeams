import { useState, useCallback, useRef, useEffect } from 'react'
import type { Source } from '@/types'

// BE-SEC-01: 从 document.cookie 读取指定名称的值（与 apiClient 拦截器逻辑一致）
function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(^| )' + name + '(?:=([^;]*))?'))
  return match?.[2] ?? null
}

interface SSEOptions {
  onMessage?: (data: unknown) => void
  onError?: (error: Error) => void
  onOpen?: () => void
  onClose?: () => void
  /** 非主动断开时自动重连的最大次数（默认 3） */
  maxRetries?: number
  /** 重连基础退避毫秒（默认 1000） */
  baseRetryDelayMs?: number
}

export function useSSE(options: SSEOptions = {}) {
  const [isConnected, setIsConnected] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const retryCountRef = useRef(0)
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const { onMessage, onError, onOpen, onClose, maxRetries = 3, baseRetryDelayMs = 1000 } = options

  const disconnect = useCallback(() => {
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current)
      retryTimeoutRef.current = null
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
      setIsConnected(false)
      onClose?.()
    }
  }, [onClose])

  // 通过 ref 保存 connect 的最新实现，避免重连调度与连接函数之间的循环依赖
  const connectRef = useRef<(url: string, body?: Record<string, unknown>) => void>()

  const scheduleReconnect = useCallback((url: string, body?: Record<string, unknown>) => {
    if (retryCountRef.current >= maxRetries) {
      retryCountRef.current = 0
      const finalError = new Error('SSE 连接多次失败，请检查网络后重试')
      setError(finalError)
      onError?.(finalError)
      setIsConnected(false)
      return
    }
    // P2-T11: 指数退避 + jitter（随机抖动），防止重连雪崩
    // 延迟 = baseDelay * 2^retryCount + 随机 [0, baseDelay)，最大不超过 30 秒
    const expDelay = baseRetryDelayMs * 2 ** retryCountRef.current
    const jitter = Math.random() * baseRetryDelayMs
    const delay = Math.min(expDelay + jitter, 30000)
    retryTimeoutRef.current = setTimeout(() => {
      retryCountRef.current += 1
      connectRef.current?.(url, body)
    }, delay)
  }, [maxRetries, baseRetryDelayMs, onError])

  const connectInternal = useCallback(async (url: string, body?: Record<string, unknown>) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }

    const abortController = new AbortController()
    abortControllerRef.current = abortController

    try {
      setIsConnected(true)
      setError(null)
      onOpen?.()

      // P1-1: SSE 通过 Cookie 认证，fetch 需显式携带 credentials
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      }
      // BE-SEC-01: SSE 是 POST 请求，需携带 CSRF token（与 apiClient 拦截器一致）
      const csrfToken = getCookie('csrf_token')
      if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken
      }

      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: body ? JSON.stringify(body) : undefined,
        credentials: 'include',
        signal: abortController.signal,
      })

      if (!response.ok) {
        // 4xx 为不可恢复错误，直接失败不重连
        if (response.status >= 400 && response.status < 500) {
          throw new Error(`客户端错误 ${response.status}，请刷新后重试`)
        }
        throw new Error(`服务端错误 ${response.status}`)
      }

      // 连接成功，重置重试计数
      retryCountRef.current = 0

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('ReadableStream not supported')
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()

        if (done) {
          break
        }

        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            if (data.trim() === '[DONE]') {
              disconnect()
              return
            }
            try {
              const parsed = JSON.parse(data)
              onMessage?.(parsed)
            } catch {
              onMessage?.(data)
            }
          }
        }
      }

      // 正常结束，不重连
      disconnect()
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        return
      }
      const error = err instanceof Error ? err : new Error(String(err))
      setError(error)
      onError?.(error)
      abortControllerRef.current = null
      setIsConnected(false)
      // 仅对 5xx / 网络错误自动重连；4xx 已在上面抛出并跳过此处
      scheduleReconnect(url, body)
    }
  }, [disconnect, onMessage, onError, onOpen, scheduleReconnect])

  const connect = useCallback((url: string, body?: Record<string, unknown>) => {
    retryCountRef.current = 0
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current)
      retryTimeoutRef.current = null
    }
    connectInternal(url, body)
  }, [connectInternal])

  useEffect(() => {
    connectRef.current = connect
  }, [connect])

  // 卸载时清理
  useEffect(() => {
    return () => {
      if (retryTimeoutRef.current) {
        clearTimeout(retryTimeoutRef.current)
      }
      abortControllerRef.current?.abort()
    }
  }, [])

  return {
    isConnected,
    error,
    connect,
    disconnect
  }
}

export function useSSEWithAgent(agentId: string, conversationId?: string) {
  const [messages, setMessages] = useState<Array<{ role: string; content: string; sources?: Array<{ content: string; source: string }> }>>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamError, setStreamError] = useState<string | null>(null)
  const accumulatedContentRef = useRef<string>('')

  const { isConnected, connect, disconnect } = useSSE({
    onMessage: (data) => {
      if (typeof data === 'object' && data !== null) {
        const parsed = data as {
          content?: string
          done: boolean
          message_id?: string
          conversation_id?: string
          error?: string
          sources?: Source[]
        }
        if (parsed.done) {
          setIsStreaming(false)
          // BE-REL-03: 后端明确返回错误/保存失败时提示用户
          if (parsed.error || parsed.message_id === 'error') {
            setStreamError(parsed.error || '消息保存失败，历史记录可能缺失')
            return
          }
          // P1-RAG: 将后端返回的检索来源挂载到当前助手消息
          if (parsed.sources && parsed.sources.length > 0) {
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last && last.role === 'assistant') {
                return [
                  ...prev.slice(0, -1),
                  { ...last, sources: parsed.sources }
                ]
              }
              return prev
            })
          }
          return
        }
        if (parsed.error) {
          setStreamError(parsed.error)
          setIsStreaming(false)
          return
        }
        const content = parsed.content
        if (content) {
          accumulatedContentRef.current += content
          setMessages(prev => {
            const last = prev[prev.length - 1]
            if (last && last.role === 'assistant') {
              return [
                ...prev.slice(0, -1),
                { ...last, content: accumulatedContentRef.current }
              ]
            }
            return [...prev, { role: 'assistant', content, sources: undefined }]
          })
        }
      }
    },
    onError: (error) => {
      setStreamError(error.message)
      setIsStreaming(false)
    },
    onClose: () => {
      setIsStreaming(false)
    },
  })

  const sendMessage = useCallback((content: string) => {
    if (!agentId) return
    accumulatedContentRef.current = ''
    setMessages([])
    setStreamError(null)
    setIsStreaming(true)
    connect(`/api/v1/agents/${agentId}/chat/stream`, {
      conversation_id: conversationId,
      content,
    })
  }, [agentId, conversationId, connect])

  const stopStreaming = useCallback(() => {
    disconnect()
    setIsStreaming(false)
  }, [disconnect])

  const clearMessages = useCallback(() => {
    setMessages([])
    accumulatedContentRef.current = ''
  }, [])

  return {
    messages,
    isStreaming,
    isConnected,
    streamError,
    sendMessage,
    stopStreaming,
    clearMessages,
  }
}
