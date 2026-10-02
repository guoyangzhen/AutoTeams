/**
 * VoiceRecorder — 语音录制 + 上传转录组件（产品完善方案 P0-1 语音讨论入口）。
 *
 * 使用浏览器 MediaRecorder 录制音频，上传至后端 /api/v1/audio/transcribe，
 * 由 faster-whisper 本地转录为结构化文本。转录完成后通过 onTranscribed 回调注入页面。
 *
 * 设计要点：
 * - 优雅降级：浏览器不支持 MediaRecorder / 后端转录不可用时给出明确提示，不阻塞页面。
 * - 复用现有 dist 设计令牌（brand/error/success），与 Button.tsx 风格一致。
 * - 录制中实时显示时长，可随时停止/取消。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Mic, Square, Loader2, CheckCircle2, AlertTriangle, X } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { transcribeAudio } from '@/api/audio'

interface VoiceRecorderProps {
  /** 转录完成回调（文本注入到页面输入框） */
  onTranscribed: (text: string) => void
  /** 是否禁用（如提交中） */
  disabled?: boolean
  /** 按钮文案场景前缀 */
  label?: string
}

/** 兼容各浏览器 MediaRecorder 的 MIME 类型 */
function pickMimeType(): string {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/ogg;codecs=opus',
  ]
  for (const type of candidates) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(type)) {
      return type
    }
  }
  return ''
}

export function VoiceRecorder({ onTranscribed, disabled = false, label = '语音录入' }: VoiceRecorderProps) {
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const timerRef = useRef<number | null>(null)

  const supported = typeof window !== 'undefined' && !!window.MediaRecorder

  // 清理计时器
  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  const stopRecording = useCallback(() => {
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    mediaRecorderRef.current?.stop()
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
  }, [])

  const handleUpload = useCallback(
    async (blob: Blob) => {
      setTranscribing(true)
      setRecording(false)
      try {
        const ext = (blob.type || 'audio/webm').split('/')[1]?.split(';')[0] || 'webm'
        const filename = `recording.${ext === 'ogg' ? 'ogg' : 'webm'}`
        const resp = await transcribeAudio(blob, filename, 'zh')
        if (resp.text) {
          setDone(true)
          onTranscribed(resp.text)
        } else {
          setError(
            resp.available
              ? '未能识别到语音内容，请重试'
              : '后端语音转录服务不可用（faster-whisper 未部署），请手动输入',
          )
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : '语音转录失败，请重试')
      } finally {
        setTranscribing(false)
      }
    },
    [onTranscribed],
  )

  const startRecording = useCallback(async () => {
    if (!supported || disabled) return
    setError(null)
    setDone(false)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      const mimeType = pickMimeType()
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mimeType || 'audio/webm' })
        chunksRef.current = []
        void handleUpload(blob)
      }
      mediaRecorderRef.current = recorder
      recorder.start()
      setRecording(true)
      setElapsed(0)
      timerRef.current = window.setInterval(() => {
        setElapsed((s) => s + 1)
      }, 1000)
    } catch (err) {
      setError('无法访问麦克风，请检查浏览器权限设置')
      console.warn('MediaRecorder 启动失败:', err)
    }
  }, [supported, disabled, handleUpload])

  const cancel = useCallback(() => {
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    mediaRecorderRef.current?.stop()
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    chunksRef.current = []
    setRecording(false)
    setError(null)
  }, [])

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60)
    const sec = s % 60
    return `${m}:${sec.toString().padStart(2, '0')}`
  }

  if (!supported) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-text-tertiary">
        <AlertTriangle className="w-3.5 h-3.5" aria-hidden="true" />
        当前浏览器不支持语音录入
      </span>
    )
  }

  return (
    <div className="inline-flex items-center gap-2">
      {!recording && !transcribing && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={startRecording}
          className="text-brand-500 hover:text-brand-600"
        >
          <Mic className="w-4 h-4" aria-hidden="true" />
          {label}
        </Button>
      )}

      {recording && (
        <div className="inline-flex items-center gap-2 rounded-full bg-error/10 border border-error/30 px-3 py-1">
          <span className="w-2 h-2 rounded-full bg-error animate-pulse" />
          <span className="text-xs text-error font-medium">录制中 {formatTime(elapsed)}</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={stopRecording}
            className="text-error hover:text-error"
          >
            <Square className="w-3.5 h-3.5" aria-hidden="true" />
            完成
          </Button>
          <button
            type="button"
            onClick={cancel}
            className="text-text-tertiary hover:text-text-primary"
            aria-label="取消录音"
          >
            <X className="w-3.5 h-3.5" aria-hidden="true" />
          </button>
        </div>
      )}

      {transcribing && (
        <span className="inline-flex items-center gap-1.5 text-xs text-text-secondary">
          <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
          正在转录语音...
        </span>
      )}

      {done && !transcribing && (
        <span className="inline-flex items-center gap-1.5 text-xs text-success">
          <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />
          已转录音文
        </span>
      )}

      {error && !transcribing && (
        <button
          type="button"
          onClick={() => setError(null)}
          className="inline-flex items-center gap-1.5 text-xs text-warning"
          title="点击关闭"
        >
          <AlertTriangle className="w-3.5 h-3.5" aria-hidden="true" />
          {error}
        </button>
      )}
    </div>
  )
}