import apiClient from './client'
import { FolderScanResult, ApiResponse } from '@/types'

export const scanFolder = async (path: string, recursive: boolean = true): Promise<FolderScanResult> => {
  const response = await apiClient.post<ApiResponse<FolderScanResult>>('/folders/scan', {
    path,
    recursive
  })
  return response.data.data
}

/**
 * 目录子项（目录或文件）。
 */
export interface FolderChild {
  name: string
  path: string
  type: 'directory' | 'file'
  size?: number
}

/**
 * list-children 端点返回结构。
 */
export interface ListChildrenResult {
  current_path: string
  parent_path: string | null
  children: FolderChild[]
}

/**
 * 列出指定路径下的直接子项，供 Setup 路径快捷选择。
 *
 * 端点 GET /folders/list-children（无尾斜杠）。不传 path 时默认列出 UPLOAD_ROOT 子项。
 *
 * @param path 目标目录路径，省略时使用后端 UPLOAD_ROOT
 * @returns 当前路径、可导航父目录、子项列表
 */
export const listFolderChildren = async (path?: string): Promise<ListChildrenResult> => {
  const response = await apiClient.get<ApiResponse<ListChildrenResult>>('/folders/list-children', {
    params: path ? { path } : undefined,
  })
  return response.data.data
}

/** POST /folders/upload 响应：服务端暂存文件夹的绝对路径 */
export interface FolderUploadResult {
  folder_path: string
  folder_name: string
  file_count: number
  total_size: number
}

/**
 * 上传本地文件夹（webkitdirectory）到服务端 UPLOAD_ROOT 暂存，
 * 返回服务端可扫描的文件夹路径，供套用模板时作为知识源。
 *
 * 每个文件的相对路径（file.webkitRelativePath）作为 multipart filename 上传，
 * 后端据此还原目录结构。
 */
export const uploadFolder = async (files: File[]): Promise<FolderUploadResult> => {
  const formData = new FormData()
  files.forEach((file) => {
    // webkitRelativePath 形如 "公司文档/01-组织.md"；普通文件回退到 name
    const relPath = file.webkitRelativePath || file.name
    formData.append('files', file, relPath)
  })
  const response = await apiClient.post<ApiResponse<FolderUploadResult>>('/folders/upload', formData)
  return response.data.data
}
