import apiClient from './client'

export interface ChannelAccountItem {
  id: string
  channel_type: string
  name: string
  description?: string
  mounted_profile_ids: string[]
  default_profile_id?: string
  status: string
  is_active: boolean
  created_at: string
}

export async function listChannelAccounts(): Promise<ChannelAccountItem[]> {
  const resp = await apiClient.get('/connectors/accounts')
  return resp.data.data
}

export async function createChannelAccount(payload: {
  channel_type: string
  name: string
  description?: string
  credentials: Record<string, any>
  mounted_profile_ids?: string[]
  default_profile_id?: string
}): Promise<{ id: string; name: string }> {
  const resp = await apiClient.post('/connectors/accounts', payload)
  return resp.data.data
}

export async function updateChannelAccount(id: string, payload: {
  name?: string
  description?: string
  credentials?: Record<string, any>
  mounted_profile_ids?: string[]
  default_profile_id?: string
  is_active?: boolean
}): Promise<{ id: string; name: string }> {
  const resp = await apiClient.put(`/connectors/accounts/${id}`, payload)
  return resp.data.data
}

export async function deleteChannelAccount(id: string): Promise<void> {
  await apiClient.delete(`/connectors/accounts/${id}`)
}

export async function generateBindToken(): Promise<{ bind_token: string; expires_in_seconds: number; instruction: string }> {
  const resp = await apiClient.post('/connectors/bind/generate')
  return resp.data.data
}

export async function simulateInboundMessage(payload: {
  account_id: string
  channel_type: string
  external_user_id: string
  external_user_name?: string
  content: string
  message_id?: string
}): Promise<any> {
  const resp = await apiClient.post('/connectors/simulate/inbound', payload)
  return resp.data.data
}
