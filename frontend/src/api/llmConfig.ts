import apiClient from './client'
import { ApiResponse } from '@/types'

/**
 * 模型 API 配置 API 客户端（设置页「模型 API 配置」区块）。
 *
 * 安全约定：后端只返回掩码密钥（openai_api_key_masked 等），
 * 前端不持有、也不展示明文密钥。
 */

export interface LLMConfigUpdate {
  openai_enabled: boolean
  openai_api_base?: string
  /** 传入非空则为新密钥；留空/省略则保留原密钥 */
  openai_api_key?: string
  openai_model?: string

  anthropic_enabled: boolean
  anthropic_api_base?: string
  anthropic_api_key?: string
  anthropic_model?: string
}

export interface LLMConfigView {
  enterprise_id: string
  openai_enabled: boolean
  openai_api_base: string | null
  openai_model: string | null
  openai_has_key: boolean
  openai_api_key_masked: string

  anthropic_enabled: boolean
  anthropic_api_base: string | null
  anthropic_model: string | null
  anthropic_has_key: boolean
  anthropic_api_key_masked: string

  updated_at: string | null
}

/** 读取企业模型 API 配置（掩码视图；未配置时返回 null） */
export async function getLLMConfig(enterpriseId: string): Promise<LLMConfigView | null> {
  const response = await apiClient.get<ApiResponse<LLMConfigView>>(`/llm-config/${enterpriseId}`)
  return response.data.data
}

/** 保存企业模型 API 配置（管理员） */
export async function upsertLLMConfig(
  enterpriseId: string,
  data: LLMConfigUpdate,
): Promise<LLMConfigView> {
  const response = await apiClient.put<ApiResponse<LLMConfigView>>(
    `/llm-config/${enterpriseId}`,
    data,
  )
  return response.data.data
}