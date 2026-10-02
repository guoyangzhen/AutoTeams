import apiClient from './client'
import { Skill, ApiResponse } from '@/types'

export interface SkillExecutionResult {
  skill_id: string
  output_data: Record<string, unknown>
  execution_time_ms: number
}

export const getSkills = async (agentId?: string): Promise<Skill[]> => {
  const response = await apiClient.get<ApiResponse<Skill[]>>('/skills', {
    params: agentId ? { agent_id: agentId } : undefined
  })
  return response.data.data
}

export const getSkill = async (id: string): Promise<Skill> => {
  const response = await apiClient.get<ApiResponse<Skill>>(`/skills/${id}`)
  return response.data.data
}

export const executeSkill = async (skillId: string, inputData: Record<string, unknown>): Promise<SkillExecutionResult> => {
  const response = await apiClient.post<ApiResponse<SkillExecutionResult>>(
    `/skills/${skillId}/execute`,
    { input_data: inputData }
  )
  return response.data.data
}

// ============================================================
// 死代码激活（A4/B1/A5/B2）：补全 Skill 管理 API 客户端
// 后端端点已在 backend/app/api/skills.py 实现，但前端从未调用
// ============================================================

/** A4: AI 自主生成 Skill（基于协作历史与知识库） */
export interface GenerateSkillsResult {
  generated: number
  skills: Skill[]
}

export const generateSkills = async (
  agentId: string,
  maxSkills: number = 3,
): Promise<GenerateSkillsResult> => {
  const response = await apiClient.post<ApiResponse<GenerateSkillsResult>>('/skills/generate', {
    agent_id: agentId,
    max_skills: maxSkills,
  })
  return response.data.data
}

/** B1: 新建技能 */
export const createSkill = async (data: {
  agent_id: string
  name: string
  description?: string
  skill_type: string
  input_type?: string
  output_type?: string
  config?: Record<string, unknown>
  permissions?: string[]
}): Promise<Skill> => {
  const response = await apiClient.post<ApiResponse<Skill>>('/skills', data)
  return response.data.data
}

/** P1-SKILL: 导入外部 Skill 包（进入 pending 待审批队列） */
export const importSkill = async (
  agentId: string,
  packageData: {
    name: string
    description?: string
    skill_type: string
    input_type?: string
    output_type?: string
    config?: Record<string, unknown>
    permissions?: string[]
  },
): Promise<Skill> => {
  const response = await apiClient.post<ApiResponse<Skill>>('/skills/import', {
    agent_id: agentId,
    package: packageData,
  })
  return response.data.data
}

/** B1: 更新技能 */
export const updateSkill = async (
  skillId: string,
  data: Partial<{
    name: string
    description: string
    skill_type: string
    input_type: string
    output_type: string
    config: Record<string, unknown>
    permissions: string[]
  }>,
): Promise<Skill> => {
  const response = await apiClient.put<ApiResponse<Skill>>(`/skills/${skillId}`, data)
  return response.data.data
}

/** B1: 删除技能 */
export const deleteSkill = async (skillId: string): Promise<void> => {
  await apiClient.delete(`/skills/${skillId}`)
}

/** A5: 审批通过待审 Skill（管理员） */
export const approveSkill = async (skillId: string, reason: string = ''): Promise<Skill> => {
  const response = await apiClient.post<ApiResponse<Skill>>(`/skills/${skillId}/approve`, { reason })
  return response.data.data
}

/** A5: 拒绝待审 Skill（管理员） */
export const rejectSkill = async (skillId: string, reason: string = ''): Promise<Skill> => {
  const response = await apiClient.post<ApiResponse<Skill>>(`/skills/${skillId}/reject`, { reason })
  return response.data.data
}

/** B2: 查询技能执行历史 */
export interface SkillExecutionRecord {
  id: string
  skill_id: string
  user_id: string | null
  input_data: Record<string, unknown> | null
  output_data: Record<string, unknown> | null
  execution_time_ms: number
  status: string
  error_message: string | null
  created_at: string
}

export const getSkillExecutions = async (
  skillId: string,
  options: { limit?: number; offset?: number } = {},
): Promise<SkillExecutionRecord[]> => {
  const response = await apiClient.get<ApiResponse<SkillExecutionRecord[]>>(
    `/skills/${skillId}/executions`,
    { params: { limit: options.limit ?? 20, offset: options.offset ?? 0 } },
  )
  return response.data.data
}

