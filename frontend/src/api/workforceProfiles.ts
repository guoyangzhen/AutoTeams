import apiClient from './client'

export interface DutyBoundaries {
  allowed: string[]
  forbidden: string[]
}

export interface WorkforceProfile {
  id: string
  enterprise_id: string
  agent_id?: string
  employee_badge: string
  display_name: string
  job_title: string
  department: string
  duty_boundaries: DutyBoundaries
  tone_style: string
  authorized_flows: string[]
  accessible_knowledge_buckets: string[]
  authorized_tools: string[]
  /**
   * 聘用状态。后端 schema 文档为 shadow/active/suspended/retired，
   * 进化流程会额外写入 production，种子数据使用 probation —— 取并集以免漏标。
   */
  employment_status: 'shadow' | 'probation' | 'production' | 'active' | 'suspended' | 'retired'
  performance_score: number
  avatar_url?: string
  created_at: string
  updated_at: string
}

export interface CreateWorkforceProfilePayload {
  display_name: string
  job_title: string
  department?: string
  duty_boundaries?: DutyBoundaries
  tone_style?: string
  authorized_flows?: string[]
  accessible_knowledge_buckets?: string[]
  authorized_tools?: string[]
  employment_status?: string
  performance_score?: number
  /**
   * 关联的底层 Agent ID。绑定后该数字员工才能进入 /chat/:agentId 对话。
   * 后端 `WorkforceProfileUpdate` 不含此字段，因此只能在创建时绑定。
   */
  agent_id?: string
}

/** PUT accepts only WorkforceProfileUpdate fields; agent binding and identity are immutable here. */
export interface UpdateWorkforceProfilePayload {
  display_name?: string
  job_title?: string
  department?: string
  duty_boundaries?: DutyBoundaries
  tone_style?: string
  authorized_flows?: string[]
  accessible_knowledge_buckets?: string[]
  authorized_tools?: string[]
  employment_status?: WorkforceProfile['employment_status']
  performance_score?: number
  avatar_url?: string | null
}

export async function listWorkforceProfiles(): Promise<WorkforceProfile[]> {
  const resp = await apiClient.get('/workforce-profiles')
  return resp.data.data
}

export async function getWorkforceProfile(id: string): Promise<WorkforceProfile> {
  const resp = await apiClient.get(`/workforce-profiles/${id}`)
  return resp.data.data
}

export async function createWorkforceProfile(payload: CreateWorkforceProfilePayload): Promise<WorkforceProfile> {
  const resp = await apiClient.post('/workforce-profiles', payload)
  return resp.data.data
}

export async function updateWorkforceProfile(id: string, payload: UpdateWorkforceProfilePayload): Promise<WorkforceProfile> {
  const resp = await apiClient.put(`/workforce-profiles/${id}`, payload)
  return resp.data.data
}

export async function deleteWorkforceProfile(id: string): Promise<void> {
  await apiClient.delete(`/workforce-profiles/${id}`)
}

export async function checkDutyBoundary(id: string, actionIntent: string): Promise<{ allowed: boolean; reason: string; matched_boundary?: string }> {
  const resp = await apiClient.post(`/workforce-profiles/${id}/check-boundary`, { action_intent: actionIntent })
  return resp.data.data
}
