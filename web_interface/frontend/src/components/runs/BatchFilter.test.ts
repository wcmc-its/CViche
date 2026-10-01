import { describe, expect, it } from 'vitest'
import { batchLabel, buildBatchModel, formatBatchWhen } from './BatchFilter'
import type { BatchSummary } from '../../types'

// Local times, so the day boundaries hold in any timezone.
const NOW = new Date(2026, 9, 1, 21, 0)
const at = (day: number, hour: number, minute: number) => new Date(2026, 9, day, hour, minute).toISOString()

// Fewer runs than files sent: the label counts the runs, as the batch view's title does.
const BATCH: BatchSummary = {
  id: 'BQXZKD',
  submitted_by: { id: 7, display_name: 'Test Admin', cwid: null, email: null, department: null },
  created_at: at(1, 18, 5),
  run_count: 28,
  files_submitted: 30,
}
// The time as this machine's locale writes it (12- or 24-hour), so the test holds in any locale.
const clock = (hour: number, minute: number) =>
  new Date(2026, 9, 1, hour, minute).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

describe('batch filter labels', () => {
  it('says Today or Yesterday for recent batches', () => {
    expect(formatBatchWhen(at(1, 18, 5), NOW)).toBe(`Today ${clock(18, 5)}`)
    expect(formatBatchWhen(new Date(2026, 8, 30, 9, 14).toISOString(), NOW)).toBe(`Yesterday ${clock(9, 14)}`)
    expect(formatBatchWhen(new Date(2026, 8, 20, 9, 14).toISOString(), NOW)).not.toMatch(/^(Today|Yesterday)/)
  })

  it('labels an option "<when> · N CVs" with the submitter as meta', () => {
    expect(batchLabel(BATCH, NOW)).toBe(`Today ${clock(18, 5)} · 28 CVs`)
    const [item] = buildBatchModel([BATCH], 7).items
    expect(item.id).toBe('BQXZKD')
    expect(item.meta).toBe('Submitted by Test Admin (you)')
    expect(buildBatchModel([BATCH], 99).items[0].meta).toBe('Submitted by Test Admin')
    expect(item.search).toContain('bqxzkd')
  })
})
