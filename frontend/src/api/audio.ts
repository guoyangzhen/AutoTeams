/**
 * 音频转录 API 客户端 — 产品完善方案 P0-1 语音讨论入口。
 *
 * 端点契约（/api/v1/audio/*）：
 * - POST /audio/transcribe   上传音频并转录为文本
 */
import apiClient from './client'
import { ApiResponse } from '@/types'

/** 音频转录响应 */
export interface AudioTranscribeResponse {
  text: string
  language: string
  /** 后端 faster-whisper 是否可用（不可用时文本为空） */
  available: boolean
}

/**
 * 上传音频并转录为文本（POST /audio/transcribe）
 * @param audioBlob 录音的 Blob（webm/wav/mp3 等）
 * @param filename  建议文件名（含扩展名，用于后端类型检测）
 * @param language  语言代码，默认 zh
 */
export async function transcribeAudio(
  audioBlob: Blob,
  filename = 'recording.webm',
  language = 'zh',
): Promise<AudioTranscribeResponse> {
  const formData = new FormData()
  formData.append('file', audioBlob, filename)
  formData.append('language', language)
  const response = await apiClient.post<ApiResponse<AudioTranscribeResponse>>(
    '/audio/transcribe',
    formData,
  )
  return response.data.data
}