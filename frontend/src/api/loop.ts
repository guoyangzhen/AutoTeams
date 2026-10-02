import apiClient from './client'
import { ApiResponse } from '@/types'

export interface RAGTrendPoint {
  date: string
  faithfulness: number
  answer_relevancy: number
  context_precision: number
  context_recall: number
  count: number
}

export interface RAGLatest {
  id: string
  agent_id: string
  conversation_id: string
  message_id: string
  faithfulness: number
  answer_relevancy: number
  context_precision: number
  context_recall: number
  details: Record<string, unknown>
  evaluated_at: string
}

export interface OptimizationHistoryItem {
  id: string
  agent_id: string
  type: 'feedback' | 'gap' | 'optimization'
  input: Record<string, unknown> | null
  output: Record<string, unknown> | null
  applied: boolean
  applied_at: string | null
  created_at: string | null
}

export interface AgentVersionSnapshot {
  id: string
  agent_id: string
  version: string
  config_snapshot: {
    name?: string
    description?: string
    system_prompt?: string
    skills?: string[]
    params?: Record<string, unknown>
    config?: Record<string, unknown>
  }
  changelog: string | null
  is_active: boolean
  created_at: string | null
}

export interface LoopInsights {
  agent_id: string
  days: number
  optimizations: OptimizationHistoryItem[]
  versions: AgentVersionSnapshot[]
  rag_trend: RAGTrendPoint[]
  rag_latest: RAGLatest | null
  // R5: 原 /feedback、/knowledge-gaps 已聚合到 /insights
  feedback_analysis: FeedbackAnalysis
  knowledge_gaps: KnowledgeGaps
}

export interface FeedbackAnalysis {
  total_feedback: number
  satisfaction_rate: number
  dissatisfied_count: number
  issues: Array<{
    reason: string
    expected_info?: string
    improvement?: string
    priority: string
  }>
}

export interface KnowledgeGaps {
  total_questions: number
  top_keywords: Array<{ keyword: string; count: number }>
  gaps: string[]
  suggestions: string[]
  priority_questions: string[]
}

export interface LoopStatus {
  agent_id: string
  total_optimizations: number
  recent_optimizations: OptimizationHistoryItem[]
  status: string
}

// P0-1b: 监控统计聚合数据
export interface LoopStats {
  trend: Array<{ time: string; count: number }>
  response_time: Array<{ range: string; count: number }>
  satisfaction_pie: Array<{ name: string; value: number; color: string }>
  recent_sessions: Array<{
    id: string
    user: string
    startTime: string
    messages: number
    responseTime: string
    satisfaction: number | null
    status: 'completed' | 'active' | 'interrupted'
  }>
  error_logs: Array<{
    timestamp: string
    level: 'ERROR' | 'WARN' | 'INFO'
    message: string
    detail?: string
  }>
  alerts: Array<{
    dot: string
    title: string
    desc: string
    time: string
  }>
}

export const optimizeRetrieval = async (agentId: string, query: string, feedback: string): Promise<Record<string, unknown>> => {
  const response = await apiClient.post<ApiResponse<Record<string, unknown>>>(`/loop/${agentId}/optimize`, {
    query,
    feedback
  })
  return response.data.data
}

export const getLoopStatus = async (agentId: string): Promise<LoopStatus> => {
  const response = await apiClient.get<ApiResponse<LoopStatus>>(`/loop/${agentId}/status`)
  return response.data.data
}

// P0-1b: 获取监控统计聚合数据
export const getLoopStats = async (agentId: string, days: number = 7): Promise<LoopStats> => {
  const response = await apiClient.get<ApiResponse<LoopStats>>(`/loop/${agentId}/stats`, {
    params: { days }
  })
  return response.data.data
}

// P1-LOOP: 获取优化历史、版本快照、RAG 评估趋势
export const getLoopInsights = async (agentId: string, days: number = 30): Promise<LoopInsights> => {
  const response = await apiClient.get<ApiResponse<LoopInsights>>(`/loop/${agentId}/insights`, {
    params: { days }
  })
  return response.data.data
}

// O-08: 应用优化记录到 Agent 配置（打通 Loop 闭环，仅管理员）
export interface ApplyOptimizationResult {
  applied: boolean
  optimization_id: string
  version_snapshot_id: string
  agent_version: string
  summary: string
}

export const applyOptimization = async (agentId: string, optimizationId: string): Promise<ApplyOptimizationResult> => {
  const response = await apiClient.post<ApiResponse<ApplyOptimizationResult>>(`/loop/${agentId}/apply/${optimizationId}`)
  return response.data.data
}
