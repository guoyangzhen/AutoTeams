/**
 * AUD-02：沙箱 bash 与子进程环境变量。
 *
 * - bash 从「全部放行」改为默认拒绝（允许列表为空）；
 * - 子进程只继承白名单环境变量，服务凭据一律不出现；
 * - 超时与输出上限强制生效，子进程被回收。
 */
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { SANDBOX_ENV_ALLOWLIST, buildSandboxEnv, isSecretLookingEnvName } from '../src/sandbox-env.js'
import {
  SandboxCommandDeniedError,
  assertCommandAllowed,
  execSandboxBash,
  loadSandboxBashPolicy,
} from '../src/sandbox-tools.js'

const sessionRoot = await mkdtemp(join(tmpdir(), 'collab-bash-'))

// 服务进程持有的真实凭据形态（合成值），子进程一个都不应该看到。
const SECRETS: Record<string, string> = {
  DATABASE_URL: 'postgresql://autoteams:real-password@db:5432/autoteams',
  SECRET_KEY: 'audit-only-secret',
  AGNES_API_KEY: 'sk-audit-only',
  BRIDGE_INTERNAL_SECRET: 'audit-bridge-secret',
  REDIS_URL: 'redis://:audit-password@redis:6379/0',
  CHROMA_AUTH_TOKEN: 'audit-chroma-token',
  OPENAI_API_KEY: 'sk-audit-only-openai',
  SESSION_SIGNING_KEY: 'audit-session-key',
}
Object.assign(process.env, SECRETS)

test('buildSandboxEnv 只保留白名单变量，丢弃全部凭据', () => {
  const env = buildSandboxEnv({
    PATH: '/usr/bin',
    HOME: '/home/agent',
    LANG: 'C.UTF-8',
    NODE_ENV: 'production',
    ...SECRETS,
  })
  assert.deepEqual(Object.keys(env).sort(), ['HOME', 'LANG', 'NODE_ENV', 'PATH'])
  for (const name of Object.keys(SECRETS)) assert.equal(env[name], undefined)

  // 真实 process.env 下同样不含任何敏感变量
  const live = buildSandboxEnv()
  for (const name of Object.keys(SECRETS)) assert.equal(live[name], undefined, `${name} 不应出现在子进程环境中`)
  assert.ok(Object.keys(live).every((name) => SANDBOX_ENV_ALLOWLIST.includes(name)))
  assert.ok(isSecretLookingEnvName('DATABASE_URL') && isSecretLookingEnvName('AGNES_API_KEY'))
  assert.ok(!isSecretLookingEnvName('PATH'))
})

test('bash 默认拒绝：未配置允许列表时任何命令都被拒绝', () => {
  const policy = loadSandboxBashPolicy({})
  assert.equal(policy.allowedCommands.size, 0)
  assert.equal(policy.unrestricted, false)
  assert.throws(() => assertCommandAllowed('ls', policy), SandboxCommandDeniedError)
  assert.throws(() => assertCommandAllowed('node', policy), SandboxCommandDeniedError)
})

test('允许列表只接受单一简单命令，链式/替换/解释器一律拒绝', () => {
  const policy = loadSandboxBashPolicy({ COLLAB_SANDBOX_BASH_ALLOWLIST: 'ls, cat' })
  assert.deepEqual(assertCommandAllowed('ls -la notes.md', policy), ['ls', '-la', 'notes.md'])
  assert.throws(() => assertCommandAllowed('ls; whoami', policy), SandboxCommandDeniedError)
  assert.throws(() => assertCommandAllowed('cat notes.md | sh', policy), SandboxCommandDeniedError)
  assert.throws(() => assertCommandAllowed('cat $(whoami)', policy), SandboxCommandDeniedError)
  assert.throws(() => assertCommandAllowed('ls > out.txt', policy), SandboxCommandDeniedError)
  assert.throws(() => assertCommandAllowed('bash -c ls', policy), SandboxCommandDeniedError)
  assert.throws(() => assertCommandAllowed('env node print-env.mjs', policy), SandboxCommandDeniedError)
  assert.throws(() => assertCommandAllowed('node print-env.mjs', policy), SandboxCommandDeniedError)
})

test('显式 opt-in 才能放开全部命令，并被大声告警', () => {
  const policy = loadSandboxBashPolicy({ COLLAB_SANDBOX_ALLOW_UNRESTRICTED_BASH: 'true' })
  assert.equal(policy.unrestricted, true)
  assert.deepEqual(assertCommandAllowed('ls; whoami', policy), ['ls;', 'whoami'])
})

async function runChild(command: string, policy = loadSandboxBashPolicy({ COLLAB_SANDBOX_BASH_ALLOWLIST: 'node' })) {
  const chunks: Buffer[] = []
  const startedAt = Date.now()
  const result = await execSandboxBash(sessionRoot, policy, {
// 超时/输出上限必须打真实的子进程与真实时钟（假定时器无法覆盖 kill 行为），因此这里确实等待。
    command,
    cwd: sessionRoot,
    onData: (data) => chunks.push(Buffer.from(data)),
  })
  return { result, output: Buffer.concat(chunks).toString('utf8'), elapsed: Date.now() - startedAt }
}

test('真实子进程拿不到任何服务凭据', async () => {
  await writeFile(join(sessionRoot, 'print-env.mjs'), 'console.log(JSON.stringify(process.env))\n', 'utf8')
  const { result, output } = await runChild('node print-env.mjs')
  assert.equal(result.exitCode, 0, output)

  const childEnv = JSON.parse(output.trim()) as Record<string, string>
  for (const name of Object.keys(SECRETS)) {
    assert.equal(childEnv[name], undefined, `子进程环境不应包含 ${name}`)
  }
  // 白名单变量确实传递下去了（证明不是「什么都没传」这种假阳性）
  assert.ok(Object.keys(childEnv).length > 0)
  assert.ok(childEnv.PATH || childEnv.Path, '子进程应当继承 PATH')
  assert.ok(childEnv.HOME || childEnv.USERPROFILE, '子进程应当继承 HOME/USERPROFILE')
})

test('超时被强制执行，超长输出被截断，进程被回收', async () => {
  // 超时/输出上限必须打真实的子进程与真实时钟（假定时器无法覆盖 kill 行为），这里确实等待。
  await writeFile(join(sessionRoot, 'sleep.mjs'), 'await new Promise((r) => setTimeout(r, 30000))\n', 'utf8')
  await writeFile(
    join(sessionRoot, 'flood.mjs'),
    'process.stdout.write("x".repeat(1024 * 1024))\n',
    'utf8',
  )

  const policy = loadSandboxBashPolicy({
    COLLAB_SANDBOX_BASH_ALLOWLIST: 'node',
    COLLAB_SANDBOX_BASH_TIMEOUT_MS: '1000',
    COLLAB_SANDBOX_BASH_MAX_OUTPUT_BYTES: '4096',
  })

  const timedOut = await runChild('node sleep.mjs', policy)
  assert.equal(timedOut.result.exitCode, 124)
  assert.match(timedOut.output, /超时/)
  assert.ok(timedOut.elapsed < 15_000, `期望超时后立即返回，实际 ${timedOut.elapsed}ms`)

  const flooded = await runChild('node flood.mjs', policy)
  assert.equal(flooded.result.exitCode, null, '输出超限应终止命令')
  assert.ok(flooded.output.length < 8192, `输出应被截断到上限附近，实际 ${flooded.output.length} 字节`)
  assert.match(flooded.output, /输出超过/)
})

test('Windows taskkill 启动失败时仍回收直接子进程', { skip: process.platform !== 'win32' }, async () => {
  await writeFile(
    join(sessionRoot, 'ready-then-sleep.mjs'),
    'console.log("ready"); await new Promise((r) => setTimeout(r, 30000))\n',
    'utf8',
  )
  const policy = loadSandboxBashPolicy({
    COLLAB_SANDBOX_BASH_ALLOWLIST: 'node',
    COLLAB_SANDBOX_BASH_TIMEOUT_MS: '1000',
  })
  const originalPath = process.env.PATH
  let sawReady = false
  let taskkillUnavailable = false
  const startedAt = Date.now()
  try {
    const result = await execSandboxBash(sessionRoot, policy, {
      command: 'node ready-then-sleep.mjs',
      cwd: sessionRoot,
      onData: (data) => {
        if (!sawReady && data.toString('utf8').includes('ready')) {
          sawReady = true
          // 子进程已经启动；只让随后启动的 taskkill 无法从 PATH 找到。
          process.env.PATH = ''
          taskkillUnavailable = (spawnSync('taskkill', ['/??'], {
            env: { PATH: '' },
            stdio: 'ignore',
          }).error as NodeJS.ErrnoException | undefined)?.code === 'ENOENT'
        }
      },
    })
    assert.equal(sawReady, true)
    assert.equal(taskkillUnavailable, true, '此测试必须真正覆盖 taskkill 的 ENOENT 路径')
    assert.equal(result.exitCode, 124)
    assert.ok(Date.now() - startedAt < 15_000, 'taskkill 失败后不应等待子进程自然退出')
  } finally {
    if (originalPath === undefined) delete process.env.PATH
    else process.env.PATH = originalPath
  }
})


test('production refuses host command opt-ins instead of only warning', () => {
  assert.throws(() => loadSandboxBashPolicy({
    NODE_ENV: 'production', COLLAB_SANDBOX_BASH_ALLOWLIST: 'node,cat',
  }), SandboxCommandDeniedError)
  assert.throws(() => loadSandboxBashPolicy({
    NODE_ENV: 'production', COLLAB_SANDBOX_ALLOW_UNRESTRICTED_BASH: 'true',
  }), SandboxCommandDeniedError)
  const policy = loadSandboxBashPolicy({ NODE_ENV: 'production' })
  assert.equal(policy.allowedCommands.size, 0)
  assert.equal(policy.unrestricted, false)
})

test('production execution rejects even a policy constructed outside the loader', async () => {
  const previous = process.env.NODE_ENV
  process.env.NODE_ENV = 'production'
  try {
    await assert.rejects(() => runChild('node --version', {
      allowedCommands: new Set(['node']), unrestricted: true,
      timeoutMs: 1000, maxOutputBytes: 1024,
    }), SandboxCommandDeniedError)
  } finally {
    if (previous === undefined) delete process.env.NODE_ENV
    else process.env.NODE_ENV = previous
  }
})
