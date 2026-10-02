/* eslint-env node */
// Compatibility entry point: validate the same configuration consumed by Vercel.
import('../../vercel.mjs').then(({ config }) => {
  console.log(`[OK] API rewrite configured for ${new URL(config.rewrites[0].destination).origin}`)
}).catch((error) => {
  console.error(`[ERROR] ${error.message}`)
  process.exitCode = 1
})
