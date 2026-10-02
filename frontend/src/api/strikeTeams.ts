/**
 * AutoTeams 5.0 动态敏捷特遣队接口（战役 1 · Contract Net Protocol 2.0）。
 *
 * 严格对齐后端 `backend/app/api/strike_teams.py`（前缀 /strike_teams）：
 * - GET  /strike_teams?status=&include_dissolved=  → StrikeTeam[]
 * - POST /strike_teams                            → StrikeTeam（forming）
 * - GET  /strike_teams/{id}                       → StrikeTeam
 * - POST /strike_teams/{id}/bid                   → StrikeTeam（抵押入池）
 * - POST /strike_teams/{id}/activate              → StrikeTeam（锁定资源租约）
 * - POST /strike_teams/{id}/subtasks/{sid}/lock?assignee_badge=   → StrikeTeam
 * - POST /strike_teams/{id}/subtasks/{sid}/deliver                → StrikeTeam
 * - POST /strike_teams/{id}/subtasks/{sid}/accept                 → StrikeTeam
 * - POST /strike_teams/{id}/review                → StrikeTeam（reviewing）
 * - POST /strike_teams/{id}/dissolve              → { team, settlement }
 *
 * 状态机：forming → active → reviewing → dissolved
 */
import apiClient from './client'

export type StrikeTeamStatus = 'forming' | 'active' | 'reviewing' | 'dissolved'

export type SubtaskStatus = 'pending' | 'locked' | 'delivered' | 'accepted'

export interface StrikeTeamMember {
  badge: string
  role: string
  stake_hp: number
  joined_at?: string | null
  rationale?: string
  /** 结算后该员工账面 HP（清算时回写） */
  settled_hp?: number
  /** refunded = 押金已返还；forfeited = 押金已罚没 */
  settlement?: 'refunded' | 'forfeited'
}

export interface SubtaskNode {
  id: string
  title: string
  status: SubtaskStatus
  assignee_badge?: string | null
  depends_on: string[]
  deliverable?: Record<string, unknown> | null
}

export interface SubtaskGraph {
  nodes: SubtaskNode[]
  edges: Array<{ from: string; to: string }>
}

export interface StrikeTeam {
  id: string
  enterprise_id: string
  name: string
  mission_statement: string
  initiator_badge: string
  status: StrikeTeamStatus
  allocated_compute_budget: number
  total_hp_stake: number
  shared_blackboard_id: string
  members: StrikeTeamMember[]
  subtask_graph: SubtaskGraph
  created_at?: string | null
  updated_at?: string | null
  expires_at?: string | null
}

/** 清算结算单：逐人押金去向，验收通过返还、失败罚没。 */
export interface StrikeTeamSettlement {
  team_id: string
  accepted: boolean
  total_refunded_hp: number
  total_forfeited_hp: number
  settlement_note: string
  deliverable: Record<string, unknown>
  settlements: Array<{
    badge: string
    role: string
    stake_hp: number
    refunded_hp: number
    forfeited_hp: number
  }>
  dissolved_at: string
}

export async function listStrikeTeams(params?: {
  status?: StrikeTeamStatus
  include_dissolved?: boolean
}): Promise<StrikeTeam[]> {
  const resp = await apiClient.get('/strike_teams', { params })
  return resp.data.data
}

export async function getStrikeTeam(teamId: string): Promise<StrikeTeam> {
  const resp = await apiClient.get(`/strike_teams/${teamId}`)
  return resp.data.data
}

export async function createStrikeTeam(payload: {
  name: string
  mission_statement: string
  initiator_badge: string
  initiator_role?: string
  initiator_stake_hp?: number
  allocated_compute_budget?: number
  subtask_graph?: SubtaskGraph
  expires_at?: string
}): Promise<StrikeTeam> {
  const resp = await apiClient.post('/strike_teams', payload)
  return resp.data.data
}

/** 带资竞标：抵押 HP 换取入场券，计入全队对赌池。 */
export async function submitStrikeTeamBid(
  teamId: string,
  payload: { badge: string; role: string; stake_hp: number; rationale?: string },
): Promise<StrikeTeam> {
  const resp = await apiClient.post(`/strike_teams/${teamId}/bid`, payload)
  return resp.data.data
}

/** 锁定资源租约：forming → active。 */
export async function activateStrikeTeam(teamId: string): Promise<StrikeTeam> {
  const resp = await apiClient.post(`/strike_teams/${teamId}/activate`)
  return resp.data.data
}

/** 锁入 DAG 子任务；前置依赖未验收时后端返回 409。 */
export async function lockStrikeSubtask(
  teamId: string,
  subtaskId: string,
  assigneeBadge: string,
): Promise<StrikeTeam> {
  const resp = await apiClient.post(
    `/strike_teams/${teamId}/subtasks/${subtaskId}/lock`,
    undefined,
    { params: { assignee_badge: assigneeBadge } },
  )
  return resp.data.data
}

export async function deliverStrikeSubtask(
  teamId: string,
  subtaskId: string,
  deliverable: Record<string, unknown>,
): Promise<StrikeTeam> {
  const resp = await apiClient.post(
    `/strike_teams/${teamId}/subtasks/${subtaskId}/deliver`,
    deliverable,
  )
  return resp.data.data
}

export async function acceptStrikeSubtask(teamId: string, subtaskId: string): Promise<StrikeTeam> {
  const resp = await apiClient.post(`/strike_teams/${teamId}/subtasks/${subtaskId}/accept`)
  return resp.data.data
}

/** 申请成果验收：active → reviewing（全部子任务验收后）。 */
export async function requestStrikeTeamReview(teamId: string): Promise<StrikeTeam> {
  const resp = await apiClient.post(`/strike_teams/${teamId}/review`)
  return resp.data.data
}

/** 交付验收并清算解散：押金原路返还或罚没。 */
export async function dissolveStrikeTeam(
  teamId: string,
  payload: { accepted: boolean; settlement_note?: string; deliverable?: Record<string, unknown> },
): Promise<{ team: StrikeTeam; settlement: StrikeTeamSettlement }> {
  const resp = await apiClient.post(`/strike_teams/${teamId}/dissolve`, payload)
  return resp.data.data
}
