/** Preserve finite scores, including 0 and 100; missing or invalid data has no score. */
export function performanceScoreDisplay(value: unknown) {
  const score = typeof value === 'number' && Number.isFinite(value) ? value : null
  return {
    score,
    label: score === null ? '未评估' : score.toFixed(1),
    width: Math.min(100, Math.max(0, score ?? 0)),
    tone: score === null ? null : score >= 80 ? 'ok' : score >= 60 ? 'warn' : 'bad',
  }
}
