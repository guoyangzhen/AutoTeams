/**
 * 模型可执行子进程的环境变量白名单（AUD-02 纵深防御）。
 *
 * 协作服务进程持有数据库、向量库、Bridge 共享密钥和模型 API Key；
 * SDK 的 bash 工具默认把整个 process.env 传给子进程，一旦模型可以执行任意命令，
 * 这些凭据就等价于「打印给我看」。这里改为**白名单**：只有子进程真正需要的
 * PATH/HOME/语言/临时目录等变量才会被传递，其余一律不出现。
 *
 * 诚实说明：白名单是纵深防御，不是安全边界。会话内的 bash 仍能读到服务进程
 * 所在容器的文件系统与网络；真正的隔离需要容器/微虚拟机，见 SANDBOX_ISOLATION.md。
 */
import { homedir } from 'node:os'

/** 允许传递给模型可执行子进程的环境变量（大小写按 POSIX 精确匹配，Windows 不敏感）。 */
export const SANDBOX_ENV_ALLOWLIST: readonly string[] = [
  // 命令查找与运行时必需
  'PATH',
  'PATHEXT',
  'SHELL',
  'COMSPEC',
  'SystemRoot',
  'SystemDrive',
  'windir',
  'WINDIR',
  // 用户目录与临时目录
  'HOME',
  'USERPROFILE',
  'HOMEDRIVE',
  'HOMEPATH',
  'LOGNAME',
  'USER',
  'TMPDIR',
  'TMP',
  'TEMP',
  'APPDATA',
  'LOCALAPPDATA',
  // 区域设置与终端
  'LANG',
  'LANGUAGE',
  'LC_ALL',
  'LC_CTYPE',
  'TERM',
  'COLORTERM',
  'TZ',
  // Node 运行时（不含 NODE_OPTIONS/NODE_PATH：可被用来注入模块或代码）
  'NODE_ENV',
  'NODE_VERSION',
  // 常见 CI 标记，便于在沙箱内跑常规构建命令
  'CI',
]

/**
 * 变量名中的敏感特征。即使某个名字出现在白名单里，只要命中该模式也一律丢弃——
 * 例如有人把密钥塞进一个看起来无害的 HOME 变量。
 */
const SECRET_NAME_PATTERN =
  /(SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|APIKEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL|SESSION|COOKIE|JWT|DATABASE|POSTGRES|MYSQL|REDIS|MONGO|CHROMA|ELASTIC|_DSN$|_URL$|_URI$|AUTH)/i

export function isSecretLookingEnvName(name: string): boolean {
  return SECRET_NAME_PATTERN.test(name)
}


export function buildSandboxEnv(
  source: NodeJS.ProcessEnv = process.env,
  extra: Record<string, string> = {},
): NodeJS.ProcessEnv {
  const caseInsensitive = process.platform === 'win32'
  const indexed: NodeJS.ProcessEnv = {}
  for (const [name, value] of Object.entries(source)) indexed[caseInsensitive ? name.toUpperCase() : name] = value

  const env: NodeJS.ProcessEnv = {}
  for (const name of SANDBOX_ENV_ALLOWLIST) {
    if (isSecretLookingEnvName(name)) continue
    const value = indexed[caseInsensitive ? name.toUpperCase() : name]
    if (typeof value === 'string' && value !== '') env[name] = value
  }
  // HOME 兜底：子进程（如 node）偶尔仍需要它。
  if (!env.HOME && !env.USERPROFILE) env.HOME = homedir()

  for (const [name, value] of Object.entries(extra)) {
    if (isSecretLookingEnvName(name)) continue
    if (typeof value === 'string' && value !== '') env[name] = value
  }
  return env
}
