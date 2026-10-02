/**
 * Team-Matrix 多智能体协同矩阵接口。
 *
 * 严格对齐后端 `backend/app/api/team_matrix.py`（前缀 /teams）：
 * - GET  /teams                                  → WorkgroupTeam[]
 * - POST /teams                                  → { id, name }
 * - GET  /teams/{team_id}                        → WorkgroupTeam（含 config）
 * - GET  /teams/{team_id}/tasks?status=          → MatrixTask[]
 * - POST /teams/{team_id}/tasks                  → { id, title, status }
 * - POST /teams/{team_id}/tasks/{task_id}/bid    → { id, round, current_hp, statement }
 * - POST /teams/{team_id}/tasks/{task_id}/award  → { id, assignee, status }
 * - POST /teams/{team_id}/tasks/{task_id}/deliver→ { id, status }
 * - POST /teams/{team_id}/tasks/{task_id}/review → { id, status }
 * - GET  /teams/{team_id}/blackboard?topic=      → BlackboardEntry[]
 * - POST /teams/{team_id}/blackboard             → { id, topic }
 */
import apiClient from './client'

export interface WorkgroupTeam {
  id: string
  enterprise_id?: string
  name: string
  description?: string | null
  leader_profile_id: string
  member_profile_ids: string[]
  config?: Record<string, unknown>
  status: string
  created_at?: string | null
}

export type MatrixTaskStatus =
  | 'pending'
  | 'bidding'
  | 'in_progress'
  | 'review'
  | 'done'
  | 'rework'
  | 'escalated'

export interface MatrixTask {
  id: string
  team_id?: string
  parent_task_id?: string | null
  title: string
  description: string
  priority: number
  status: MatrixTaskStatus
  suggested_profile_id?: string | null
  assignee_profile_id?: string | null
  deliverable_report?: Record<string, unknown> | null
  review_feedback?: Record<string, unknown> | null
  created_at?: string | null
}

/** 竞标提交结果（后端返回结算后的剩余 HP）。 */
export interface TaskBidResult {
  id: string
  round: number
  current_hp: number
  statement: string
}

export interface BlackboardEntry {
  id: string
  team_id?: string
  topic: string
  content: string
  source_profile_id: string
  source_task_id?: string | null
  citations: unknown[]
  is_pinned: boolean
  updated_at?: string | null
}

export async function listWorkgroupTeams(): Promise<WorkgroupTeam[]> {
  const resp = await apiClient.get('/teams')
  return resp.data.data
}

export async function getWorkgroupTeam(teamId: string): Promise<WorkgroupTeam> {
  const resp = await apiClient.get(`/teams/${teamId}`)
  return resp.data.data
}

export async function createWorkgroupTeam(payload: {
  name: string
  leader_profile_id: string
  member_profile_ids: string[]
  description?: string
}): Promise<{ id: string; name: string }> {
  const resp = await apiClient.post('/teams', payload)
  return resp.data.data
}

export async function listMatrixTasks(
  teamId: string,
  status?: MatrixTaskStatus,
): Promise<MatrixTask[]> {
  const resp = await apiClient.get(`/teams/${teamId}/tasks`, {
    params: status ? { status } : undefined,
  })
  return resp.data.data
}

export async function createMatrixTask(
  teamId: string,
  payload: {
    title: string
    description: string
    priority?: number
    parent_task_id?: string
    suggested_profile_id?: string
  },
): Promise<{ id: string; title: string; status: string }> {
  const resp = await apiClient.post(`/teams/${teamId}/tasks`, payload)
  return resp.data.data
}

/** 候选数字员工提交一轮竞标陈述，引擎按 HP_r = HP_{r-1} - (10 - score) × 3 结算。 */
export async function submitCandidateBid(
  teamId: string,
  taskId: string,
  payload: {
    candidate_profile_id: string
    bid_round: number
    statement: string
    score: number
    score_rationale?: string
  },
): Promise<TaskBidResult> {
  const resp = await apiClient.post(`/teams/${teamId}/tasks/${taskId}/bid`, payload)
  return resp.data.data
}

export async function awardTask(
  teamId: string,
  taskId: string,
  winnerProfileId: string,
): Promise<{ id: string; assignee: string; status: string }> {
  const resp = await apiClient.post(`/teams/${teamId}/tasks/${taskId}/award`, {
    winner_profile_id: winnerProfileId,
  })
  return resp.data.data
}

export async function deliverTask(
  teamId: string,
  taskId: string,
  reportData: Record<string, unknown>,
): Promise<{ id: string; status: string }> {
  const resp = await apiClient.post(`/teams/${teamId}/tasks/${taskId}/deliver`, {
    report_data: reportData,
  })
  return resp.data.data
}

export async function reviewTask(
  teamId: string,
  taskId: string,
  payload: { approved: boolean; feedback?: Record<string, unknown> },
): Promise<{ id: string; status: string }> {
  const resp = await apiClient.post(`/teams/${teamId}/tasks/${taskId}/review`, payload)
  return resp.data.data
}

export async function listBlackboardEntries(
  teamId: string,
  topic?: string,
): Promise<BlackboardEntry[]> {
  const resp = await apiClient.get(`/teams/${teamId}/blackboard`, {
    params: topic ? { topic } : undefined,
  })
  return resp.data.data
}

export async function postBlackboardEntry(
  teamId: string,
  payload: {
    topic: string
    content: string
    source_profile_id: string
    source_task_id?: string
    citations?: Array<Record<string, unknown>>
    is_pinned?: boolean
  },
): Promise<{ id: string; topic: string }> {
  const resp = await apiClient.post(`/teams/${teamId}/blackboard`, payload)
  return resp.data.data
}
