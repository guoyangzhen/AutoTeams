/**
 * 编译器（Compiler）API 客户端 — 对应 spec.md §10.7 WT1 端点。
 *
 * 端点契约（/api/v1/compiler/*）：
 * - POST /compiler/compile          触发五级编译
 * - GET  /compiler/jobs/{job_id}    查询编译任务
 * - GET  /compiler/jobs              列出编译任务（分页）
 * - GET  /compiler/completeness/{enterprise_id}  查询完成度
 * - POST /compiler/recompile          触发增量重编译
 * - GET  /compiler/animation/{job_id} 编译动画数据
 *
 * Mock 开关：VITE_USE_MOCK=true 时返回 mockData，便于前端独立开发。
 */
import apiClient from './client'
import { ApiResponse, PaginatedResponse } from '@/types'
import type {
  CompileRequest,
  CompileResponse,
  CompilationJob,
  CompletenessResult,
  RecompileRequest,
  CompilationAnimation,
} from '@/types'
import {
  mockDelay,
  mockCompilationJob,
  mockCompleteness,
  mockCompilationAnimation,
} from './mockData'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/**
 * 编译触发响应（含数据源标记，来自后端 /compiler/compile 返回值）。
 *
 * source：'user_upload' 表示用户显式指定了上传的数据文件夹；'default' 表示使用演示数据。
 * folder_name：实际使用的数据源文件夹名（仅 basename），仅当 source=user_upload 时前端展示。
 */
export type CompileResponseWithSource = CompileResponse & {
  source?: 'default' | 'user_upload'
  folder_name?: string | null
}

/** 触发五级编译（POST /compiler/compile） */
export async function compile(
  req: CompileRequest,
  options?: { folderPath?: string },
): Promise<CompileResponseWithSource> {
  if (USE_MOCK) {
    // 后端 compile 端点同步返回 'completed'（非 'running'），并附加运行时统计字段
    return mockDelay({
      job_id: 'job-mock-001',
      status: 'completed',
      completeness: 85,
      level: 'runnable',
      runtime_version: 'v1.1.0',
      agent_count: 5,
      process_count: 4,
      source: options?.folderPath ? 'user_upload' : 'default',
      folder_name: options?.folderPath
        ? options.folderPath.split(/[\\/]/).filter(Boolean).pop() || options.folderPath
        : null,
    })
  }
  const response = await apiClient.post<ApiResponse<CompileResponseWithSource>>(
    '/compiler/compile',
    req,
    { params: options?.folderPath ? { folder_path: options.folderPath } : undefined },
  )
  return response.data.data
}

/** 查询编译任务详情（GET /compiler/jobs/{job_id}） */
export async function getCompilationJob(jobId: string): Promise<CompilationJob> {
  if (USE_MOCK) {
    return mockDelay({ ...mockCompilationJob, job_id: jobId })
  }
  const response = await apiClient.get<ApiResponse<CompilationJob>>(`/compiler/jobs/${jobId}`)
  return response.data.data
}

/** 列出编译任务（GET /compiler/jobs?enterprise_id=&limit=&offset=） */
export async function listCompilationJobs(
  enterpriseId: string,
  limit = 20,
  offset = 0,
): Promise<PaginatedResponse<CompilationJob>> {
  if (USE_MOCK) {
    return mockDelay({ items: [{ ...mockCompilationJob, enterprise_id: enterpriseId }], total: 1 })
  }
  const response = await apiClient.get<ApiResponse<PaginatedResponse<CompilationJob>>>('/compiler/jobs', {
    params: { enterprise_id: enterpriseId, limit, offset },
  })
  return response.data.data
}

/** 查询完成度（GET /compiler/completeness/{enterprise_id}） */
export async function getCompleteness(enterpriseId: string): Promise<CompletenessResult> {
  if (USE_MOCK) {
    return mockDelay({ ...mockCompleteness })
  }
  const response = await apiClient.get<ApiResponse<CompletenessResult>>(
    `/compiler/completeness/${enterpriseId}`,
  )
  return response.data.data
}

/** 触发增量重编译（POST /compiler/recompile） */
export async function recompile(req: RecompileRequest): Promise<CompileResponse> {
  if (USE_MOCK) {
    // 后端同步返回 'completed'，并附加运行时统计字段
    return mockDelay({
      job_id: 'job-mock-002',
      status: 'completed',
      completeness: 87,
      level: 'runnable',
      runtime_version: 'v1.2.0',
      agent_count: 6,
      process_count: 4,
    })
  }
  const response = await apiClient.post<ApiResponse<CompileResponse>>('/compiler/recompile', req)
  return response.data.data
}

/** 编译动画数据（GET /compiler/animation/{job_id}） */
export async function getCompilationAnimation(jobId: string): Promise<CompilationAnimation> {
  if (USE_MOCK) {
    return mockDelay({ ...mockCompilationAnimation })
  }
  const response = await apiClient.get<ApiResponse<CompilationAnimation>>(
    `/compiler/animation/${jobId}`,
  )
  return response.data.data
}

// ============================================================
// UI v4 新增：编译回放（路演演示）
// ============================================================

/** 单级回放阶段（含真实耗时，供按真实时序加速回放） */
export interface ReplayStage {
  name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  discovered: string
  confidence: number | null
  duration_ms: number | null
}

export interface CompilationReplay {
  available: boolean
  reason?: string
  job_id?: string
  enterprise_id?: string
  stages: ReplayStage[]
  confidence?: number
  completeness?: number
  total_duration_ms?: number
  compiled_at?: string | null
}

/**
 * 编译回放数据（GET /compiler/replay/{enterprise_id}）。
 *
 * 真实编译需 10-20 分钟，路演/评委远程体验无法现场等待。
 * 本接口返回**最近一次成功编译的真实产物与真实时序**，
 * 前端按 duration_ms 相对比例加速回放五级管道动画。
 *
 * 数据 100% 真实，仅压缩时间轴，不虚构任何内容。
 * available=false 时前端应隐藏回放入口（诚实空态）。
 */
export async function getCompilationReplay(enterpriseId: string): Promise<CompilationReplay> {
  const response = await apiClient.get<ApiResponse<CompilationReplay>>(
    `/compiler/replay/${enterpriseId}`,
  )
  return response.data.data
}

/** 编译进度 SSE 单帧负载（GET /compiler/jobs/{job_id}/stream） */
export interface CompileProgressFrame {
  job_id?: string
  enterprise_id?: string
  stage?: string
  status?: string
  /** 后端真实进度 0-1（替代前端「已完成阶段/5」估算） */
  progress?: number
  confidence?: number
  completeness?: number
  error_message?: string | null
  stages?: ReplayStage[]
  done?: boolean
  timeout?: boolean
  error?: string
}

/** 编译进度 SSE 端点路径（供 useSSE / EventSource 使用） */
export function compileStreamPath(jobId: string): string {
  return `/compiler/jobs/${jobId}/stream`
}
