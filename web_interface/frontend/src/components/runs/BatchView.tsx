import { useEffect, useState } from 'react'
import { Link2, Loader2 } from 'lucide-react'
import type { ApiError } from '../../api/client'
import { getBatch, getQueue } from '../../api/batches'
import type { BatchDetail, BatchRunRow, BatchStatusCounts, QueueLane } from '../../types'
import { formatMinutes } from '../../utils'
import { statusLabel, statusLabelColor } from '../../utils/status'
import StatusIcon from '../shared/StatusIcon'
import { BATCH_PARAM } from './runFilters'
import { cvCount, formatBatchWhen, submitterName } from './BatchFilter'
import { NO_SCORE_TEXT } from './runQuality'

/** How long "Link copied" shows after Copy link. */
export const COPY_FEEDBACK_MS = 1600
const NOT_FOUND = 404
export const BATCH_NOT_FOUND = "This batch doesn't exist, or you don't have access to it."
const BATCH_LOAD_FAILED = 'Unable to load this batch. Please refresh the page to try again.'
export const FACULTY_PENDING = 'Read from the CV when it runs'
/** How often the view re-reads the batch while any of its runs is queued or running. */
export const BATCH_REFRESH_MS = 30_000

/** Status-bar segments in the design's order; a status with no runs is left out. Each is
 *  labelled with statusLabel(), so the bar and the rows name a status the same way. */
const SEGMENTS: { key: keyof BatchStatusCounts; color: string }[] = [
  { key: 'complete', color: 'bg-success-700' },
  { key: 'running', color: 'bg-primary-600' },
  { key: 'queued', color: 'bg-[#D6CCB6]' },
  { key: 'failed', color: 'bg-error-600' },
  { key: 'cancelled', color: 'bg-orange-600' },
  { key: 'created', color: 'bg-gray-300' },
]

const isActive = (counts: BatchStatusCounts): boolean => counts.queued + counts.running > 0

const OPENABLE = new Set(['complete', 'running'])

/** "Up to 3 run at once. The last should finish in about X." or "All runs finished." */
export function timeLeftText(counts: BatchStatusCounts, lane: QueueLane | null): string {
  if (!isActive(counts)) return 'All runs finished.'
  const parts: string[] = []
  if (lane?.workers) parts.push(`Up to ${lane.workers} run at once.`)
  if (lane?.est_wait_minutes != null) parts.push(`The last should finish in about ${formatMinutes(lane.est_wait_minutes)}.`)
  return parts.join(' ')
}

/** "N files in this batch have no run." when runs fall short of the files sent. Not "didn't
 *  upload": a file can reach storage and still fail to become a run (#802). */
export function missingFilesNote(batch: Pick<BatchDetail, 'run_count' | 'files_submitted'>): string {
  const n = batch.files_submitted - batch.run_count
  if (n <= 0) return ''
  return n === 1 ? '1 file in this batch has no run.' : `${n} files in this batch have no run.`
}

interface Loaded {
  batch: BatchDetail | null
  lane: QueueLane | null
  error: string | null
}

/** The batch and the batch queue's lane, re-read every BATCH_REFRESH_MS while runs are
 *  queued or running. A queue that can't be read only loses the time line; a failed
 *  re-read keeps the last batch shown. */
function useBatchDetail(batchId: string): Loaded {
  const [state, setState] = useState<Loaded>({ batch: null, lane: null, error: null })
  const [reads, setReads] = useState(0)
  useEffect(() => {
    setState({ batch: null, lane: null, error: null })
  }, [batchId])
  useEffect(() => {
    let cancelled = false
    getBatch(batchId)
      .then((batch) => { if (!cancelled) setState((s) => ({ ...s, batch, error: null })) })
      .catch((err: ApiError) => {
        console.error('Error fetching batch:', err)
        if (cancelled) return
        setState((s) => (s.batch ? s : { ...s, error: err?.status === NOT_FOUND ? BATCH_NOT_FOUND : BATCH_LOAD_FAILED }))
      })
    getQueue()
      .then((queue) => { if (!cancelled) setState((s) => ({ ...s, lane: queue.batch })) })
      .catch((err) => console.error('Queue overview unavailable; the batch view omits its time line', err))
    return () => { cancelled = true }
  }, [batchId, reads])
  const active = state.batch !== null && isActive(state.batch.status_counts)
  useEffect(() => {
    if (!active) return
    const timer = setTimeout(() => setReads((n) => n + 1), BATCH_REFRESH_MS)
    return () => clearTimeout(timer)
    // A new read (reads) or a new answer (state.batch) restarts the wait, so a failed re-read is retried too.
  }, [active, reads, state.batch])
  return state
}

function CopyLinkButton({ batchId }: { batchId: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), COPY_FEEDBACK_MS)
    return () => clearTimeout(timer)
  }, [copied])
  const copy = async () => {
    const url = `${window.location.origin}/runs?${BATCH_PARAM}=${encodeURIComponent(batchId)}`
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
    } catch (err) {
      console.error('Copying the batch link failed', err)
    }
  }
  return (
    <button
      type="button"
      onClick={() => void copy()}
      className="flex items-center gap-1.5 rounded-lg border border-sand-400 bg-white px-3.5 py-[9px] text-[13px] font-medium text-gray-900 hover:bg-sand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
    >
      <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
      {copied ? 'Link copied' : 'Copy link'}
    </button>
  )
}

function StatusBar({ counts, timeLeft }: { counts: BatchStatusCounts; timeLeft: string }) {
  const shown = SEGMENTS.filter((s) => counts[s.key] > 0)
  return (
    <>
      <div className="flex h-2.5 gap-[3px]" aria-hidden="true">
        {shown.map((s) => <div key={s.key} className={`rounded-[3px] ${s.color}`} style={{ flex: counts[s.key] }} />)}
      </div>
      <div className="flex flex-wrap justify-between gap-3 text-[13px]">
        <div className="flex flex-wrap gap-4" data-testid="batch-status-counts">
          {shown.map((s) => (
            <span key={s.key} className="flex items-center gap-1.5 text-gray-700">
              <span className={`h-2 w-2 rounded-sm ${s.color}`} aria-hidden="true" />
              {statusLabel(s.key)} <strong className="tabular-nums">{counts[s.key]}</strong>
            </span>
          ))}
        </div>
        {timeLeft && <span className="text-gray-500">{timeLeft}</span>}
      </div>
    </>
  )
}

interface RowProps {
  row: BatchRunRow
  index: number
  allRunsView: boolean
  grid: string
  onSelectRun: (runId: string) => void
}

function BatchRunLine({ row, index, allRunsView, grid, onSelectRun }: RowProps) {
  const openable = OPENABLE.has(row.status)
  const open = openable ? () => onSelectRun(row.run_id) : undefined
  return (
    <li
      onClick={open}
      onKeyDown={openable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectRun(row.run_id) } } : undefined}
      tabIndex={openable ? 0 : undefined}
      role={openable ? 'link' : undefined}
      data-testid="batch-run"
      className={`${grid} items-center border-b border-sand-200 px-[22px] py-[11px] last:border-b-0 ${openable ? 'cursor-pointer hover:bg-sand-50' : ''} ${row.status === 'running' ? 'bg-primary-50' : 'bg-white'}`}
    >
      <span className="text-right text-xs tabular-nums text-gray-400">{index + 1}</span>
      <span title={row.filename} className="truncate font-medium text-gray-900">{row.filename}</span>
      {row.cv_owner_name
        ? <span className="text-sm text-gray-900">{row.cv_owner_name}</span>
        : <span className="text-[13px] italic text-gray-400">{FACULTY_PENDING}</span>}
      <span className={`flex items-center gap-1.5 text-[13px] font-medium ${statusLabelColor(row.status)}`}>
        <StatusIcon status={row.status} />
        {statusLabel(row.status)}
        {row.status === 'queued' && row.queue_position !== null && (
          <span className="font-normal text-gray-400">{row.queue_position} ahead</span>
        )}
      </span>
      {allRunsView && (
        <span className={`font-semibold tabular-nums ${row.quality_score === null ? 'text-gray-400' : 'text-gray-900'}`}>
          {row.quality_score ?? NO_SCORE_TEXT}
        </span>
      )}
    </li>
  )
}

interface BatchViewProps {
  batchId: string
  allRunsView: boolean
  currentUserId: number | undefined
  onSelectRun: (runId: string) => void
}

/** The batch view the Runs page shows when ?batch= is set: header, status bar, one row per run. */
export default function BatchView({ batchId, allRunsView, currentUserId, onSelectRun }: BatchViewProps) {
  const { batch, lane, error } = useBatchDetail(batchId)
  const card = 'mt-4 overflow-hidden rounded-xl border border-sand-300 bg-white shadow-[0_1px_2px_rgba(60,40,10,0.05)]'
  if (error) return <section className={`${card} px-5 py-8 text-center text-sm text-gray-500`}>{error}</section>
  if (!batch) {
    return (
      <div className="py-4 text-center text-sm text-gray-500">
        <Loader2 className="mr-2 inline h-4 w-4 animate-spin" aria-hidden="true" />
        Loading batch...
      </div>
    )
  }
  const grid = `grid gap-3 ${allRunsView ? 'grid-cols-[28px_minmax(0,1.6fr)_minmax(0,1fr)_170px_70px]' : 'grid-cols-[28px_minmax(0,1.6fr)_minmax(0,1fr)_170px]'}`
  const note = missingFilesNote(batch)
  return (
    <section className={card} aria-label="Batch">
      <div className="flex flex-col gap-3.5 border-b border-sand-200 px-[22px] py-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">Batch</div>
            <h2 className="mt-0.5 text-xl font-semibold text-gray-900">{cvCount(batch.run_count)}</h2>
            <div className="mt-0.5 text-[13px] text-gray-500">
              Submitted {formatBatchWhen(batch.created_at)} by {submitterName(batch, currentUserId)} · <span className="font-mono">{batch.id}</span>
            </div>
          </div>
          <CopyLinkButton batchId={batch.id} />
        </div>
        <StatusBar counts={batch.status_counts} timeLeft={timeLeftText(batch.status_counts, lane)} />
      </div>
      <div className="overflow-x-auto">
        <div className="min-w-[560px]">
          <div className={`${grid} border-b border-sand-200 bg-sand-50 px-[22px] py-2.5 text-xs font-medium text-gray-500`}>
            <span className="text-right">#</span><span>File</span><span>Faculty</span><span>Status</span>{allRunsView && <span>Score</span>}
          </div>
          <ul aria-label="Runs in this batch">
            {batch.runs.map((row, i) => (
              <BatchRunLine key={row.run_id} row={row} index={i} allRunsView={allRunsView} grid={grid} onSelectRun={onSelectRun} />
            ))}
          </ul>
        </div>
      </div>
      {note && <div className="bg-sand-50 px-[22px] py-3 text-[13px] text-gray-500">{note}</div>}
    </section>
  )
}
