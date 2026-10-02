import apiClient from './client'
import { ApiResponse } from '@/types'

/**
 * 业务效果指标（Business Metrics）API 客户端。
 *
 * 对应后端 GET /metrics/business，聚合效率/成本/覆盖/质量/趋势五类指标，
 * 供「业务效果仪表盘」与 Home 顶部 KPI 概览使用。
 *
 * 数据口径（与后端 metrics_service 保持一致，前端 tooltip 可引用）：
 * - 协作量：conversations 表按日聚合
 * - 解决率：会话末位满意度非负向视为已解决
 * - 知识覆盖率：已获得 assistant 回复的 user 问题数 / user 问题总数
 * - token 成本：assistant 消息 token_count 之和 × 单价（元/千 token）
 * - ROI 节省人力：已解决会话数 × (人工耗时 - AI 耗时)
 */

/** 技能使用 Top5 条目 */
export interface SkillTopItem {
  skill_id: string
  name: string
  count: number
}

/** 每日趋势点 */
export interface DailyCountPoint {
  date: string
  count: number
}

export interface SatisfactionTrendPoint {
  date: string
  score: number
}

/** 业务效果指标聚合结果（与后端 metrics_service.get_business_metrics 返回结构一致） */
export interface BusinessMetrics {
  agent_id: string | null
  enterprise_id: string | null
  period: {
    start: string
    end: string
    range_days: number
  }
  summary: {
    total_conversations: number
    total_questions: number
    total_tokens: number
    resolved_count: number
  }
  efficiency: {
    resolution_rate: number
    escalation_rate: number
    avg_response_time_ms: number
    human_compare_minutes: number
    hours_saved: number
  }
  cost: {
    total_tokens: number
    /** 时间窗内已成功执行的工具调用次数 */
    tool_call_count: number
    total_cost_yuan: number
    per_conversation_cost_yuan: number
    estimated_monthly_cost_yuan: number
    pre_ai_cost_yuan: number
    cost_reduction_pct: number
    cost_per_1k_tokens_yuan: number
  }
  coverage: {
    knowledge_coverage: number
    total_questions: number
    answered_questions: number
    skill_top5: SkillTopItem[]
  }
  quality: {
    accuracy: number
    satisfaction_score: number
    satisfaction_rate: number
    rated_count: number
  }
  trends: {
    daily_counts: DailyCountPoint[]
    satisfaction_trend: SatisfactionTrendPoint[]
  }
}

/**
 * 获取业务效果指标。
 *
 * @param rangeDays 聚合时间窗口（天），后端限制 1-90
 * @param agentId 指定智能体；留空则聚合当前企业全部智能体
 */
export const getBusinessMetrics = async (
  rangeDays: number = 30,
  agentId?: string,
): Promise<BusinessMetrics> => {
  const response = await apiClient.get<ApiResponse<BusinessMetrics>>('/metrics/business', {
    params: { range_days: rangeDays, ...(agentId ? { agent_id: agentId } : {}) },
  })
  return response.data.data
}
