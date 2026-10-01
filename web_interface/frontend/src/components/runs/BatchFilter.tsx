import type { BatchSummary } from '../../types'
import { formatDate } from '../../utils'
import RunFilterCombo from './RunFilterCombo'
import type { ComboModel } from './runFilterOptions'

const MS_PER_DAY = 24 * 60 * 60 * 1000

const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()

/** "Today 6:05 PM", "Yesterday 9:14 AM", else the app's usual date. `now` is passed for tests. */
export function formatBatchWhen(iso: string, now: Date = new Date()): string {
  const when = new Date(iso)
  const time = when.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  if (sameDay(when, now)) return `Today ${time}`
  if (sameDay(when, new Date(now.getTime() - MS_PER_DAY))) return `Yesterday ${time}`
  return formatDate(iso)
}

export const cvCount = (n: number): string => `${n} ${n === 1 ? 'CV' : 'CVs'}`

/** "Today 6:05 PM · 30 CVs" */
export function batchLabel(batch: BatchSummary, now?: Date): string {
  return `${formatBatchWhen(batch.created_at, now)} · ${cvCount(batch.run_count)}`
}

/** The submitter's name, with "(you)" for the viewer's own batch. */
export function submitterName(batch: Pick<BatchSummary, 'submitted_by'>, currentUserId: number | undefined): string {
  const by = batch.submitted_by
  if (!by) return 'Unknown'
  return by.id === currentUserId ? `${by.display_name} (you)` : by.display_name
}

export function buildBatchModel(batches: BatchSummary[], currentUserId: number | undefined): ComboModel {
  return {
    pinned: [{ id: '', label: 'Any batch', search: '' }],
    items: batches.map((b) => {
      const label = batchLabel(b)
      const by = submitterName(b, currentUserId)
      return { id: b.id, label, meta: `Submitted by ${by}`, search: [label, by, b.id].join(' ').toLowerCase() }
    }),
  }
}

interface BatchFilterComboProps {
  batches: BatchSummary[]
  batchId: string
  currentUserId: number | undefined
  onPick: (batchId: string) => void
}

/** The "Batch" filter beside Department, Faculty and Run by; shown to anyone with a batch. */
export function BatchFilterCombo({ batches, batchId, currentUserId, onPick }: BatchFilterComboProps) {
  const selected = batches.find((b) => b.id === batchId)
  return (
    <RunFilterCombo
      label="Batch"
      valueLabel={batchId ? (selected ? batchLabel(selected) : batchId) : 'Any'}
      activeId={batchId}
      placeholder="Search batches"
      model={buildBatchModel(batches, currentUserId)}
      onPick={onPick}
    />
  )
}
