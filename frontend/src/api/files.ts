import apiClient from './client'
import { ApiResponse } from '@/types'

/** 后端 File 记录（对应 backend/app/schemas/file.py 的 FileResponse） */
export interface FileRecord {
  id: string
  agent_id: string
  original_name: string
  file_path: string
  file_size: number
  file_type: string
  status: string
  is_confidential: boolean
  /** 是否为极度私密/涉密文件（决定是否需要授权管理） */
  is_highly_confidential?: boolean
  /** 涉密状态：'authorized' / 'pending' / null */
  confidential_status?: string | null
  chunk_count: number
  vector_count: number
  error_message: string | null
  content_hash: string | null
  created_at: string
}

export interface FileListResponse {
  files: FileRecord[]
  total: number
}

/** 文件预览响应 */
export interface FilePreview {
  file_id: string
  original_name: string
  file_type: string
  preview: string
  truncated: boolean
}

/** 分页查询文件列表 */
export const listFiles = async (params?: {
  agent_id?: string
  limit?: number
  offset?: number
}): Promise<FileListResponse> => {
  const response = await apiClient.get<ApiResponse<FileListResponse>>('/files', {
    params,
  })
  return response.data.data
}

/** 获取单个文件详情 */
export const getFile = async (fileId: string): Promise<FileRecord> => {
  const response = await apiClient.get<ApiResponse<FileRecord>>(`/files/${fileId}`)
  return response.data.data
}

/** 预览文本类文件内容 */
export const previewFile = async (fileId: string, maxChars = 500): Promise<FilePreview> => {
  const response = await apiClient.get<ApiResponse<FilePreview>>(
    `/files/${fileId}/preview`,
    { params: { max_chars: maxChars } }
  )
  return response.data.data
}

/** 删除文件（需要管理员权限） */
export const deleteFile = async (fileId: string): Promise<void> => {
  await apiClient.delete(`/files/${fileId}`)
}

/** 批量上传结果项 */
export interface BatchUploadItem {
  status: 'success' | 'error'
  file?: FileRecord
  original_name?: string
  error?: string
}

/** 批量上传响应 */
export interface BatchUploadResponse {
  total: number
  successful: number
  failed: number
  items: BatchUploadItem[]
}

/** 批量/文件夹上传文件到指定 Agent */
export const batchUploadFiles = async (
  agentId: string,
  files: File[]
): Promise<BatchUploadResponse> => {
  const formData = new FormData()
  formData.append('agent_id', agentId)
  files.forEach((file) => formData.append('files', file))
  const response = await apiClient.post<ApiResponse<BatchUploadResponse>>(
    '/files/batch-upload',
    formData,
  )
  return response.data.data
}

/** 涉密文件授权记录 */
export interface ConfidentialAccess {
  id: string
  file_id: string
  user_id: string
  granted_by: string
  granted_at: string | null
}

/**
 * 涉密文件授权管理（P1-SANDBOX）：
 * 以下 3 个函数激活 backend/app/api/files.py 中半接入的端点，
 * 闭合"极度私密文件"的授权管理闭环。
 * 答辩演示价值：金融/医疗数据安全叙事。
 */

/** 授予指定用户访问极度私密文件的权限（管理员） */
export async function grantConfidentialAccess(
  fileId: string,
  targetUserId: string,
): Promise<ConfidentialAccess> {
  const formData = new FormData()
  formData.append('target_user_id', targetUserId)
  const response = await apiClient.post<ApiResponse<ConfidentialAccess>>(
    `/files/${fileId}/confidential/grant`,
    formData,
  )
  return response.data.data
}

/** 撤销指定用户对极度私密文件的访问权限（管理员） */
export async function revokeConfidentialAccess(
  fileId: string,
  targetUserId: string,
): Promise<void> {
  const formData = new FormData()
  formData.append('target_user_id', targetUserId)
  await apiClient.post(`/files/${fileId}/confidential/revoke`, formData)
}

/** 查询极度私密文件的授权访问列表（管理员） */
export async function listConfidentialAccess(
  fileId: string,
): Promise<ConfidentialAccess[]> {
  const response = await apiClient.get<ApiResponse<ConfidentialAccess[]>>(
    `/files/${fileId}/confidential/access`,
  )
  return response.data.data
}
