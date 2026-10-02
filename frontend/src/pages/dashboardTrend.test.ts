import { describe, expect, it } from 'vitest'
import { trendScale } from './dashboardTrend'

describe('dashboard trend axis', () => {
  it('places two-point counts on the same integer scale as the axis labels', () => {
    const scale = trendScale([2, 4])
    expect(scale.ticks).toEqual([2, 4, 6])
    expect(scale.y(4, 30, 210)).toBe(scale.y(scale.ticks[1], 30, 210))
    expect(scale.y(2, 30, 210)).toBe(scale.y(scale.ticks[0], 30, 210))
  })

  it('keeps zero and invalid counts finite at the baseline', () => {
    const scale = trendScale([0, 0])
    expect(scale.ticks).toEqual([0, 1])
    expect(scale.y(0, 30, 210)).toBe(210)
    expect(scale.y(Number.NaN, 30, 210)).toBe(210)
  })
})
