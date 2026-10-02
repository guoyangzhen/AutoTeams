/**
 * 认知层（Cognition）API 客户端 — 对应 spec.md §10.7 WT1 端点。
 *
 * 端点契约（/api/v1/cognition/*）：
 * - GET /cognition/knowledge-graph/{enterprise_id}  知识图谱
 * - GET /cognition/profile/{enterprise_id}          企业画像
 * - GET /cognition/operating-model/{enterprise_id}  运行模型
 */
import apiClient from './client'
import { ApiResponse } from '@/types'
import type { KnowledgeGraph, EnterpriseProfile, OperatingModel } from '@/types'
import {
  mockDelay,
  mockKnowledgeGraph,
  mockEnterpriseProfile,
  mockOperatingModel,
} from './mockData'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** 知识图谱（GET /cognition/knowledge-graph/{enterprise_id}） */
export async function getKnowledgeGraph(enterpriseId: string): Promise<KnowledgeGraph> {
  if (USE_MOCK) {
    return mockDelay({ ...mockKnowledgeGraph })
  }
  const response = await apiClient.get<ApiResponse<KnowledgeGraph>>(
    `/cognition/knowledge-graph/${enterpriseId}`,
  )
  return response.data.data
}

/** 企业画像（GET /cognition/profile/{enterprise_id}） */
export async function getEnterpriseProfile(enterpriseId: string): Promise<EnterpriseProfile> {
  if (USE_MOCK) {
    return mockDelay({ ...mockEnterpriseProfile })
  }
  const response = await apiClient.get<ApiResponse<EnterpriseProfile>>(
    `/cognition/profile/${enterpriseId}`,
  )
  return response.data.data
}

/** 运行模型（GET /cognition/operating-model/{enterprise_id}） */
export async function getOperatingModel(enterpriseId: string): Promise<OperatingModel> {
  if (USE_MOCK) {
    return mockDelay({ ...mockOperatingModel })
  }
  const response = await apiClient.get<ApiResponse<OperatingModel>>(
    `/cognition/operating-model/${enterpriseId}`,
  )
  return response.data.data
}

// ============================================================
// UI v4 新增：企业生命体征（驾驶舱 VitalPulse 数据源）
// ============================================================

/**
 * 企业生命体征聚合结果。
 *
 * 所有字段均为真实统计 —— 未运转的企业会得到 0 值与平直心电线，
 * 这是本次重构刻意保留的诚实性（不做假数据兜底）。
 */
export interface EnterpriseVitals {
  agents: {
    total: number
    production: number
    training: number
    recruit: number
    stage_distribution: Record<string, number>
  }
  events: {
    last_hour: number
    last_24h: number
    total: number
    failed: number
    /** 每小时事件速率 → 驱动心跳频率 */
    per_hour: number
  }
  approvals: { pending: number }
  shadow: {
    autonomous: number
    total: number
    evaluated: number
    match: number
    /** 真实信任度 = 一致判定 / 已评估；无样本时 null（前端显示「样本不足」） */
    trust_score: number | null
  }
  compile: {
    running: boolean
    job_id: string | null
    stage: string | null
    progress: number | null
    last_completed_at: string | null
    last_completeness: number | null
  }
  health: {
    score: number
    /** 健康语义 → 驱动波形颜色 */
    tone: 'alive' | 'alert' | 'fault' | 'idle'
  }
  collected_at: string
}

/**
 * 企业生命体征（GET /cognition/vitals/{enterprise_id}）。
 *
 * 驾驶舱首屏一次拿全运转状态，替代此前并发 6 个接口再前端拼装。
 */
export async function getEnterpriseVitals(enterpriseId: string): Promise<EnterpriseVitals> {
  const response = await apiClient.get<ApiResponse<EnterpriseVitals>>(
    `/cognition/vitals/${enterpriseId}`,
  )
  return response.data.data
}
