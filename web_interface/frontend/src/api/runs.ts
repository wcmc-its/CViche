import { api } from './client'
import type { ApiError } from './client'
import { runRoutes } from './routes'
import type {
  RunStatus, PaginatedRuns, FeedbackStatus, RunListParams, RunFilterOptions,
  RunQualityReport, RunReviewNote, StatusFilterCounts,
} from '../types'

export async function getRunStatus(runId: string): Promise<RunStatus> {
  return api.get<RunStatus>(runRoutes.status(runId))
}

export async function getRunStep(runId: string, step: number): Promise<any> {
  return api.get(runRoutes.step(runId, step))
}

export async function getPromptLogs(runId: string, step: number): Promise<any> {
  return api.get(runRoutes.promptLogs(runId, step))
}

export async function getRunDataJson(runId: string, filename: string): Promise<any> {
  // Backend resolves data files by basename and rejects absolute paths; some
  // callers pass the full output path recorded in Step.output_files, so strip
  // any directory prefix here to avoid a 400 from the path-traversal guard.
  const name = filename.split('/').pop() || filename
  return api.get(runRoutes.dataJson(runId, name))
}

export async function cancelRun(runId: string): Promise<void> {
  await api.post(runRoutes.cancel(runId))
}

export async function restartRun(runId: string): Promise<{ run_id: string }> {
  return api.post<{ run_id: string }>(runRoutes.restart(runId))
}

export async function retryStep(runId: string, step: number): Promise<void> {
  await api.post(runRoutes.retryStep(runId, step))
}

/** Serialise admin scope/filter params; unset and empty values are omitted. */
export function buildRunListQuery(params: RunListParams = {}): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  return query.toString()
}

export async function getRuns(
  offset: number,
  limit: number,
  params?: RunListParams,
): Promise<PaginatedRuns> {
  const res = await api.getRaw(runRoutes.list(offset, limit, buildRunListQuery(params)))
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const message = body?.detail || 'Failed to load runs'
    throw { status: res.status, message } as ApiError
  }
  const data = await res.json()
  // Handle both paginated response { runs, total, has_more } and legacy array response
  if (Array.isArray(data)) {
    return { runs: data, total: data.length, has_more: false }
  }
  return { runs: data.runs || [], total: data.total || 0, has_more: data.has_more || false }
}

/** Admin only: options and run counts for the Department / Faculty / Run by
 *  filters. Defaults to scope 'all'; pass the active filters to cascade counts. */
export async function getRunFilterOptions(params: RunListParams = {}): Promise<RunFilterOptions> {
  return api.get<RunFilterOptions>(
    runRoutes.filterOptions(buildRunListQuery({ scope: 'all', ...params })),
  )
}

/** Status pill counts over the caller's own runs; members have no filter-options. */
export async function getMyStatusCounts(): Promise<StatusFilterCounts> {
  return api.get<StatusFilterCounts>(runRoutes.myStatusCounts())
}

export async function getFeedbackStatuses(): Promise<FeedbackStatus[]> {
  return api.get<FeedbackStatus[]>(runRoutes.feedbackStatus())
}

export async function startRun(runId: string): Promise<void> {
  await api.post(runRoutes.start(runId))
}

export interface Capacity {
  available: boolean
  active: number
  limit: number
}

// Read-only, advisory snapshot of this pod's run-admission capacity (issue
// #177). The upload flow uses it to avoid spending an upload when the pod is
// already at capacity. It is NOT authoritative -- the start-time gate still
// applies -- so callers should fail open if this request errors.
export async function getCapacity(): Promise<Capacity> {
  return api.get<Capacity>(runRoutes.capacity())
}

/** Admin only: score breakdown and run-doctor findings for a run. Either part
 *  is null when its artifact was never stored. */
export async function getRunQuality(runId: string): Promise<RunQualityReport> {
  return api.get<RunQualityReport>(runRoutes.quality(runId))
}

/** Run owner or admin: whether the run may need cleanup. Never the score. */
export async function getRunReviewNote(runId: string): Promise<RunReviewNote> {
  return api.get<RunReviewNote>(runRoutes.reviewNote(runId))
}
