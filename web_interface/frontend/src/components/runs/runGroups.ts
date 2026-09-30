import type { RunSummary } from '../../types'
import { RUN_BY_SELF } from './runFilters'

export type SortField = 'status' | 'cv' | 'started_at' | 'total_duration_seconds' | 'total_cost' | 'feedback'
export type SortDir = 'asc' | 'desc'

export const OWNER_UNKNOWN_LABEL = 'Owner not yet identified'
export const SELF_RUN_BY_LABEL = 'Faculty themselves'

/** One row of the table: the latest run of a faculty member plus their earlier reruns. */
export interface RunGroup {
  key: string
  /** Display name of the faculty member; null while stage 4 has not identified one. */
  owner: string | null
  latest: RunSummary
  /** Earlier runs of the same owner, newest first. */
  older: RunSummary[]
}

/** Case- and whitespace-insensitive key for comparing faculty names. */
export function normalizeOwner(name: string): string {
  return name.trim().replace(/\s+/g, ' ').toLowerCase()
}

function ownerOf(run: RunSummary): string | null {
  const name = run.cv_owner_name?.trim()
  return name ? name : null
}

const byStartedDesc = (a: RunSummary, b: RunSummary) => (b.started_at ?? '').localeCompare(a.started_at ?? '')

/**
 * Group runs by faculty member. Runs with no owner yet each form their own group.
 * Grouping is per loaded page: a faculty member's runs on another page are not merged in.
 */
export function groupRuns(runs: RunSummary[]): RunGroup[] {
  const buckets = new Map<string, RunSummary[]>()
  for (const run of runs) {
    const owner = ownerOf(run)
    const key = owner ? `owner:${normalizeOwner(owner)}` : `run:${run.run_id}`
    const bucket = buckets.get(key)
    if (bucket) bucket.push(run)
    else buckets.set(key, [run])
  }
  return [...buckets.entries()].map(([key, members]) => {
    const [latest, ...older] = [...members].sort(byStartedDesc)
    return { key, owner: ownerOf(latest), latest, older }
  })
}

function feedbackRank(runId: string, feedbackMap: Record<string, boolean>): number {
  if (feedbackMap[runId] === true) return 2
  return feedbackMap[runId] === false ? 1 : 0
}

/** Ascending comparison of two runs on one sort field. */
export function compareRuns(
  a: RunSummary,
  b: RunSummary,
  field: SortField,
  feedbackMap: Record<string, boolean>,
): number {
  switch (field) {
    case 'status':
      return a.status.localeCompare(b.status)
    case 'cv':
      return (ownerOf(a) ?? a.filename).localeCompare(ownerOf(b) ?? b.filename)
    case 'started_at':
      return (a.started_at ?? '').localeCompare(b.started_at ?? '')
    case 'total_duration_seconds':
      return (a.total_duration_seconds ?? 0) - (b.total_duration_seconds ?? 0)
    case 'total_cost':
      return (a.total_cost ?? 0) - (b.total_cost ?? 0)
    case 'feedback':
      return feedbackRank(a.run_id, feedbackMap) - feedbackRank(b.run_id, feedbackMap)
  }
}

/** Display label for who ran a run; null when unknown. */
export function runByLabel(run: RunSummary, currentUserId: number | undefined): string | null {
  if (run.submission_type === 'own_cv') return SELF_RUN_BY_LABEL
  if (!run.run_by) return null
  return run.run_by.id === currentUserId ? `${run.run_by.display_name} (you)` : run.run_by.display_name
}

/** Value to put in the Run by filter for this run's runner; null when unknown. */
export function runByFilterValue(run: RunSummary): string | null {
  if (run.submission_type === 'own_cv') return RUN_BY_SELF
  return run.run_by ? String(run.run_by.id) : null
}
