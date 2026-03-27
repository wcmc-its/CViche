import { api } from './client'
import type { ApiError } from './client'
import type { RunStatus, PaginatedRuns, FeedbackStatus } from '../types'

export async function getRunStatus(runId: string): Promise<RunStatus> {
  return api.get<RunStatus>(`/api/run/${runId}/status`)
}

export async function getRunStep(runId: string, step: number): Promise<any> {
  return api.get(`/api/run/${runId}/step/${step}`)
}

export async function getPromptLogs(runId: string, step: number): Promise<any> {
  return api.get(`/api/run/${runId}/prompt-logs?step=${step}`)
}

export async function getRunDataJson(runId: string, filename: string): Promise<any> {
  return api.get(`/api/run/${runId}/data/${filename}/json`)
}

export async function cancelRun(runId: string): Promise<void> {
  await api.post(`/api/run/${runId}/cancel`)
}

export async function restartRun(runId: string): Promise<{ new_run_id: string }> {
  return api.post<{ new_run_id: string }>(`/api/run/${runId}/restart`)
}

export async function getRuns(offset: number, limit: number): Promise<PaginatedRuns> {
  const res = await api.getRaw(`/api/runs?offset=${offset}&limit=${limit}`)
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
  return api.get<FeedbackStatus[]>('/api/runs/feedback-status')
}

export async function startRun(runId: string): Promise<void> {
  await api.post(`/api/run/${runId}/start`)
}
