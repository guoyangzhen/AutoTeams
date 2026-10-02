import { describe, expect, it } from 'vitest'
import { performanceScoreDisplay } from './performanceScore'

describe('performanceScoreDisplay', () => {
  it('preserves a real zero and a valid perfect score', () => {
    expect(performanceScoreDisplay(0)).toEqual({ score: 0, label: '0.0', width: 0, tone: 'bad' })
    expect(performanceScoreDisplay(100)).toEqual({ score: 100, label: '100.0', width: 100, tone: 'ok' })
  })

  it.each([undefined, null, NaN, Infinity, -Infinity, '85'])(
    'shows missing or invalid value %s as unassessed',
    (value) => {
      expect(performanceScoreDisplay(value)).toEqual({
        score: null,
        label: '未评估',
        width: 0,
        tone: null,
      })
    },
  )
})
