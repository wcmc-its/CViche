import { describe, expect, it } from 'vitest'
import { limitsChanged } from './adminConfigState'

const SAVED = { rate_limit_daily: 10, rate_limit_monthly: 50 }

describe('limitsChanged', () => {
  it('is false while the inputs match the saved limits', () => {
    expect(limitsChanged(SAVED, '10', '50')).toBe(false)
  })

  it('is true once either input differs, including a cleared one', () => {
    expect(limitsChanged(SAVED, '11', '50')).toBe(true)
    expect(limitsChanged(SAVED, '10', '')).toBe(true)
  })
})
