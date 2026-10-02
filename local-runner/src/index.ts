#!/usr/bin/env node
/**
 * AutoTeams 本地守护进程（Local Runner）CLI 入口
 *
 * 用法：
 *   autoteams-runner connect --server <bridge> --token <token> --grant <grant> --path <folder> [--scope read|read_write] [--label <name>]
 *
 * 示例（可直接从网页复制）：
 *   autoteams-runner connect --server ws://127.0.0.1:3001/bridge --token xxx --grant yyy --path "D:\\项目" --scope read_write
 */
import { start } from './client.js'

interface CliOptions {
  server: string
  token: string
  grant: string
  path: string
  scope: string
  [key: string]: string | undefined
}

/** 解析 --key value 形式的参数 */
function parseArgs(argv: string[]): CliOptions {
  const options: CliOptions = { server: '', token: '', grant: '', path: '', scope: 'read' }
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]
    if (arg.startsWith('--')) {
      const key = arg.slice(2)
      const next = argv[i + 1]
      if (next !== undefined && !next.startsWith('--')) {
        options[key] = next
        i++
      } else {
        options[key] = ''
      }
    }
  }
  return options
}

function printHelp(): void {
  // eslint-disable-next-line no-console
  console.log(`AutoTeams 本地守护进程（Local Runner）

用法:
  autoteams-runner connect --server <bridge地址> --token <token> --grant <授权ID> --path <本地文件夹> [--scope read|read_write] [--label <名称>] [--api <云端API地址> --device-id <设备ID> --runner-id <Runner ID> [--device-secret <设备凭据>] [--vault <本机凭据库路径>]]

参数:
  --server  云端桥接 WebSocket 地址（如 ws://127.0.0.1:3001/bridge）
  --token   授权时下发的一次性连接令牌
  --grant   本地路径授权 ID
  --path    要授权的本地文件夹绝对路径
  --scope   授权范围: read（只读，默认）或 read_write（文件读写）
  --label   该路径的友好名称（可选）

具身物理执行（Local Runner 2.0，可选）:
  --api            云端 REST 地址（如 http://127.0.0.1:8000）
  --device-id      已注册的设备 ID
  --runner-id      注册设备时指定的 Runner ID，必须与该设备一致
  --device-secret  设备长期凭据（建议通过 AUTOTEAMS_DEVICE_SECRET 环境变量提供）
  --vault          本机凭据库 JSON 路径；凭据只在本机解析，严禁随任务下发
  --physical       端侧作用域（默认 read,physical），仅 physical 才允许点击/键入

说明:
  在 AutoTeams 网页「本地连接」中注册本地路径后，复制生成的连接命令并在此运行即可。
  该命令会保持运行，将本机桥接到云端，云端 AI 即可在授权目录内安全地读写文件；
  先在云端注册物理设备；本地路径授权不会注册该设备。
  设置 --api、--device-id、--runner-id 与 AUTOTEAMS_DEVICE_SECRET 后，本机还会领取浏览器 / 桌面物理任务。
  也可通过 AUTOTEAMS_API_URL、AUTOTEAMS_DEVICE_ID、AUTOTEAMS_RUNNER_ID、AUTOTEAMS_DEVICE_SECRET 全部配置；
  所有点击与键入都受三重护栏实时审查，高危操作需在本机控制台现场确认。`)
}

const command = process.argv[2]
const rest = process.argv.slice(3)

if (command === 'connect') {
  const options = parseArgs(rest)
  if (!options.server || !options.token || !options.grant || !options.path) {
    // eslint-disable-next-line no-console
    console.error('缺少必要参数。运行 autoteams-runner --help 查看用法。')
    process.exit(1)
  }
  // 校验 scope 取值
  if (options.scope !== 'read' && options.scope !== 'read_write') {
    // eslint-disable-next-line no-console
    console.error('scope 只能是 read 或 read_write')
    process.exit(1)
  }
  if ('bridge-secret' in options || process.env.AUTOTEAMS_BRIDGE_SECRET !== undefined) {
    console.error('--bridge-secret / AUTOTEAMS_BRIDGE_SECRET 已停用；请使用设备 ID 与设备长期凭据（AUTOTEAMS_DEVICE_ID / AUTOTEAMS_DEVICE_SECRET）。')
    process.exit(1)
  }
  const apiUrl = options.api ?? process.env.AUTOTEAMS_API_URL
  const deviceId = options['device-id'] ?? process.env.AUTOTEAMS_DEVICE_ID
  const runnerId = options['runner-id'] ?? process.env.AUTOTEAMS_RUNNER_ID
  const deviceSecret = options['device-secret'] ?? process.env.AUTOTEAMS_DEVICE_SECRET
  const physicalRequested = [apiUrl, deviceId, runnerId, deviceSecret, options.vault, options.physical,
    process.env.AUTOTEAMS_CREDENTIAL_VAULT, process.env.AUTOTEAMS_PHYSICAL_SCOPES]
    .some((value) => value !== undefined)
  if (physicalRequested) {
    if (!apiUrl?.trim() || !deviceId?.trim() || !runnerId?.trim() || !deviceSecret?.trim()) {
      console.error('启用具身物理执行需要完整配置 --api / AUTOTEAMS_API_URL、--device-id / AUTOTEAMS_DEVICE_ID、--runner-id / AUTOTEAMS_RUNNER_ID 与 AUTOTEAMS_DEVICE_SECRET（或 --device-secret）。')
      process.exit(1)
    }
    if (apiUrl !== apiUrl.trim() || deviceId !== deviceId.trim() || runnerId !== runnerId.trim()) {
      console.error('物理设备地址和 ID 不得包含首尾空白。')
      process.exit(1)
    }
    try {
      const url = new URL(apiUrl)
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash || url.search) {
        throw new Error('invalid API address')
      }
    } catch {
      console.error('云端 REST 地址无效：请使用不含用户信息、查询串或片段的 http:// 或 https:// 地址。')
      process.exit(1)
    }
  }
  // 注入配置并启动
  // 将 grant / token 拼接到 /bridge 地址的查询串，供云端认领校验
  let bridgeUrl: URL
  try {
    bridgeUrl = new URL(options.server)
    if (!['ws:', 'wss:'].includes(bridgeUrl.protocol) || bridgeUrl.hash || bridgeUrl.username || bridgeUrl.password) {
      throw new Error('invalid bridge address')
    }
  } catch {
    console.error('桥接地址无效：请使用不含用户信息或片段的 ws:// 或 wss:// 地址。')
    process.exit(1)
  }
  bridgeUrl.searchParams.set('grant', options.grant)
  bridgeUrl.searchParams.set('token', options.token)
  process.env.AUTOTEAMS_BRIDGE_URL = bridgeUrl.toString()
  process.env.AUTOTEAMS_GRANT_PATH = options.path
  process.env.AUTOTEAMS_GRANT_SCOPE = options.scope
  if (physicalRequested) {
    process.env.AUTOTEAMS_API_URL = apiUrl!
    process.env.AUTOTEAMS_DEVICE_ID = deviceId!
    process.env.AUTOTEAMS_RUNNER_ID = runnerId!
    process.env.AUTOTEAMS_DEVICE_SECRET = deviceSecret!
    if (options.vault) process.env.AUTOTEAMS_CREDENTIAL_VAULT = options.vault
    if (options.physical) process.env.AUTOTEAMS_PHYSICAL_SCOPES = options.physical
  }
  start()
} else if (command === '--help' || command === '-h' || command === 'help' || !command) {
  printHelp()
} else {
  // eslint-disable-next-line no-console
  console.error(`未知命令: ${command}`)
  printHelp()
  process.exit(1)
}
