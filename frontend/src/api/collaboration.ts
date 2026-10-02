/**
 * 协作（Collaboration）API 客户端 — 对应 spec.md §10.7 WT4 端点。
 *
 * 端点契约（/api/v1/collaboration/*）：
 * - POST /collaboration/events                       发布事件
 * - GET  /collaboration/events                       查询事件（分页）
 * - POST /collaboration/approvals/{id}/approve       审批通过
 * - POST /collaboration/approvals/{id}/reject        审批拒绝
 * - POST /collaboration/rollback                      触发回滚
 */
import apiClient from './client'
import { ApiResponse, PaginatedResponse } from '@/types'
import type {
  CollaborationEvent,
  PublishEventRequest,
  PublishEventResponse,
  ApprovalRequest,
  ApprovalApproveResponse,
  ApprovalRejectRequest,
  ApprovalRejectResponse,
  CollaborationRollbackRequest,
  CollaborationRollbackResponse,
} from '@/types'
import {
  mockDelay,
  mockCollaborationEvents,
  mockApprovalRequest,
  MOCK_ENTERPRISE_ID,
} from './mockData'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** 发布事件（POST /collaboration/events） */
export async function publishEvent(req: PublishEventRequest): Promise<PublishEventResponse> {
  if (USE_MOCK) {
    // status 对齐后端 EventStatusLiteral：'processed' 表示已处理
    return mockDelay({ event_id: `evt-${Date.now()}`, status: 'processed' })
  }
  const response = await apiClient.post<ApiResponse<PublishEventResponse>>(
    '/collaboration/events',
    req,
  )
  return response.data.data
}

/** 查询事件（GET /collaboration/events?enterprise_id=&type=&limit=&offset=） */
export async function listEvents(
  enterpriseId: string,
  options: { type?: string; limit?: number; offset?: number } = {},
): Promise<PaginatedResponse<CollaborationEvent>> {
  const { type, limit = 50, offset = 0 } = options
  if (USE_MOCK) {
    const items = type
      ? mockCollaborationEvents.filter((e) => e.event_type === type)
      : mockCollaborationEvents
    return mockDelay({ items, total: items.length })
  }
  const response = await apiClient.get<ApiResponse<PaginatedResponse<CollaborationEvent>>>(
    '/collaboration/events',
    { params: { enterprise_id: enterpriseId, type, limit, offset } },
  )
  return response.data.data
}

// ============================================================
// UI v4 新增：协作事件流 SSE（驾驶舱「实时运转」数据源）
// ============================================================

/** 事件流 SSE 单帧负载 */
export interface EventStreamFrame {
  /** 增量新事件（首帧为最近 30 条建立上下文） */
  events?: CollaborationEvent[]
  /** 生命体征快照（仅在变化时下发，减少流量） */
  vitals?: unknown | null
  /** 首帧标记：前端应整体替换而非追加 */
  full_refresh?: boolean
  done?: boolean
  timeout?: boolean
  error?: string
}

/**
 * 协作事件流 SSE 端点路径（GET /collaboration/events/stream）。
 *
 * 修复审计问题：驾驶舱标称「实时运转」、带脉冲绿点，实际零轮询。
 * 前端可无缝降级到 listEvents 轮询（弱网/路演现场保命路径）。
 */
export function eventStreamPath(enterpriseId: string): string {
  return `/collaboration/events/stream?enterprise_id=${encodeURIComponent(enterpriseId)}`
}

/** 后端 ApprovalGateView（GET /collaboration/approvals 返回项） */
export interface ApprovalGateView {
  id: string
  enterprise_id: string
  process_id?: string
  node_id?: string
  agent_id?: string | null
  status: 'pending' | 'approved' | 'rejected'
  approver_id?: string | null
  decided_at?: string | null
  created_at?: string
  /** 后端附加的用户友好字段（list_approvals 返回） */
  agent_name?: string | null
  node_label?: string | null
}

/**
 * 获取待审批请求。
 *
 * 直接调用后端审批门列表（GET /collaboration/approvals?status=pending），
 * 返回项的 id 即 gate_id，批准/拒绝可直接使用。
 * 修复审计问题：此前从事件流合成 id（quotation_id/event_id）与后端期望的
 * gate_id 不一致，导致批准/拒绝报 NOT_FOUND。
 */
export async function getPendingApprovals(enterpriseId: string): Promise<ApprovalRequest[]> {
  if (USE_MOCK) {
    return mockDelay([
      { ...mockApprovalRequest, enterprise_id: enterpriseId || MOCK_ENTERPRISE_ID },
    ])
  }
  const response = await apiClient.get<ApiResponse<PaginatedResponse<ApprovalGateView>>>(
    '/collaboration/approvals',
    { params: { enterprise_id: enterpriseId, status: 'pending', limit: 50, offset: 0 } },
  )
  return response.data.data.items.map((g) => ({
    id: g.id,
    enterprise_id: enterpriseId,
    title: g.process_id ? `业务流程审批` : `协作审批事项`,
    description: g.node_label
      ? `审批节点：${g.node_label}`
      : g.node_id
        ? `审批节点：${g.node_id}`
        : '',
    requester_id: g.agent_id || '',
    // 优先用后端返回的中文申请人名，避免暴露原始 Agent ID（UUID）
    requester_name: g.agent_name || g.agent_id || '',
    approval_type: 'process' as const,
    context: {},
    status: g.status || 'pending',
    created_at: g.created_at || '',
    process_id: g.process_id,
    node_id: g.node_id,
    agent_id: g.agent_id,
    approver_id: g.approver_id,
    decided_at: g.decided_at,
  }))
}

function isHttpNotFound(err: unknown): boolean {
  if (err && typeof err === 'object' && 'response' in err) {
    const res = err.response
    if (res && typeof res === 'object' && 'status' in res) {
      return res.status === 404
    }
  }
  return false
}

/** 审批通过（POST /collaboration/approval-gates/{id}/approve 或 /approvals/{id}/approve） */
export async function approveRequest(
  id: string,
  comment?: string,
): Promise<ApprovalApproveResponse> {
  if (USE_MOCK) {
    return mockDelay({ approved: true, process_resumed: true })
  }
  try {
    const response = await apiClient.post<ApiResponse<ApprovalApproveResponse>>(
      `/collaboration/approval-gates/${id}/approve`,
      { comment },
    )
    return response.data.data
  } catch (err: unknown) {
    if (isHttpNotFound(err)) {
      const fallback = await apiClient.post<ApiResponse<ApprovalApproveResponse>>(
        `/collaboration/approvals/${id}/approve`,
        { comment },
      )
      return fallback.data.data
    }
    throw err
  }
}

/** 审批拒绝（POST /collaboration/approval-gates/{id}/reject 或 /approvals/{id}/reject） */
export async function rejectRequest(
  id: string,
  req: ApprovalRejectRequest,
): Promise<ApprovalRejectResponse> {
  if (USE_MOCK) {
    return mockDelay({ rejected: true })
  }
  try {
    const response = await apiClient.post<ApiResponse<ApprovalRejectResponse>>(
      `/collaboration/approval-gates/${id}/reject`,
      req,
    )
    return response.data.data
  } catch (err: unknown) {
    if (isHttpNotFound(err)) {
      const fallback = await apiClient.post<ApiResponse<ApprovalRejectResponse>>(
        `/collaboration/approvals/${id}/reject`,
        req,
      )
      return fallback.data.data
    }
    throw err
  }
}

/** 触发回滚（POST /collaboration/rollback） */
export async function rollback(
  req: CollaborationRollbackRequest,
): Promise<CollaborationRollbackResponse> {
  if (USE_MOCK) {
    return mockDelay({ rolled_back: true, agent_id: 'agent-sales-001' })
  }
  const response = await apiClient.post<ApiResponse<CollaborationRollbackResponse>>(
    '/collaboration/rollback',
    req,
  )
  return response.data.data
}

/** 演示案例所需的角色 Agent ID 映射 */
export interface DemoCaseAgentIds {
  sales_agent_id: string
  product_expert_agent_id: string
  finance_agent_id: string
  customer_service_agent_id: string
  after_sales_agent_id: string
}

/** 演示案例结果（POST /collaboration/events/demo-case） */
export interface DemoCaseResult {
  events: string[]
  approval_gate_id: string
  completed: boolean
  steps: number
}

/**
 * 触发 7 步演示案例（POST /collaboration/events/demo-case）。
 *
 * 生成询盘→报价→审批→成交→售后的完整事件流，让"今日 AI 公司"页有真实运转数据。
 * 后端要求以 query 参数传入 5 个角色 Agent ID。
 */
export async function triggerDemoCase(
  enterpriseId: string,
  agentIds: DemoCaseAgentIds,
  approvalMode: 'manual' | 'auto' = 'manual',
): Promise<DemoCaseResult> {
  if (USE_MOCK) {
    return mockDelay({ events: [], approval_gate_id: '', completed: true, steps: 7 })
  }
  const response = await apiClient.post<ApiResponse<DemoCaseResult>>(
    '/collaboration/events/demo-case',
    {},
    {
      params: {
        enterprise_id: enterpriseId,
        ...agentIds,
        approval_mode: approvalMode,
      },
    },
  )
  return response.data.data
}
