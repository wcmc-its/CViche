import { api } from './client'
import { feedbackRoutes } from './routes'
import type { CorrectedDocxResult, FeedbackDetail, FeedbackFormData, VerdictGroup } from '../types'

export async function getFeedback(runId: string): Promise<any> {
  return api.get(feedbackRoutes.get(runId))
}

export async function submitFeedback(runId: string, data: FeedbackFormData): Promise<Response> {
  // Returns raw Response because FeedbackForm needs status-specific handling
  // (201 success, 409 duplicate, 422 validation, 403 forbidden, 404 not found)
  return api.postRaw(feedbackRoutes.submit(runId), data)
}

/** Every reviewer's feedback on a run, newest first. Run owner or admin only. */
export async function getRunFeedbackAll(runId: string): Promise<FeedbackDetail[]> {
  return api.get<FeedbackDetail[]>(feedbackRoutes.all(runId))
}

/** The groups of doctor findings the review form asks a verdict on (#1587). */
export async function getVerdictGroups(runId: string): Promise<VerdictGroup[]> {
  return api.get<VerdictGroup[]>(feedbackRoutes.verdictGroups(runId))
}

/** Upload the reviewer's corrected copy; the server diffs and stores it, and
 *  answers with a one-line count. Throws ApiError (with the server's message). */
export async function uploadCorrectedDocx(runId: string, file: File): Promise<CorrectedDocxResult> {
  const body = new FormData()
  body.append('file', file)
  return api.post<CorrectedDocxResult>(feedbackRoutes.correctedDocx(runId), body)
}
