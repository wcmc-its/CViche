import { describe, expect, it } from 'vitest'
import {
  COST_OUTLIER_RATIO, DEFAULT_MAX_UPLOAD_MB, UNSUPPORTED_TYPE_REASON, costOutliers, missingItems, maxUploadBytes, tooLargeReason, classifyFailure, inFlightText, inboxFailure, makeInboxRow, makeRow, rowSize, estimateText, mayAlreadyBeStarted, quotaShortfall, rowNote, wasStarted,
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

  const DEFAULT_BYTES = maxUploadBytes(DEFAULT_MAX_UPLOAD_MB)

  it("marks a file over the cap as won't-be-submitted, with no estimate pending", () => {
    const row = makeRow(sized('big.docx', DEFAULT_BYTES + 1), 'k')
    expect(row.invalidReason).toBe('Larger than 10 MB')
    expect(row.estimate).toBeNull()
  })

  it('accepts a file exactly at the cap and keeps the wrong-type reason first', () => {
    expect(makeRow(sized('ok.docx', DEFAULT_BYTES), 'k').invalidReason).toBeNull()
    expect(makeRow(sized('big.doc', DEFAULT_BYTES + 1), 'k').invalidReason).toBe(UNSUPPORTED_TYPE_REASON)
  })

  it("checks against the backend's cap when it is not the default (#109)", () => {
    const fifteenMb = sized('mid.docx', maxUploadBytes(15))
    expect(makeRow(fifteenMb, 'k', undefined, 20).invalidReason).toBeNull()
    expect(makeRow(fifteenMb, 'k', undefined, 12).invalidReason).toBe(tooLargeReason(12))
    expect(tooLargeReason(12)).toBe('Larger than 12 MB')
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

describe('emailed CV rows (#1298)', () => {
  const item = { id: 5, filename: 'emailed.docx', size_bytes: 4096, received_at: '2026-10-01T12:00:00Z', duplicate: null }

  it('carries the item id and its real size on a name-only placeholder file', () => {
    const row = makeInboxRow(item, 'row-1')
    expect(row.inbox).toEqual({ id: 5, sizeBytes: 4096 })
    expect(row.file.name).toBe('emailed.docx')
    expect(rowSize(row)).toBe(4096)
    expect(row.invalidReason).toBeNull()
    expect(row.estimate).toBeNull()
    expect(makeRow(new File(['ab'], 'a.docx'), 'row-2').inbox).toBeNull()
  })

  it('states only the count when no row has an estimate', () => {
    expect(estimateText(2, { minutes: 0, cost: null }, false)).toBe('2 CVs')
    expect(estimateText(2, { minutes: 12, cost: null }, false)).toBe('2 CVs · about 12 min of processing')
  })

  it('classifies a refused item like a refused upload', () => {
    const base = { id: 5, status: 'failed' as const, run_id: null, last_processed_on: null }
    expect(inboxFailure({ ...base, error: 'duplicate_file', message: 'Already processed' }))
      .toEqual({ reason: 'Already processed', retryable: false, duplicate: true })
    expect(inboxFailure({ ...base, error: 'rate_limited', message: 'Daily limit reached' }).retryable).toBe(true)
    expect(inboxFailure({ ...base, error: 'file_missing', message: null }))
      .toEqual({ reason: "Couldn't submit the emailed file", retryable: false })
  })
})

describe('costOutliers (#1599)', () => {
  const weighed = (weights: (number | undefined)[]) =>
    weights.map((w, i) => makeRow(new File(['x'], `cv${i}.docx`), `k${i}`, { ...EST_BASE, cost_weight: w }))

  it('compares each file with the median of the others, an even count averaging the middle two', () => {
    // big's others are 1, 1, 2, 4: median 1.5, so 4.5 is exactly COST_OUTLIER_RATIO.
    const flagged = costOutliers(weighed([1, 1, 2, 4, 4.5]))
    expect([...flagged.keys()]).toEqual(['k4'])
    expect(flagged.get('k4')?.ratio).toBe(COST_OUTLIER_RATIO)
  })

  it('flags nothing in a one-file batch, or for an estimate without a weight (an older backend)', () => {
    expect(costOutliers(weighed([9])).size).toBe(0)
    expect(costOutliers(weighed([1, undefined, 1, 9])).has('k3')).toBe(true)
    expect(costOutliers(weighed([undefined, undefined, 9])).size).toBe(0)
  })

  it('blocks submitting until each flagged file is included', () => {
    const rows = weighed([1, 1, 9])
    const input = { rows, multi: true, attested: true, quota: null }
    expect(missingItems(input)).toEqual(['Include or remove 1 unusually large CV'])
    expect(missingItems({ ...input, rows: rows.map((r) => ({ ...r, costConfirmed: true })) })).toEqual([])
  })
})
