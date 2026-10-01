import { describe, expect, it } from 'vitest'
import { classifyFailure, inFlightText, mayAlreadyBeStarted, quotaShortfall, wasStarted } from './batchRows'
import type { QuotaInfo } from '../../types'

const QUOTA: QuotaInfo = {
  daily_limit: 10, daily_used: 0, daily_remaining: 10, monthly_limit: 50, monthly_used: 47, monthly_remaining: 3, is_admin: false,
}

describe('classifyFailure', () => {
  it('retries no answer, 5xx and 429; not other 4xx', () => {
    expect(classifyFailure(new TypeError('Failed to fetch'), 'x').retryable).toBe(true)
    expect(classifyFailure({ status: 503, message: 'down' }, 'x').retryable).toBe(true)
    expect(classifyFailure({ status: 429, message: 'Too many requests' }, 'x').retryable).toBe(true)
    expect(classifyFailure({ status: 400, message: 'bad file' }, 'x').retryable).toBe(false)
  })
})

describe('quotaShortfall', () => {
  it('names the runs left this month when the month is shorter than the day', () => {
    expect(quotaShortfall(5, QUOTA)).toBe('You have 3 runs left this month. Remove 2 files, or submit the rest next month.')
    expect(quotaShortfall(3, QUOTA)).toBe('')
  })
})

describe('a refused start', () => {
  it('may mean an earlier start got through only for 400 and 409', () => {
    expect(mayAlreadyBeStarted({ status: 400, message: 'Cannot start run in status: running' })).toBe(true)
    expect(mayAlreadyBeStarted({ status: 409, message: 'already started' })).toBe(true)
    expect(mayAlreadyBeStarted({ status: 404, message: 'Uploaded file no longer available' })).toBe(false)
    expect(mayAlreadyBeStarted(new TypeError('Failed to fetch'))).toBe(false)
  })

  it('went through once the run has left created or paused', () => {
    expect(['queued', 'running', 'complete', 'failed', 'cancelled'].every(wasStarted)).toBe(true)
    expect(wasStarted('created')).toBe(false)
    expect(wasStarted('paused')).toBe(false)
  })
})

describe('inFlightText', () => {
  it('words the in-flight cap', () => {
    expect(inFlightText(2)).toBe('Two files at a time')
    expect(inFlightText(1)).toBe('One file at a time')
    expect(inFlightText(12)).toBe('12 files at a time')
  })
})
