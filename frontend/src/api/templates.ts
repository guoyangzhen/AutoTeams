import apiClient from './client'
import { ApiResponse } from '@/types'

// B6: 数字员工能力模板
export interface AgentTemplate {
  id: string
  role: 'customer_service' | 'sales' | 'hr' | 'ops' | 'finance' | 'medical' | 'education' | 'legal' | string
  name: string
  description: string | null
  system_prompt: string
  skill_ids: string[]
  knowledge_structure: Record<string, unknown>
  sample_dialogues: Array<{ user: string; assistant: string }>
  is_preset: boolean
  enterprise_id: string | null
  created_at: string
  updated_at: string
}

export interface SkillTemplate {
  id: string
  code: string
  name: string
  skill_type: string
  description: string | null
  config: Record<string, unknown>
  output_schema: Record<string, unknown>
  is_preset: boolean
  created_at: string
  updated_at: string
}

export interface ApplyTemplateResult {
  agent_id: string
  template_id: string
  template_name: string
  system_prompt_applied: boolean
  skills_created: number
  build_status: string
  build_messages: string[]
}

export const getTemplates = async (role?: string): Promise<AgentTemplate[]> => {
  const response = await apiClient.get<ApiResponse<AgentTemplate[]>>('/templates', {
    params: role ? { role } : undefined,
  })
  return response.data.data
}

export const getSkillTemplates = async (): Promise<SkillTemplate[]> => {
  const response = await apiClient.get<ApiResponse<SkillTemplate[]>>('/templates/skill-templates')
  return response.data.data
}

/**
 * 套用模板创建 Agent。
 *
 * 该端点会同步跑完整条 LangGraph 构建链（扫描 → 解析 → 分块 → 向量化 → 装配技能
 * → 自检），单个真实文档目录经常超过 apiClient 默认的 60s 超时。
 * 超时并不会取消服务端的工作，只会让前端把「其实正在构建的 Agent」误报成失败，
 * 用户重试就会建出重复员工。因此这里默认给足 10 分钟。
 */
export const APPLY_TEMPLATE_TIMEOUT_MS = 10 * 60 * 1000

export const applyTemplate = async (
  templateId: string,
  payload: { folder_path: string; name_override?: string; description_override?: string },
  timeoutMs: number = APPLY_TEMPLATE_TIMEOUT_MS,
): Promise<ApplyTemplateResult> => {
  // 端点无尾斜杠（符合 hard constraint），路径为 /templates/{id}/apply
  const response = await apiClient.post<ApiResponse<ApplyTemplateResult>>(
    `/templates/${templateId}/apply`,
    payload,
    { timeout: timeoutMs },
  )
  return response.data.data
}

// B6: 企业私有模板「另存为」请求体
export interface CreateTemplatePayload {
  role: 'customer_service' | 'sales' | 'hr' | 'ops' | 'finance' | 'medical' | 'education' | 'legal'
  name: string
  description?: string
  system_prompt: string
  skill_ids?: string[]
  knowledge_structure?: Record<string, unknown>
  sample_dialogues?: Array<{ user: string; assistant: string }>
}

// B6: 企业私有模板「另存为」端点（对齐愿景蓝图 4.1.3）
// 端点无尾斜杠，路径为 /templates（与 GET /templates 同前缀但 HTTP 方法不同）
export const createPrivateTemplate = async (
  payload: CreateTemplatePayload
): Promise<AgentTemplate> => {
  const response = await apiClient.post<ApiResponse<AgentTemplate>>('/templates', payload)
  return response.data.data
}
