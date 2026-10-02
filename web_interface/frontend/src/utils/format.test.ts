import { describe, expect, it } from 'vitest'
import { formatTimeLeft } from './format'

describe('formatTimeLeft', () => {
  it('rounds the remainder up to whole minutes', () => {
    expect(formatTimeLeft(300, 100)).toBe('about 4 min left')
    expect(formatTimeLeft(300, 299)).toBe('about 1 min left')
    expect(formatTimeLeft(7500, 0)).toBe('about 2 h 5 m left')
  })

  it('is hidden when the estimate is unknown or used up, never negative', () => {
    expect(formatTimeLeft(null, 10)).toBeNull()
    expect(formatTimeLeft(undefined, 10)).toBeNull()
    expect(formatTimeLeft(0, 10)).toBeNull()
    expect(formatTimeLeft(300, 300)).toBeNull()
    expect(formatTimeLeft(300, 900)).toBeNull()
  })
})
