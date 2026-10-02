/**
 * 本地路径授权 API 客户端 —— 协作工作台与本地工具桥接。
 *
 * 安全策略：本地桥接仅提供授权目录内的 list/read/write/delete 文件操作；不提供
 * 通用命令、包管理器、解释器或 Agentic CLI 的远程执行入口。
 */
import apiClient from './client'

// ============================================================
// 类型定义（与 backend/app/schemas/local_path.py 对齐）
// ============================================================

export interface LocalPathGrant {
  id: string
  enterprise_id: string
  user_id: string
  label: string | null
  local_path: string
  scope: string
  status: 'pending' | 'connected' | 'offline' | 'revoked'
  /**
   * 一次性配对令牌是否已被本机「认领」消费（服务端在 claim 时即置位，早于 connected）。
   *
   * 可选字段：分阶段上线期间旧版服务端不返回它，缺失一律按「未认领」处理，
   * 此时 pending 仍只是候选状态——服务端可能在下次刷新前改写。
   * 只暴露这一个布尔信号，不暴露 token / 哈希 / 过期时间等任何凭据材料。
   */
  claimed?: boolean
  runner_id: string | null
  tool_manifest: {
    tools?: string[]
    maxFileBytes?: number
    executionPolicy?: 'file_operations_only'
  } | null
  resolved_path: string | null
  created_at: string
  updated_at: string
}

/** 本地已就绪工具：只有经 Runner 强制路径约束的文件操作。 */
export interface LocalTool {
  name: 'read' | 'write' | 'delete'
  label: string
  group: 'file'
}

/** 将授权记录的工具清单展开为可展示的本地文件操作列表。 */
export function expandLocalTools(manifest: LocalPathGrant['tool_manifest']): LocalTool[] {
  const tools: LocalTool[] = []
  const fileNames = manifest?.tools || []
  if (fileNames.includes('list') || fileNames.includes('read')) {
    tools.push({ name: 'read', label: '读取文件', group: 'file' })
  }
  if (fileNames.includes('write')) tools.push({ name: 'write', label: '写入文件', group: 'file' })
  if (fileNames.includes('delete')) tools.push({ name: 'delete', label: '删除文件', group: 'file' })
  return tools
}

export interface RegisterLocalPathResult {
  grant: LocalPathGrant
  setup_token: string
  setup_command: string
  setup_token_expires_at: string
}

/** 本地任务执行结果（协作服务经 /bridge 下发给本地 Runner 后返回）。 */
export interface LocalTaskResult {
  ok?: boolean
  type?: string
  entries?: Array<{ path: string; type: 'file' | 'dir'; size?: number }>
  path?: string
  content?: string
  bytes?: number
  error?: string
}

// ============================================================
// API 方法
// ============================================================

/** 注册本地路径授权，返回一次性 setup token 与可直接复制的连接命令。 */
export async function registerLocalPath(req: {
  local_path: string
  label?: string
  scope?: 'read' | 'read_write'
}): Promise<RegisterLocalPathResult> {
  const { data } = await apiClient.post<{ data: RegisterLocalPathResult }>('/local-paths/register', req)
  return data.data
}

/** 列出当前企业下可见的授权记录。 */
export async function listLocalPaths(): Promise<LocalPathGrant[]> {
  const { data } = await apiClient.get<{ data: LocalPathGrant[] }>('/local-paths')
  return data.data
}

/** 获取单个授权记录。 */
export async function getLocalPath(grantId: string): Promise<LocalPathGrant> {
  const { data } = await apiClient.get<{ data: LocalPathGrant }>(`/local-paths/${grantId}`)
  return data.data
}

/** 撤销授权。 */
export async function revokeLocalPath(grantId: string): Promise<void> {
  await apiClient.delete(`/local-paths/${grantId}`)
}

/** 驱动本地守护进程执行受限文件操作。 */
export async function runLocalTask(
  grantId: string,
  task: {
    tool: 'list' | 'read' | 'write' | 'delete'
    path?: string
    content?: string | null
  },
): Promise<LocalTaskResult> {
  const { data } = await apiClient.post<{ data: LocalTaskResult }>(
    `/local-paths/${grantId}/run`,
    task,
    { timeout: 150000 },
  )
  return data.data
}
