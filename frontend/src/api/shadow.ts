/**
 * 影子模式（Shadow）API 客户端 — 对应产品完善方案_v3.2 补1。
 *
 * 端点契约（/api/v1/shadow/*）：
 * - GET  /shadow/tasks?enterprise_id=&status=&agent_id=&limit=&offset=  任务列表
 * - POST /shadow/tasks                                                   创建任务
 * - GET  /shadow/tasks/summary?enterprise_id=                           阶段汇总
 * - POST /shadow/tasks/{id}/record-ai                                   记录 AI 回答
 * - POST /shadow/tasks/{id}/evaluate                                    评估（晋升/保持）
 * - POST /shadow/tasks/{id}/promote                                     晋升 autonomous
 * - POST /shadow/tasks/{id}/demote?target=                              降级
 */
import apiClient from './client'
import { ApiResponse, PaginatedResponse } from '@/types'
import type {
  ShadowTask,
  ShadowSummary,
  CreateShadowTaskRequest,
  RecordAiAnswerRequest,
} from '@/types'
import {
  ShadowSummarySchema,
  ShadowTaskListSchema,
  ShadowTaskSchema,
  parseContract,
} from './schemas'

/** 影子任务列表（GET /shadow/tasks） */
export async function listShadowTasks(
  enterpriseId: string,
  options: { status?: string; agentId?: string; limit?: number; offset?: number } = {},
): Promise<PaginatedResponse<ShadowTask>> {
  const { status, agentId, limit = 50, offset = 0 } = options
  const response = await apiClient.get<ApiResponse<PaginatedResponse<ShadowTask>>>(
    '/shadow/tasks',
    { params: { enterprise_id: enterpriseId, status, agent_id: agentId, limit, offset } },
  )
  return parseContract(ShadowTaskListSchema, response.data.data, 'GET /shadow/tasks')
}

/** 创建影子任务（POST /shadow/tasks） */
export async function createShadowTask(
  req: CreateShadowTaskRequest,
): Promise<ShadowTask> {
  const response = await apiClient.post<ApiResponse<ShadowTask>>('/shadow/tasks', req)
  return parseContract(ShadowTaskSchema, response.data.data, 'POST /shadow/tasks')
}

/** 影子模式阶段汇总（GET /shadow/tasks/summary） */
export async function getShadowSummary(
  enterpriseId: string,
): Promise<ShadowSummary> {
  const response = await apiClient.get<ApiResponse<ShadowSummary>>(
    '/shadow/tasks/summary',
    { params: { enterprise_id: enterpriseId } },
  )
  return parseContract(ShadowSummarySchema, response.data.data, 'GET /shadow/tasks/summary')
}

/** 记录 AI 回答（POST /shadow/tasks/{id}/record-ai） */
export async function recordAiAnswer(
  taskId: string,
  req: RecordAiAnswerRequest,
): Promise<ShadowTask> {
  const response = await apiClient.post<ApiResponse<ShadowTask>>(
    `/shadow/tasks/${taskId}/record-ai`,
    req,
  )
  return parseContract(
    ShadowTaskSchema,
    response.data.data,
    'POST /shadow/tasks/record-ai',
  )
}

/**
 * 让真实 Agent 对该影子任务作答（POST /shadow/tasks/{id}/generate-ai）。
 *
 * 与 recordAiAnswer 的区别：后者由调用方提供答案文本（用于外部系统回填），
 * 本接口由后端调用真实 LLM 生成，是 UI 上「AI 作答」按钮的正确实现。
 *
 * 修复：前端此前用模板字符串伪造 AI 答案写库，污染了影子模式
 * 赖以成立的人机对照样本。LLM 不可用时后端返回 503 且不写库 ——
 * 宁可没有答案，也不要假答案。
 */
export async function generateAiAnswer(taskId: string): Promise<ShadowTask> {
  const response = await apiClient.post<ApiResponse<ShadowTask>>(
    `/shadow/tasks/${taskId}/generate-ai`,
  )
  return parseContract(
    ShadowTaskSchema,
    response.data.data,
    'POST /shadow/tasks/generate-ai',
  )
}

/** 评估 AI vs 真人（POST /shadow/tasks/{id}/evaluate） */
export async function evaluateShadowTask(
  taskId: string,
  req: { match: boolean; auto_qualify?: boolean },
): Promise<ShadowTask> {
  const response = await apiClient.post<ApiResponse<ShadowTask>>(
    `/shadow/tasks/${taskId}/evaluate`,
    req,
  )
  return parseContract(
    ShadowTaskSchema,
    response.data.data,
    'POST /shadow/tasks/evaluate',
  )
}

/** 晋升 qualified → autonomous（POST /shadow/tasks/{id}/promote） */
export async function promoteShadowTask(taskId: string): Promise<ShadowTask> {
  const response = await apiClient.post<ApiResponse<ShadowTask>>(
    `/shadow/tasks/${taskId}/promote`,
  )
  return parseContract(
    ShadowTaskSchema,
    response.data.data,
    'POST /shadow/tasks/promote',
  )
}

/** 降级（POST /shadow/tasks/{id}/demote?target=） */
export async function demoteShadowTask(
  taskId: string,
  target: 'evaluating' | 'shadowing' = 'evaluating',
): Promise<ShadowTask> {
  const response = await apiClient.post<ApiResponse<ShadowTask>>(
    `/shadow/tasks/${taskId}/demote`,
    undefined,
    { params: { target } },
  )
  return parseContract(
    ShadowTaskSchema,
    response.data.data,
    'POST /shadow/tasks/demote',
  )
}