/**
 * 导航与外部链接策略的反例测试。
 *
 * 旧实现 `url.startsWith(DEV_SERVER_URL)` 会被 `http://localhost:3000.evil.example` 绕过；
 * `shell.openExternal` 又接受任意 scheme。这里逐条钉死。
 */
import { strict as assert } from 'node:assert'
import { describe, it } from 'node:test'
import { classifyNavigation, isSafeExternalUrl, normalizeAllowedOrigins } from '../navigation-policy.js'

const ALLOWED = normalizeAllowedOrigins(['http://127.0.0.1:34115'])

describe('导航策略', () => {
  it('origin 精确相等视为内部页面', () => {
    assert.equal(classifyNavigation('http://127.0.0.1:34115/', ALLOWED), 'internal')
    assert.equal(classifyNavigation('http://127.0.0.1:34115/dashboard/x?y=1#z', ALLOWED), 'internal')
  })

  it('origin 不一致的 http(s) 一律外部处理', () => {
    assert.equal(classifyNavigation('https://example.com/', ALLOWED), 'external')
    assert.equal(classifyNavigation('http://127.0.0.1:34116/', ALLOWED), 'external')
    assert.equal(classifyNavigation('http://localhost:34115/', ALLOWED), 'external')
  })

  const blocked: Array<[string, string]> = [
    ['前缀相似域名', 'http://127.0.0.1:34115.evil.example/'],
    ['端口前缀', 'http://127.0.0.1:341150/'],
    ['userinfo 伪装', 'http://127.0.0.1:34115@evil.example/'],
    ['本地文件', 'file:///C:/Windows/System32/drivers/etc/hosts'],
    ['UNC 路径', '\\\\evil.example\\share'],
    ['javascript scheme', 'javascript:alert(1)'],
    ['data scheme', 'data:text/html,<script>alert(1)</script>'],
    ['blob scheme', 'blob:http://127.0.0.1:34115/1234'],
    ['Follina 协议', 'ms-msdt:/id PCWDiagnostic'],
    ['SMB 协议', 'smb://evil.example/share'],
    ['无法解析', 'not a url'],
  ]

  for (const [label, url] of blocked) {
    it(`拒绝${label}`, () => {
      assert.equal(classifyNavigation(url, ALLOWED), 'blocked')
    })
  }

  it('归一化允许 origin 时丢弃非法配置', () => {
    assert.deepEqual(normalizeAllowedOrigins(['http://localhost:80', 'garbage', 'HTTPS://Example.com']), [
      'http://localhost:80',
      'https://example.com:443',
    ])
  })
})

describe('系统浏览器闸门', () => {
  it('只放行 http(s)', () => {
    assert.equal(isSafeExternalUrl('https://example.com/docs'), true)
    assert.equal(isSafeExternalUrl('http://example.com/docs'), true)
  })

  const rejected = [
    'file:///C:/Windows/System32/calc.exe',
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'smb://evil.example/share',
    'ms-msdt:/id PCWDiagnostic',
    'vscode://file/c:/tmp',
    'not a url',
  ]

  for (const url of rejected) {
    it(`不交给系统浏览器：${url}`, () => {
      assert.equal(isSafeExternalUrl(url), false)
    })
  }
})
