import { AlertCircle, Check, Loader2 } from 'lucide-react'
import { doneBody, doneTitle, permanentNote, retryLabel } from './batchRows'
import type { SendProgress } from './batchRows'

const CARD = 'rounded-xl border border-sand-300 bg-white shadow-[0_1px_2px_rgba(60,40,10,0.05)]'
const SECONDARY_BUTTON =
  'rounded-lg border border-sand-400 bg-white px-3.5 py-[9px] text-[13px] font-medium text-gray-900 hover:bg-sand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500'

/** "Uploading N of M · Two files at a time", the bar, and the keep-tab-open note. */
export function UploadingBanner({ progress }: { progress: SendProgress }) {
  const pct = Math.round((progress.sent / Math.max(1, progress.total)) * 100)
  return (
    <section className="flex flex-col gap-2.5 rounded-xl border border-primary-200 bg-primary-50 px-5 py-4" role="status" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="flex items-center gap-2 font-semibold text-[#1E3A8A]">
          <Loader2 className="h-4 w-4 animate-spin text-primary-600" aria-hidden="true" />
          Uploading {progress.sent} of {progress.total}
        </span>
        <span className="text-[13px] text-[#1E40AF]">Two files at a time</span>
      </div>
      <div className="h-2 overflow-hidden rounded bg-primary-100">
        <div className="h-full bg-primary-600 transition-[width] duration-300" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-[13px] text-[#1E40AF]">
        Keep this tab open until the uploads finish. CVs that are already queued keep processing if you leave.
      </p>
    </section>
  )
}

interface DoneCardProps {
  progress: SendProgress
  /** Runs already waiting in the batch queue when this batch was submitted. */
  aheadBefore: number
  /** About when the last run should finish; null when the queue can't say. */
  finishMinutes: number | null
  onViewBatch: () => void
  onRetry: () => void
  onNewBatch: () => void
}

/** The done card: queued count, when the last should finish, and what to do next. */
export function DoneCard({ progress, aheadBefore, finishMinutes, onViewBatch, onRetry, onNewBatch }: DoneCardProps) {
  const failed = progress.retryable + progress.permanent > 0
  return (
    <section className={`${CARD} flex flex-col gap-3.5 px-6 py-5`} aria-label="Batch submitted">
      <div className="flex items-start gap-3.5">
        <span className={`flex h-9 w-9 flex-none items-center justify-center rounded-full ${failed ? 'bg-warning-100' : 'bg-success-100'}`}>
          {failed
            ? <AlertCircle className="h-[18px] w-[18px] text-amber-700" aria-hidden="true" />
            : <Check className="h-[18px] w-[18px] text-success-700" strokeWidth={2.6} aria-hidden="true" />}
        </span>
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="text-lg font-semibold text-gray-900">{doneTitle(progress)}</h2>
          <p className="text-gray-600">{doneBody(aheadBefore, finishMinutes)}</p>
          {progress.permanent > 0 && <p className="text-[13px] text-error-700">{permanentNote(progress.permanent)}</p>}
        </div>
      </div>
      <div className="flex flex-wrap gap-2 sm:pl-[50px]">
        <button
          type="button"
          onClick={onViewBatch}
          className="rounded-lg bg-primary-600 px-4 py-2.5 text-[13px] font-semibold text-white hover:bg-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2"
        >
          View batch in Runs
        </button>
        {progress.retryable > 0 && (
          <button type="button" onClick={onRetry} className={SECONDARY_BUTTON}>{retryLabel(progress.retryable)}</button>
        )}
        <button type="button" onClick={onNewBatch} className={SECONDARY_BUTTON}>Start another batch</button>
      </div>
    </section>
  )
}
