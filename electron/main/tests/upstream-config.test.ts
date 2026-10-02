/**
 * 上游地址解析与桌面端配置优先级测试。
 *
 * 这是「运行时后端地址」的可验收边界：配置非法必须拒绝启动，不能静默换后端。
 */
import { strict as assert } from 'node:assert'
import * as fs from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import { after, before, describe, it } from 'node:test'
import {
  DEFAULT_API_URL,
  DEFAULT_COLLAB_URL,
  resolveDesktopConfig,
  userConfigPath,
} from '../desktop-config.js'
import { joinUpstreamPath, parseUpstreamUrl, upstreamRequestOptions } from '../upstream.js'

describe('上游地址解析', () => {
  it('归一化默认端口', () => {
    const target = parseUpstreamUrl('https://api.example.com', 'api')
    assert.equal(target.origin, 'https://api.example.com:443')
    assert.equal(target.secure, true)
    assert.equal(target.port, 443)
    assert.equal(target.prefix, '')
  })

  it('归一化路径前缀', () => {
    assert.equal(parseUpstreamUrl('https://gw.example.com/api/', 'api').prefix, '/api')
    assert.equal(parseUpstreamUrl('https://gw.example.com/api//', 'api').prefix, '/api')
    assert.equal(parseUpstreamUrl('https://gw.example.com/', 'api').prefix, '')
  })

  const invalid: Array<[string, string]> = [
    ['缺少 scheme', 'api.example.com'],
    ['ftp 协议', 'ftp://api.example.com'],
    ['file 协议', 'file:///etc/passwd'],
    ['内嵌凭据', 'http://user:pass@api.example.com'],
    ['带 query', 'http://api.example.com/?token=1'],
    ['带 fragment', 'http://api.example.com/#x'],
    ['前缀含百分号编码', 'http://api.example.com/a%20b'],
    ['空字符串', ''],
  ]

  for (const [label, value] of invalid) {
    it(`拒绝${label}`, () => {
      assert.throws(() => parseUpstreamUrl(value, 'api'))
    })
  }

  it('路径前缀里的 .. 在 URL 解析阶段被归一化，不产生越权前缀', () => {
    assert.equal(parseUpstreamUrl('http://api.example.com/gw/../etc', 'api').prefix, '/etc')
  })

  it('拼接上游路径时折叠重复斜杠', () => {
    assert.equal(joinUpstreamPath('/gw', '//api/v1//users'), '/gw/api/v1/users')
    // query 里的 http:// 不能被折叠成 http:/，否则是真实数据损坏。
    assert.equal(joinUpstreamPath('', '/api/v1/me?target=http://evil.example.com'), '/api/v1/me?target=http://evil.example.com')
    assert.equal(joinUpstreamPath('', '/api/v1'), '/api/v1')
  })

  it('生成 http.request 连接参数并替换 Host', () => {
    const target = parseUpstreamUrl('http://127.0.0.1:8000', 'api')
    const options = upstreamRequestOptions(target, 'POST', '/api/v1/auth/login?x=1', { Host: 'evil', Cookie: 'a=b' })
    assert.equal(options.hostname, '127.0.0.1')
    assert.equal(options.port, 8000)
    assert.equal(options.path, '/api/v1/auth/login?x=1')
    assert.equal(options.headers.host, 'evil')
    assert.equal(options.headers.cookie, 'a=b')
  })
})

describe('桌面端配置优先级', () => {
  let userDataDir: string

  before(async () => {
    userDataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'autoteams-desktop-config-'))
  })

  after(async () => {
    await fs.rm(userDataDir, { recursive: true, force: true })
  })

  it('没有配置文件时使用本机默认', async () => {
    const config = resolveDesktopConfig({ env: {}, userConfigPath: userConfigPath(userDataDir) })
    assert.equal(config.api.origin, new URL(DEFAULT_API_URL).origin)
    assert.equal(config.apiSource, 'default')
    assert.equal(config.collabSource, 'default')
    assert.equal(config.collab.origin, new URL(DEFAULT_COLLAB_URL).origin)
  })

  it('环境变量覆盖默认值', async () => {
    const config = resolveDesktopConfig({
      env: { AUTOTEAMS_API_URL: 'https://api.example.com' },
      userConfigPath: userConfigPath(userDataDir),
    })
    assert.equal(config.api.origin, 'https://api.example.com:443')
    assert.equal(config.apiSource, 'env')
  })

  it('用户配置文件优先于环境变量', async () => {
    const file = userConfigPath(userDataDir)
    await fs.writeFile(file, JSON.stringify({ apiUrl: 'http://10.0.0.5:8000' }), 'utf8')
    try {
      const config = resolveDesktopConfig({ env: { AUTOTEAMS_API_URL: 'https://api.example.com' }, userConfigPath: file })
      assert.equal(config.api.origin, 'http://10.0.0.5:8000')
      assert.equal(config.apiSource, 'user-file')
    } finally {
      await fs.rm(file, { force: true })
    }
  })

  it('未知字段拒绝启动', async () => {
    const file = userConfigPath(userDataDir)
    await fs.writeFile(file, JSON.stringify({ apiUrl: 'http://10.0.0.5:8000', apiBaseUrl: '/api/v1' }), 'utf8')
    try {
      assert.throws(
        () => resolveDesktopConfig({ env: {}, userConfigPath: file }),
        /未知字段/,
      )
    } finally {
      await fs.rm(file, { force: true })
    }
  })

  it('非法 JSON 拒绝启动', async () => {
    const file = userConfigPath(userDataDir)
    await fs.writeFile(file, '{ not json', 'utf8')
    try {
      assert.throws(() => resolveDesktopConfig({ env: {}, userConfigPath: file }), /不是合法 JSON/)
    } finally {
      await fs.rm(file, { force: true })
    }
  })

  it('用户配置里显式的非字符串/空值拒绝启动，不当作缺省', async () => {
    const file = userConfigPath(userDataDir)
    for (const bad of [{ apiUrl: 123 }, { apiUrl: '' }, { apiUrl: '   ' }, { collabUrl: null }]) {
      await fs.writeFile(file, JSON.stringify(bad), 'utf8')
      assert.throws(
        () => resolveDesktopConfig({ env: {}, userConfigPath: file }),
        /必须是非空字符串/,
        `${JSON.stringify(bad)} 应被拒绝`,
      )
    }
    await fs.rm(file, { force: true })
  })

  it('配置了不支持的协议时拒绝启动而不是回落默认', async () => {
    assert.throws(
      () => resolveDesktopConfig({ env: { AUTOTEAMS_API_URL: 'ftp://api.example.com' }, userConfigPath: userConfigPath(userDataDir) }),
      /只支持 http\/https/,
    )
  })
})
