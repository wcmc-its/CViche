import { describe, expect, it } from 'vitest'
import {
  MAX_UPLOAD_BYTES, TOO_LARGE_REASON, UNSUPPORTED_TYPE_REASON, classifyFailure, inFlightText, makeRow, mayAlreadyBeStarted, quotaShortfall, rowNote, wasStarted,
} from './batchRows'
import type { Estimate, QuotaInfo } from '../../types'

const EST_BASE: Estimate = {
  document_tokens: 1000, text_characters: 4000, estimated_cost_min: 1, estimated_cost_max: 2,
  estimated_time_seconds_min: 240, estimated_time_seconds_max: 360, num_steps: 12, filename: 'cv.pdf',
  file_size_kb: 10, pricing_model: 'test-model', text_characters_is_guess: false,
}

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

describe('classifyFailure on a duplicate file', () => {
  it('holds it for confirmation: not retryable, flagged, with the server message', () => {
    const failure = classifyFailure({ status: 409, code: 'duplicate_file', message: 'Already processed' }, 'x')
    expect(failure).toEqual({ reason: 'Already processed', retryable: false, duplicate: true })
  })

  it('treats a 409 with another code as an ordinary permanent failure', () => {
    expect(classifyFailure({ status: 409, message: 'Conflict' }, 'x').duplicate).toBeUndefined()
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

describe('makeRow size check', () => {
  const sized = (name: string, size: number) => {
    const file = new File(['x'], name)
    Object.defineProperty(file, 'size', { value: size })
    return file
  }

  it("marks a file over the cap as won't-be-submitted, with no estimate pending", () => {
    const row = makeRow(sized('big.docx', MAX_UPLOAD_BYTES + 1), 'k')
    expect(row.invalidReason).toBe(TOO_LARGE_REASON)
    expect(row.estimate).toBeNull()
  })

  it('accepts a file exactly at the cap and keeps the wrong-type reason first', () => {
    expect(makeRow(sized('ok.docx', MAX_UPLOAD_BYTES), 'k').invalidReason).toBeNull()
    expect(makeRow(sized('big.doc', MAX_UPLOAD_BYTES + 1), 'k').invalidReason).toBe(UNSUPPORTED_TYPE_REASON)
  })
})

describe('makeRow file type (#1273)', () => {
  it('accepts .docx and .pdf in any case, and refuses anything else', () => {
    for (const name of ['cv.docx', 'cv.pdf', 'CV.PDF']) expect(makeRow(new File(['x'], name), 'k').invalidReason).toBeNull()
    for (const name of ['cv.doc', 'cv.txt', 'cv.pdf.zip']) {
      expect(makeRow(new File(['x'], name), 'k').invalidReason).toBe(UNSUPPORTED_TYPE_REASON)
    }
  })
})

describe('rowNote scanned pages (#1282)', () => {
  const withPages = (pages: number[]) => makeRow(new File(['x'], 'cv.pdf'), 'k', { ...EST_BASE, scanned_pages: pages })

  it("names a PDF's scanned pages under a valid row", () => {
    expect(rowNote(withPages([2]))).toBe("Page 2 of this PDF is a scanned image, so its text can't be read and will be missing from the output.")
    expect(rowNote(withPages([3, 4]))).toMatch(/^Pages 3, 4 of this PDF are scanned images, so their text/)
  })

  it('says nothing when no page is scanned, and a failure still wins', () => {
    expect(rowNote(withPages([]))).toBe('')
    const failed = { ...withPages([2]), state: 'failed' as const, failure: { reason: 'Server error', retryable: true } }
    expect(rowNote(failed)).toBe('Server error')
  })
})
