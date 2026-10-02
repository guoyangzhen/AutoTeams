import assert from 'node:assert/strict'
import test from 'node:test'

import { safeBridgeUrl } from '../src/client.js'
import { settleCleanup } from '../src/shutdown.js'

test('safeBridgeUrl removes token from a valid bridge URL', () => {
  const safe = safeBridgeUrl('wss://example.test/bridge?grant=grant-1&token=bridge-secret')

  assert.equal(safe.includes('bridge-secret'), false)
  assert.equal(safe.includes('grant=grant-1'), true)
})

test('safeBridgeUrl redacts token even when malformed URL parsing fails', () => {
  const safe = safeBridgeUrl('wss://bad host/bridge?grant=grant-1&token=bridge-secret%ZZ')

  assert.equal(safe.includes('bridge-secret'), false)
  assert.equal(safe, '[invalid bridge URL]')
})

test('safeBridgeUrl omits userinfo, fragments and all non-grant query parameters', () => {
  assert.equal(
    safeBridgeUrl('wss://user:password@example.test/bridge?Token=secret&token=other&key=hidden&grant=g#secret'),
    'wss://example.test/bridge?grant=g',
  )
})

test('shutdown waits until asynchronous resource cleanup completes', async () => {
  let release!: () => void
  let finished = false
  const pending = settleCleanup(() => new Promise<void>(resolve => { release = resolve }))
    .then(result => { finished = true; return result })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(finished, false)
  release()
  assert.equal(await pending, true)
})

test('cleanup rejection is contained and does not prevent shutdown', async () => {
  assert.equal(await settleCleanup(async () => { throw new Error('driver failure') }), false)
})

test('a hung driver cannot indefinitely delay shutdown', { timeout: 1_000 }, async () => {
  assert.equal(await settleCleanup(() => new Promise<void>(() => {}), 20), false)
})
