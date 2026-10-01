import type { ApiError } from '../../api/client'
import type { Estimate, QueueLane, QuotaInfo } from '../../types'
import { formatMinutes } from '../../utils'

/** Most files one batch may hold; POST /api/batches enforces the same cap. */
export const MAX_BATCH_FILES = 50
/** Uploads in flight at once ("Two files at a time"). */
export const MAX_UPLOADS_IN_FLIGHT = 2

export const NOT_DOCX_REASON = 'Not a .docx file'
export const PERMANENT_FAILURE_SUFFIX = 'Fix it and upload it on its own.'
/** Reason shown for a failure with no server message (the request never got an answer). */
export const INTERRUPTED_REASON = 'Upload interrupted'
const START_FAILED_REASON = 'Uploaded, but processing could not start'

const SERVER_ERROR_MIN_STATUS = 500
const TOO_MANY_REQUESTS = 429
const SECONDS_PER_MINUTE = 60

export type RowState = 'ready' | 'waiting' | 'uploading' | 'queued' | 'failed'

export interface RowFailure {
  reason: string
  /** Network errors, 5xx and 429 can be retried; a 4xx validation error cannot. */
  retryable: boolean
}

/** One selected file of a batch. */
export interface BatchRow {
  key: string
  file: File
  /** Why the file won't be submitted (client check or the estimate's per-file error); null when valid. */
  invalidReason: string | null
  /** undefined while the estimate is pending; null when it could not be had. */
  estimate: Estimate | null | undefined
  state: RowState
  /** Set once the upload created the run, so a retry only re-starts it. */
  runId: string | null
  failure: RowFailure | null
}

/** A new row; a non-.docx file is marked "won't be submitted" at once. `key` must be unique on the page. */
export function makeRow(file: File, key: string, estimate: Estimate | null | undefined = undefined): BatchRow {
  const isDocx = file.name.toLowerCase().endsWith('.docx')
  return {
    key,
    file,
    invalidReason: isDocx ? null : NOT_DOCX_REASON,
    estimate: isDocx ? estimate : null,
    state: 'ready',
    runId: null,
    failure: null,
  }
}

export const isValidRow = (row: BatchRow): boolean => row.invalidReason === null

/** Sort a failed request into retryable (no answer, 5xx, 429) or permanent (other 4xx). */
export function classifyFailure(err: unknown, fallbackReason: string): RowFailure {
  const { status, message } = (err ?? {}) as Partial<ApiError>
  const retryable = status === undefined || status >= SERVER_ERROR_MIN_STATUS || status === TOO_MANY_REQUESTS
  const reason = status === undefined ? INTERRUPTED_REASON : message || fallbackReason
  return { reason, retryable }
}

export const startFailure = (err: unknown): RowFailure => classifyFailure(err, START_FAILED_REASON)

/** The row's note under its status: the skip reason, or the failure (plus "Fix it…" when permanent). */
export function rowNote(row: BatchRow): string {
  if (row.invalidReason) return row.invalidReason
  if (row.state !== 'failed' || !row.failure) return ''
  if (row.failure.retryable) return row.failure.reason
  return `${row.failure.reason.replace(/\.\s*$/, '')}. ${PERMANENT_FAILURE_SUFFIX}`
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)

/** Midpoint of an estimate's time range, in minutes. */
export function estimateMinutes(estimate: Estimate): number {
  return (estimate.estimated_time_seconds_min + estimate.estimated_time_seconds_max) / 2 / SECONDS_PER_MINUTE
}

/** Midpoint of an estimate's cost range; null when cost is hidden (non-admin). */
export function estimateCost(estimate: Estimate): number | null {
  if (estimate.estimated_cost_min === null || estimate.estimated_cost_max === null) return null
  return (estimate.estimated_cost_min + estimate.estimated_cost_max) / 2
}

export interface Totals {
  minutes: number
  cost: number | null
}

export function totalsOf(rows: BatchRow[]): Totals {
  let minutes = 0
  let cost: number | null = null
  for (const row of rows) {
    if (!row.estimate) continue
    minutes += estimateMinutes(row.estimate)
    const each = estimateCost(row.estimate)
    if (each !== null) cost = (cost ?? 0) + each
  }
  return { minutes, cost }
}

/** "28 to submit · 2 skipped" */
export function countText(rows: BatchRow[]): string {
  if (!rows.length) return ''
  const valid = rows.filter(isValidRow).length
  const skipped = rows.length - valid
  return `${valid} to submit${skipped ? ` · ${skipped} skipped` : ''}`
}

/** "30 CVs · about 6 h 20 m of processing" plus "· ~$49.40" for admins. */
export function estimateText(count: number, totals: Totals, showCost: boolean): string {
  const cost = showCost && totals.cost !== null ? ` · ~$${totals.cost.toFixed(2)}` : ''
  return `${count} ${plural(count, 'CV', 'CVs')} · about ${formatMinutes(totals.minutes)} of processing${cost}`
}

/** The finish-time line for a queue lane (batch lane for a batch, single lane for one file). */
export function finishText(lane: QueueLane, totalMinutes: number): string {
  const runAt = lane.workers === null ? '' : lane.workers > 1 ? ` and run ${lane.workers} at a time` : ' and run one at a time'
  const head = `Runs go into a shared queue behind ${lane.ahead} ${plural(lane.ahead, 'other', 'others')}${runAt}.`
  if (lane.workers === null || lane.est_wait_minutes === null) return head
  return `${head} Expect the last to finish in about ${formatMinutes(lane.est_wait_minutes + totalMinutes / lane.workers)}.`
}

/** "7 of 10 runs left today · 38 of 50 this month"; '' when neither limit applies. */
export function quotaText(quota: QuotaInfo): string {
  const parts: string[] = []
  if (quota.daily_limit !== null && quota.daily_remaining !== null) {
    parts.push(`${quota.daily_remaining} of ${quota.daily_limit} runs left today`)
  }
  if (quota.monthly_limit !== null && quota.monthly_remaining !== null) {
    parts.push(`${quota.monthly_remaining} of ${quota.monthly_limit} this month`)
  }
  return parts.join(' · ')
}

/** The quota line in "missing" when a batch is larger than the runs left. '' when it fits. */
export function quotaShortfall(count: number, quota: QuotaInfo | null): string {
  if (!quota || quota.is_admin) return ''
  const daily = quota.daily_remaining
  if (daily !== null && count > daily) {
    const extra = count - daily
    return `You have ${daily} ${plural(daily, 'run', 'runs')} left today. Remove ${extra} ${plural(extra, 'file', 'files')}, or submit the rest tomorrow.`
  }
  const monthly = quota.monthly_remaining
  if (monthly !== null && count > monthly) {
    const extra = count - monthly
    return `You have ${monthly} ${plural(monthly, 'run', 'runs')} left this month. Remove ${extra} ${plural(extra, 'file', 'files')}, or submit the rest next month.`
  }
  return ''
}

export interface MissingInput {
  rows: BatchRow[]
  multi: boolean
  attested: boolean
  quota: QuotaInfo | null
}

/** What still blocks submitting, in the design's order; the button is disabled while any remain. */
export function missingItems({ rows, multi, attested, quota }: MissingInput): string[] {
  const missing: string[] = []
  const valid = rows.filter(isValidRow).length
  if (!rows.length) missing.push(multi ? 'Add at least one CV' : 'Add a CV')
  else if (!valid) missing.push('None of these files can be processed')
  if (valid > MAX_BATCH_FILES) missing.push(`Batches are limited to ${MAX_BATCH_FILES} files. Remove ${valid - MAX_BATCH_FILES}.`)
  const shortfall = valid ? quotaShortfall(valid, quota) : ''
  if (shortfall) missing.push(shortfall)
  if (!attested) missing.push('Agree to the upload terms')
  return missing
}

/** Rows that finished sending (queued or failed) out of those being sent. */
export interface SendProgress {
  sent: number
  total: number
  queued: number
  retryable: number
  permanent: number
}

export function sendProgress(rows: BatchRow[]): SendProgress {
  const valid = rows.filter(isValidRow)
  const failed = valid.filter((r) => r.state === 'failed')
  const retryable = failed.filter((r) => r.failure?.retryable).length
  const queued = valid.filter((r) => r.state === 'queued').length
  return { sent: queued + failed.length, total: valid.length, queued, retryable, permanent: failed.length - retryable }
}

export function doneTitle(p: SendProgress): string {
  return p.queued === p.total ? `All ${p.queued} CVs queued` : `${p.queued} of ${p.total} CVs queued`
}

/** Body of the done card; the finish sentence is left out when the wait is unknown. */
export function doneBody(aheadBefore: number, finishMinutes: number | null): string {
  const finish = finishMinutes === null ? '' : ` The last should finish in about ${formatMinutes(finishMinutes)}.`
  return `They run one after another behind ${aheadBefore} ${plural(aheadBefore, 'run', 'runs')} already in the queue.${finish} You can close this tab.`
}

export function permanentNote(n: number): string {
  return n === 1
    ? "1 file couldn't be opened. Fix it and upload it on its own."
    : `${n} files couldn't be opened. Fix them and upload them on their own.`
}

export const retryLabel = (n: number): string => `Retry ${n} failed ${plural(n, 'upload', 'uploads')}`

/** Run `worker` over `items` with at most `limit` in flight; `worker` must not reject. */
export async function runPool<T>(items: T[], limit: number, worker: (item: T) => Promise<void>): Promise<void> {
  let next = 0
  const lane = async () => {
    while (next < items.length) {
      const item = items[next]
      next += 1
      await worker(item)
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane))
}
