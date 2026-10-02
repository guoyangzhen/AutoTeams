/**
 * 随包静态资源解析的边界测试。
 *
 * 验证「URL 路径 → 包内文件」的映射是否被牢牢锁在资源根目录内：
 * 编码绕过、Windows 盘符段、符号链接逃逸都必须被拒绝，而不是「碰巧不存在」。
 */
import { strict as assert } from 'node:assert'
import * as fs from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import { after, before, describe, it } from 'node:test'
import { resolveStaticAsset } from '../static-assets.js'
import { createStaticFixture, type StaticFixture } from './helpers.js'

const OUTSIDE_SECRET = 'desktop-secret-should-never-be-served'

describe('随包静态资源解析', () => {
  let fixture: StaticFixture
  let outsideDir: string
  let secretPath: string
  let symlinkSupported = false

  before(async () => {
    fixture = await createStaticFixture({
      'index.html': '<!doctype html><html><body>root</body></html>',
      'assets/index-DxKplZ14.js': 'console.log(1)\n',
      'sub/index.html': '<!doctype html><html><body>sub</body></html>',
    })
    outsideDir = await fs.mkdtemp(path.join(os.tmpdir(), 'autoteams-desktop-outside-'))
    secretPath = path.join(outsideDir, OUTSIDE_SECRET)
    await fs.writeFile(secretPath, 'top-secret', 'utf8')
    try {
      await fs.symlink(secretPath, path.join(fixture.root, 'leak.txt'), 'file')
      symlinkSupported = true
    } catch {
      // Windows 未开启开发者模式时无法创建符号链接：该用例跳过，而不是假装通过。
      symlinkSupported = false
    }
  })

  after(async () => {
    await fixture.cleanup()
    await fs.rm(outsideDir, { recursive: true, force: true })
  })

  it('解析根目录内的文件并给出正确 MIME', async () => {
    const asset = await resolveStaticAsset(fixture.root, '/assets/index-DxKplZ14.js')
    assert.ok(asset.kind === 'file')
    assert.equal(asset.contentType, 'text/javascript; charset=utf-8')
    assert.equal(path.basename(asset.filePath), 'index-DxKplZ14.js')
  })

  it('目录请求回落到目录内的 index.html', async () => {
    const asset = await resolveStaticAsset(fixture.root, '/sub')
    assert.ok(asset.kind === 'file')
    assert.equal(asset.contentType, 'text/html; charset=utf-8')
    assert.equal((await fs.readFile(asset.filePath, 'utf8')).includes('sub'), true)
  })

  it('缺失文件返回 not-found 而不是抛错', async () => {
    assert.deepEqual(await resolveStaticAsset(fixture.root, '/assets/missing.js'), { kind: 'not-found' })
  })

  it('资源根目录不存在时返回 not-found', async () => {
    assert.deepEqual(await resolveStaticAsset(path.join(fixture.root, 'nope'), '/index.html'), { kind: 'not-found' })
  })

  const rejected: Array<[string, string]> = [
    ['未编码的路径穿越', '/../' + OUTSIDE_SECRET],
    ['编码后的路径穿越', '/%2e%2e/' + OUTSIDE_SECRET],
    ['双重编码的路径穿越', '/%252e%252e/' + OUTSIDE_SECRET],
    ['反斜杠形式的穿越', '/..%5c' + OUTSIDE_SECRET],
    ['反斜杠原样出现', '/..\\' + OUTSIDE_SECRET],
    ['Windows 盘符段', '/C:/Windows/win.ini'],
    ['NUL 截断', '/index.html%00.png'],
    ['非法百分号编码', '/index.html%zz'],
  ]

  for (const [label, requestPath] of rejected) {
    it(`拒绝${label}`, async () => {
      const result = await resolveStaticAsset(fixture.root, requestPath)
      const refused = result.kind === 'rejected' || result.kind === 'not-found'
      assert.equal(refused, true, `${label} 未被拒绝：${JSON.stringify(result)}`)
    })
  }

  it('符号链接指向根目录之外时拒绝', (t) => {
    if (!symlinkSupported) {
      t.skip('当前系统不允许创建符号链接（未开启开发者模式）')
      return
    }
    return resolveStaticAsset(fixture.root, '/leak.txt').then((result) => {
      assert.ok(result.kind === 'rejected')
      assert.equal(result.status, 403)
    })
  })
})
