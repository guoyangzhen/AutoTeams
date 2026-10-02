/**
 * MCP 工具生态 API 客户端。
 *
 * 对应后端 backend/app/api/mcp_evolution.py（挂载于 /api/v1）：
 * - GET  /mcp/servers  查询已连接的 MCP 服务器
 * - GET  /mcp/tools    查询全部 MCP 工具契约
 * - POST /mcp/call     试调用指定服务器的工具
 *
 * 契约字段与 backend/app/services/mcp/schema.py 保持一致。
 */
import apiClient from './client'

/**
 * 端侧（Local Runner）工具的调用前置校验。
 *
 * 后端契约（backend/app/services/mcp/runner_bridge.py）已收紧：
 * - `runner_read_file` 必须给出 `grant_id`（用户自己的本地路径授权记录）与
 *   `relative_path`；后端**不再**读取自身文件系统；
 * - 旧参数 `device_id` 与 `file_path` 都已废弃：授权目录、授权范围与"守护进程
 *   是否在线"都记录在授权上，用设备 ID 去字符串匹配 `runner_id` 既不唯一，
 *   也不是可验证的身份链；
 * - `runner_execute_script` / `runner_system_probe` 尚未接入真实端侧通道，调用必然失败。
 *
 * 前端在发请求前先拦一次，避免把已废弃的入参发出去，也避免把「未接入 / 未实现」
 * 当成执行成功展示给用户。
 */
const RUNNER_TOOL_REQUIRED_ARGS: Record<string, string[]> = {
  runner_read_file: ['grant_id', 'relative_path'],
}

/** 后端保留但未实现的端侧工具：直接给出原因，不发无意义的请求。 */
const UNSUPPORTED_RUNNER_TOOLS: Record<string, string> = {
  runner_execute_script: '未实现：端侧脚本执行尚未接入 Local Runner 授权执行器',
  runner_system_probe: '未实现：端侧设备探针尚未接入真实探测通道',
}

/** 授权目录内的相对路径；绝对路径、盘符/UNC、任何 `..` 段一律拒绝。 */
function isDeviceRelativePath(value: string): boolean {
  const path = value.trim()
  if (!path) return false
  if (path.startsWith('/') || path.startsWith('\\')) return false
  if (/^[A-Za-z]:/.test(path)) return false
  // 任何向上跳转段都拒绝：与后端一致，不做"归一化后不越界"的宽松处理。
  return !path.split(/[\\/]+/).includes('..')
}

/**
 * 校验端侧工具入参。不通过时抛出可读错误，由调用方展示为「未接入 / 未实现」状态。
 * 非端侧工具不做任何额外校验，原样透传。
 */
export function assertRunnerToolArguments(
  toolName: string,
  args: Record<string, unknown>,
): void {
  const unsupported = UNSUPPORTED_RUNNER_TOOLS[toolName]
  if (unsupported) {
    throw new Error(`${toolName} ${unsupported}`)
  }

  const required = RUNNER_TOOL_REQUIRED_ARGS[toolName]
  if (!required) return

  if ('file_path' in args && !('relative_path' in args)) {
    throw new Error(
      `${toolName} 不再接受 file_path：后端不会读取自身文件系统，请改用 relative_path（授权目录内的相对路径）`,
    )
  }

  const missing = required.filter((key) => {
    const value = args[key]
    return typeof value !== 'string' || value.trim() === ''
  })
  if (missing.length > 0) {
    throw new Error(`${toolName} 缺少必填参数：${missing.join('、')}（端侧文件读取必须绑定具体授权）`)
  }

  if (toolName === 'runner_read_file' && !isDeviceRelativePath(String(args.relative_path))) {
    throw new Error('路径不合法：仅接受设备授权目录内的相对路径（不接受绝对路径或 ..）')
  }
}

/** 传输协议：stdio 子进程 / sse 长连接 / runner_bridge 端侧桥接。 */
export type MCPTransport = 'stdio' | 'sse' | 'runner_bridge'

/** MCP 服务器配置（MCPServerConfig）。 */
export interface MCPServer {
  server_id: string
  name: string
  transport: MCPTransport
  command?: string | null
  args?: string[]
  env?: Record<string, string>
  url?: string | null
  is_active: boolean
  timeout_seconds: number
}

/** 工具参数 JSON Schema（MCPToolParameterSchema）。 */
export interface MCPToolInputSchema {
  type: string
  properties: Record<string, unknown>
  required: string[]
}

/** 工具契约（MCPToolDefinition + 后端附加的 server_id）。 */
export interface MCPTool {
  name: string
  description?: string | null
  inputSchema: MCPToolInputSchema
  server_id: string
}

/** 工具执行结果（MCPToolResult）。 */
export interface MCPToolResult {
  success: boolean
  data?: unknown
  error?: string | null
  execution_time_ms: number
}

/** 查询 MCP 服务器列表。 */
export async function listMCPServers(): Promise<MCPServer[]> {
  const resp = await apiClient.get('/mcp/servers')
  return resp.data.data
}

/** 查询全部 MCP 工具契约。 */
export async function listMCPTools(): Promise<MCPTool[]> {
  const resp = await apiClient.get('/mcp/tools')
  return resp.data.data
}

/** 试调用指定服务器的工具。 */
export async function callMCPTool(
  serverId: string,
  toolName: string,
  args: Record<string, unknown>,
): Promise<MCPToolResult> {
  assertRunnerToolArguments(toolName, args)
  const resp = await apiClient.post('/mcp/call', {
    server_id: serverId,
    tool_name: toolName,
    arguments: args,
  })
  return resp.data.data
}
