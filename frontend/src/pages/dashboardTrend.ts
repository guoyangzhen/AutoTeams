/** Integer count ticks and the plotted line share this exact vertical scale. */
export function trendScale(values: number[]) {
  const max = Math.max(0, ...values.filter(Number.isFinite))
  const step = Math.max(1, Math.ceil(max / 3))
  const axisMax = step * 3
  const ticks = max === 0 ? [0, step] : [step, step * 2, axisMax]
  const y = (value: number, top: number, bottom: number) =>
    bottom - (Number.isFinite(value) ? Math.max(0, value) : 0) / axisMax * (bottom - top)
  return { axisMax, ticks, y }
}
