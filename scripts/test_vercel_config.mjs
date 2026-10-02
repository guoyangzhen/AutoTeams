import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const root = fileURLToPath(new URL('../', import.meta.url))
function load(value) {
  const env = { ...process.env }
  if (value === undefined) delete env.BACKEND_URL
  else env.BACKEND_URL = value
  return spawnSync(process.execPath, ['--input-type=module', '-e',
    "import { config } from './vercel.mjs'; console.log(JSON.stringify(config))"],
  { cwd: root, env, encoding: 'utf8', timeout: 10000 })
}

test('actual deployment configuration expands each backend origin', () => {
  for (const value of ['https://api-a.example.test/', 'http://127.0.0.1:8123']) {
    const result = load(value)
    assert.equal(result.status, 0, result.stderr)
    const config = JSON.parse(result.stdout)
    assert.equal(config.rewrites[0].destination, `${new URL(value).origin}/api/:path*`)
    assert.equal(config.installCommand, 'npm ci --prefix frontend')
    assert.ok(!JSON.stringify(config).includes('${BACKEND_URL}'))
  }
})

test('missing, invalid, credentialed or ambiguous origins fail before deployment', () => {
  for (const value of [undefined, '', 'relative', 'file:///tmp/backend',
    'https://user:synthetic-secret@example.test', 'https://example.test/prefix',
    'https://example.test/?key=synthetic-secret', 'https://example.test/#fragment']) {
    const result = load(value)
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /BACKEND_URL/)
    assert.ok(!result.stderr.includes('synthetic-secret'))
  }
})
