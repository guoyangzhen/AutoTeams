/**
 * 真实构建产物验收。
 *
 * AUD-28 的实证断点：Vite 默认 base=/ 时，产物 index.html 里写的是
 * `src="/assets/index-*.js"`；旧的 `loadFile` 加载方式会把它解析成
 * `E:/assets/index-*.js`，必然 404。这里直接拿 `frontend/dist` 的真实产物过一遍
 * 回环服务，逐个资源断言 200 且字节数与磁盘文件一致。
 *
 * 没有构建产物时整体跳过（不伪造通过），并提示先执行 frontend 的 npm run build。
 */
import { strict as assert } from 'node:assert'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { describe, it } from 'node:test'
import { startDesktopServer } from '../loopback-server.js'
import { parseUpstreamUrl } from '../upstream.js'
import { rawRequest } from './helpers.js'

const REAL_DIST = process.env.AUTOTEAMS_DESKTOP_REAL_DIST
  ? path.resolve(process.env.AUTOTEAMS_DESKTOP_REAL_DIST)
  : path.resolve(__dirname, '../../../..', 'frontend', 'dist')

const TEST_TIMEOUT_MS = 30_000

/** 取出 index.html 中所有以 / 开头的静态资源引用。 */
function absoluteAssetRefs(html: string): string[] {
  const refs: string[] = []
  const pattern = /(?:src|href)\s*=\s*"([^"]+)"/g
  for (const match of html.matchAll(pattern)) {
    const value = match[1]
    if (value.startsWith('/')) refs.push(value.split('?')[0])
  }
  return [...new Set(refs)]
}

describe('真实前端产物的资源解析', () => {
  const indexFile = path.join(REAL_DIST, 'index.html')
  const available = fs.existsSync(indexFile)
  if (process.env.AUTOTEAMS_DESKTOP_REQUIRE_DIST === '1') {
    assert.ok(available, `必须提供真实前端构建产物：${indexFile}`)
  }

  const itIfBuilt = available ? it : it.skip

  if (!available) {
    it('提示先构建前端产物', (t) => {
      t.skip(`未找到 ${indexFile}；请先在 frontend 目录执行 npm run build`)
    })
  }

  itIfBuilt('index.html 引用的每个绝对资源都能从包内解析', { timeout: TEST_TIMEOUT_MS }, async () => {
    const html = await fs.promises.readFile(indexFile, 'utf8')
    const refs = absoluteAssetRefs(html)
    assert.equal(refs.length > 0, true, 'index.html 中没有绝对路径资源引用，验收前提不成立')

    const server = await startDesktopServer({
      rootDir: REAL_DIST,
      api: parseUpstreamUrl('http://127.0.0.1:9', 'api'),
      collab: parseUpstreamUrl('http://127.0.0.1:9', 'collab'),
      runtimeConfig: () => ({}),
      port: 0,
    })
    try {
      for (const ref of refs) {
        const relative = ref.replace(/^\//, '')
        const response = await rawRequest(server.origin, { path: ref })
        const onDisk = path.join(REAL_DIST, relative)
        assert.ok(fs.existsSync(onDisk), `${ref} 被入口引用但未包含在构建产物中`)
        assert.equal(response.status, 200, `${ref} 未从包内解析到（HTTP ${response.status}）`)
        const stats = await fs.promises.stat(onDisk)
        assert.equal(response.body.length > 0, true, `${ref} 返回空内容`)
        assert.equal(Number(response.headers['content-length']), stats.size, `${ref} 长度与磁盘文件不一致`)
      }
    } finally {
      await server.close()
    }
  })

  itIfBuilt('深链刷新回退到真实 index.html', { timeout: TEST_TIMEOUT_MS }, async () => {
    const html = await fs.promises.readFile(indexFile, 'utf8')
    const server = await startDesktopServer({
      rootDir: REAL_DIST,
      api: parseUpstreamUrl('http://127.0.0.1:9', 'api'),
      collab: parseUpstreamUrl('http://127.0.0.1:9', 'collab'),
      runtimeConfig: () => ({}),
      port: 0,
    })
    try {
      const response = await rawRequest(server.origin, {
        path: '/enterprise/settings/model',
        headers: { accept: 'text/html' },
      })
      assert.equal(response.status, 200)
      assert.equal(response.body, html)
    } finally {
      await server.close()
    }
  })
})
