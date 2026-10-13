import type { CorrectedCopySummary, RunSummary } from '../../types'
import { formatDate, formatRelativeDate } from '../../utils'

/** Run status the backend sends once a run has finished (RunState.COMPLETE). */
const RUN_COMPLETE = 'complete'

/** What the Feedback column shows for one run. */
/** 'awaiting' = finished with no feedback, but not the viewer's to review (an admin
 *  looking at someone else's run), so it gets no call-to-action. */
export type FeedbackState = 'given' | 'earlier' | 'needed' | 'awaiting' | 'none'

/** Ascending sort rank per state: none < awaiting < needed < earlier < given. */
const FEEDBACK_RANK: Record<FeedbackState, number> = { none: 0, awaiting: 1, needed: 2, earlier: 3, given: 4 }

export const FEEDBACK_VALUE_LABEL = { given: 'Feedback given', needed: 'No feedback yet' } as const

/** The viewer can review this run: admins only their own runs, everyone else every run in their list. */
export function canReviewRun(run: RunSummary, currentUserId: number | undefined, isAdmin: boolean): boolean {
  return !isAdmin || (currentUserId !== undefined && run.run_by?.id === currentUserId)
}

/**
 * State for one run. `earlierGiven` is true when an older run of the same faculty
 * member has feedback; it only changes a finished run with no feedback of its own.
 */
export function feedbackState(
  run: RunSummary,
  currentUserId: number | undefined,
  isAdmin: boolean,
  earlierGiven = false,
): FeedbackState {
  if (run.feedback.count > 0) return 'given'
  if (run.status !== RUN_COMPLETE) return 'none'
  if (earlierGiven) return 'earlier'
  return canReviewRun(run, currentUserId, isAdmin) ? 'needed' : 'awaiting'
}

export function feedbackRank(state: FeedbackState): number {
  return FEEDBACK_RANK[state]
}

/** Tooltip for a "Feedback given" cell: "2 reviews · last 3 hours ago", plus reviewers for admins. */
export function feedbackGivenTitle(run: RunSummary, isAdmin: boolean): string {
  const { count, last_at, reviewers } = run.feedback
  const parts = [`${count} ${count === 1 ? 'review' : 'reviews'}`]
  if (last_at) parts.push(`last ${formatRelativeDate(last_at).display}`)
  if (isAdmin && reviewers && reviewers.length > 0) {
    parts.push(`by ${reviewers.map((r) => r.display_name).join(', ')}`)
  }
  return parts.join(' · ')
}

/** A run with a reviewer's corrected copy: the Feedback column's second line and the status pill. */
export const CORRECTED_LABEL = 'Corrected'

/** Shown for a copy whose uploader is not known (stored before copies were kept per uploader). */
export const UNKNOWN_UPLOADER_LABEL = 'unknown uploader'

/** The runs list's line for a run's corrected copies: '' for none, "Corrected"
 *  for one, "Corrected ×2" for two. */
export function correctedLabel(copies: CorrectedCopySummary[] | undefined): string {
  const count = copies?.length ?? 0
  if (count === 0) return ''
  return count === 1 ? CORRECTED_LABEL : `${CORRECTED_LABEL} \u00d7${count}`
}

/** "Pat Example (owner)", or the unknown-uploader label. */
export function uploaderLabel(copy: CorrectedCopySummary): string {
  if (!copy.uploader) return UNKNOWN_UPLOADER_LABEL
  return copy.role ? `${copy.uploader} (${copy.role})` : copy.uploader
}

/** Tooltip for the Corrected line: one copy per line, uploader, role and date. */
export function correctedTitle(copies: CorrectedCopySummary[]): string {
  return copies.map((copy) => `${uploaderLabel(copy)} · ${formatDate(copy.uploaded_at)}`).join('\n')
}

/** The run page's corrected-copies section; the runs list's Corrected line links to it. */
export const CORRECTED_COPIES_ANCHOR = 'corrected-copies'

export function correctedCopiesPath(runId: string): string {
  return `/run/${encodeURIComponent(runId)}#${CORRECTED_COPIES_ANCHOR}`
}
