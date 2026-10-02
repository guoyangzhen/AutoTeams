/** Wait for resource disposal, but never let a stuck driver prevent process exit. */
export async function settleCleanup(cleanup: () => Promise<void>, timeoutMs = 3_000): Promise<boolean> {
  let timer: NodeJS.Timeout | undefined
  try {
    return await Promise.race([
      Promise.resolve().then(cleanup).then(() => true),
      new Promise<false>(resolve => { timer = setTimeout(() => resolve(false), timeoutMs) }),
    ])
  } catch {
    return false
  } finally {
    if (timer) clearTimeout(timer)
  }
}
