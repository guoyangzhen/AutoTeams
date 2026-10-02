/**
 * 桌面端后端地址解析。
 *
 * AUD-28：渲染层改由回环服务提供后，后端地址必须由主进程在**运行时**决定
 * （构建期内联的 VITE_* 改不了，装机用户也没有环境变量）。
 *
 * 优先级：用户配置文件 > 环境变量 > 本机默认。
 * 配置非法时**拒绝启动**并给出原因，不静默回落到别的后端——静默回落会让用户
 * 以为连的是自己配置的服务器，实际请求却打到了本机 8000 端口。
 */

import * as fs from 'node:fs'
import * as path from 'node:path'
import { parseUpstreamUrl, type UpstreamTarget } from './upstream.js'

/** 本机默认后端（与 frontend/vite.config.ts 的 dev proxy 目标一致）。 */
export const DEFAULT_API_URL = 'http://127.0.0.1:8000'
export const DEFAULT_COLLAB_URL = 'http://127.0.0.1:3001'

/** 用户配置文件名，位于 Electron userData 目录。 */
export const USER_CONFIG_FILE_NAME = 'desktop-config.json'

export type ConfigSource = 'user-file' | 'env' | 'default'

export interface DesktopConfig {
  api: UpstreamTarget
  collab: UpstreamTarget
  apiSource: ConfigSource
  collabSource: ConfigSource
}

/** 取非空字符串；缺失、非字符串、纯空白一律视为「未配置」。 */
function nonEmptyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : undefined
}

function configSource(userValue: string | undefined, envValue: unknown): ConfigSource {
  if (userValue !== undefined) return 'user-file'
  return nonEmptyString(envValue) === undefined ? 'default' : 'env'
}

/**
 * 读取用户配置里的 URL 字段。
 * 字段**存在**就必须是可用的非空字符串：把 123 或 "" 当成「没配」会静默连到
 * 本机默认后端，用户以为连的是自己的服务器，实际请求打去了别处。
 */
function readConfiguredUrl(source: Record<string, unknown>, key: string): string | undefined {
  if (!(key in source)) return undefined
  const value = source[key]
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`桌面端配置字段 ${key} 必须是非空字符串，收到：${JSON.stringify(value)}`)
  }
  return value.trim()
}

/**
 * 读取用户配置文件。文件缺失是正常情况（首次启动）；存在但结构不合法则拒绝启动。
 */
export function readUserConfigFile(filePath: string): Record<string, unknown> {
  let raw: string
  try {
    raw = fs.readFileSync(filePath, 'utf8')
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {}
    throw new Error(`读取桌面端配置文件失败（${filePath}）：${(error as Error).message}`)
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    throw new Error(`桌面端配置文件不是合法 JSON（${filePath}）：${(error as Error).message}`)
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`桌面端配置文件必须是 JSON 对象（${filePath}）`)
  }
  return parsed as Record<string, unknown>
}

export function resolveDesktopConfig(options: { env: NodeJS.ProcessEnv; userConfigPath: string }): DesktopConfig {
  const userConfig = readUserConfigFile(options.userConfigPath)
  const unknownKeys = Object.keys(userConfig).filter((key) => key !== 'apiUrl' && key !== 'collabUrl')
  if (unknownKeys.length > 0) {
    throw new Error(`桌面端配置存在未知字段：${unknownKeys.join(', ')}（仅支持 apiUrl / collabUrl）`)
  }

  const userApi = readConfiguredUrl(userConfig, 'apiUrl')
  const userCollab = readConfiguredUrl(userConfig, 'collabUrl')
  const envApi = nonEmptyString(options.env.AUTOTEAMS_API_URL)
  const envCollab = nonEmptyString(options.env.AUTOTEAMS_COLLAB_URL)

  return {
    api: parseUpstreamUrl(userApi ?? envApi ?? DEFAULT_API_URL, '后端地址（apiUrl）'),
    collab: parseUpstreamUrl(userCollab ?? envCollab ?? DEFAULT_COLLAB_URL, '协作服务地址（collabUrl）'),
    apiSource: configSource(userApi, envApi),
    collabSource: configSource(userCollab, envCollab),
  }
}

/** 配置文件在 userData 下的完整路径。 */
export function userConfigPath(userDataDir: string): string {
  return path.join(userDataDir, USER_CONFIG_FILE_NAME)
}
