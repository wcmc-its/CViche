import { describe, expect, it } from 'vitest'
import { formatScannedPages, formatTimeLeft } from './format'

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

describe('formatScannedPages (#1282)', () => {
  it('names one page in the singular', () => {
    expect(formatScannedPages([4])).toBe("Page 4 of this PDF is a scanned image, so its text couldn't be read and is missing from the output.")
  })

  it('names several pages in the plural', () => {
    expect(formatScannedPages([3, 5])).toMatch(/^Pages 3, 5 of this PDF are scanned images, so their text/)
  })
})
