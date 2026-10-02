import { persistSession } from '../../src/session-store.js'
process.send({ ready: true })
process.once('message', async () => {
  try {
    const prefix = process.env.TEST_SESSION_PREFIX
    for (let i = 0; i < 12; i++) {
      await persistSession({ sessionId: `${prefix}-${i}`, agentId: 'agent', agentName: 'fixture',
        positionLabel: 'test', createdAt: '2026-09-29T00:00:00Z',
        lastActiveAt: '2026-09-29T00:00:00Z', messageCount: 0, lastMessagePreview: '', mode: 'sandbox',
      }, 'owner', 'enterprise', process.cwd(), [])
    }
    process.disconnect()
  } catch (error) {
    console.error(error.message)
    process.exit(1)
  }
})
