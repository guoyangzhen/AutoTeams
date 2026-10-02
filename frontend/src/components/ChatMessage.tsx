import { useState, memo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Copy, Check, ThumbsUp, ThumbsDown, FileText, ChevronRight } from 'lucide-react'
import { Message } from '@/types'
import { MarkdownRenderer } from '@/components/MarkdownRenderer'

interface ChatMessageProps {
  message: Message
  isStreaming?: boolean
  onSatisfaction?: (messageId: string, satisfaction: string) => void
}

function ChatMessageBase({ message, isStreaming = false, onSatisfaction }: ChatMessageProps) {
  const [showSources, setShowSources] = useState(false)
  const [copied, setCopied] = useState(false)
  const isUser = message.role === 'user'

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // ignore
    }
  }

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div className={`max-w-[85%] ${isUser ? 'order-2' : 'order-1'}`}>
        {/* 头像 + 名称 */}
        {!isUser && (
          <div className="flex items-center mb-1.5">
            <div className="w-7 h-7 rounded-full bg-brand-gradient flex items-center justify-center text-white text-caption font-semibold mr-2">
              AI
            </div>
            <span className="text-caption text-text-tertiary">智能助手</span>
          </div>
        )}

        {/* 消息气泡 */}
        <div
          className={`rounded-2xl px-4 py-3 ${
            isUser
              ? 'bg-brand-600 text-white rounded-br-md'
              : 'bg-surface text-text-primary border border-border-default rounded-bl-md'
          }`}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap break-words text-body">{message.content}</div>
          ) : (
            <div className={isStreaming ? 'typing-cursor' : ''}>
              <MarkdownRenderer content={message.content} />
            </div>
          )}

          {/* 来源引用卡片 */}
          {!isUser && message.sources && message.sources.length > 0 && (
            <div className="mt-3 pt-3 border-t border-border-subtle">
              <button
                onClick={() => setShowSources(!showSources)}
                className="flex items-center text-caption text-brand-400 hover:text-brand-500 transition-colors"
              >
                <ChevronRight
                  className={`h-3.5 w-3.5 mr-1 transition-transform ${showSources ? 'rotate-90' : ''}`}
                />
                {showSources ? '隐藏来源' : `查看来源 (${message.sources.length})`}
              </button>

              <AnimatePresence>
                {showSources && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="overflow-hidden"
                  >
                    <div className="mt-2 space-y-2">
                      {message.sources.map((source, index) => (
                        <div
                          key={index}
                          className="flex gap-2 p-2.5 bg-elevated rounded-lg border border-border-subtle"
                        >
                          <FileText className="h-4 w-4 flex-shrink-0 text-text-tertiary mt-0.5" />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between mb-0.5">
                              <span className="text-caption font-medium text-text-secondary truncate">
                                {source.source || '未知来源'}
                              </span>
                              <span className="text-caption text-text-tertiary ml-2">#{index + 1}</span>
                            </div>
                            <p className="text-caption text-text-tertiary line-clamp-2">{source.content}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}
        </div>

        {/* 操作栏 */}
        <div className={`flex items-center mt-1 gap-1 ${isUser ? 'justify-end' : 'justify-start'}`}>
          <span className="text-caption text-text-tertiary mr-2">{formatTime(message.created_at)}</span>

          {/* 复制按钮 */}
          {!isUser && !isStreaming && (
            <button
              onClick={handleCopy}
              className="p-1 rounded hover:bg-border-subtle text-text-tertiary hover:text-text-secondary transition-colors"
              title="复制"
              aria-label="复制"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-success" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
            </button>
          )}

          {/* 满意度反馈 */}
          {!isUser && onSatisfaction && !isStreaming && (
            <div className="flex items-center gap-0.5">
              <button
                onClick={() => onSatisfaction(message.id, 'satisfied')}
                className={`p-1 rounded hover:bg-success/10 transition-colors ${
                  message.satisfaction === 'satisfied' ? 'text-success' : 'text-text-tertiary hover:text-success'
                }`}
                title="满意"
                aria-label="满意"
              >
                <ThumbsUp className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
              <button
                onClick={() => onSatisfaction(message.id, 'unsatisfied')}
                className={`p-1 rounded hover:bg-error/10 transition-colors ${
                  message.satisfaction === 'unsatisfied' ? 'text-error' : 'text-text-tertiary hover:text-error'
                }`}
                title="不满意"
                aria-label="不满意"
              >
                <ThumbsDown className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export const ChatMessage = memo(ChatMessageBase)
export default ChatMessage
