import type { RunSummary } from '../../types'
import { formatRelativeDate } from '../../utils'

/** Run status the backend sends once a run has finished (RunState.COMPLETE). */
const RUN_COMPLETE = 'complete'

/** What the Feedback column shows for one run. */
export type FeedbackState = 'given' | 'earlier' | 'needed' | 'none'

/** Ascending sort rank per state: none < needed < earlier < given. */
const FEEDBACK_RANK: Record<FeedbackState, number> = { none: 0, needed: 1, earlier: 2, given: 3 }

export const FEEDBACK_VALUE_LABEL = { given: 'Feedback given', needed: 'Needs feedback' } as const

/** The viewer can review this run: admins only their own runs, everyone else every run in their list. */
export function canReviewRun(run: RunSummary, currentUserId: number | undefined, isAdmin: boolean): boolean {
  return !isAdmin || (currentUserId !== undefined && run.run_by?.id === currentUserId)
}

/**
 * State for one run. `earlierGiven` is true when an older run of the same faculty
 * member has feedback; it only changes a run that would otherwise say "Needs feedback".
 */
export function feedbackState(
  run: RunSummary,
  currentUserId: number | undefined,
  isAdmin: boolean,
  earlierGiven = false,
): FeedbackState {
  if (run.feedback.count > 0) return 'given'
  if (run.status !== RUN_COMPLETE || !canReviewRun(run, currentUserId, isAdmin)) return 'none'
  return earlierGiven ? 'earlier' : 'needed'
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
