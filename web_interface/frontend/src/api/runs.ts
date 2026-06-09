import { api } from './client'
import type { ApiError } from './client'
import { runRoutes } from './routes'
import type { RunStatus, PaginatedRuns, FeedbackStatus } from '../types'

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

export async function restartRun(runId: string): Promise<{ new_run_id: string }> {
  return api.post<{ new_run_id: string }>(runRoutes.restart(runId))
}

export async function retryStep(runId: string, step: number): Promise<void> {
  await api.post(runRoutes.retryStep(runId, step))
}

export async function getRuns(offset: number, limit: number): Promise<PaginatedRuns> {
  const res = await api.getRaw(runRoutes.list(offset, limit))
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

export async function getFeedbackStatuses(): Promise<FeedbackStatus[]> {
  return api.get<FeedbackStatus[]>(runRoutes.feedbackStatus())
}

export async function startRun(runId: string): Promise<void> {
  await api.post(runRoutes.start(runId))
}
