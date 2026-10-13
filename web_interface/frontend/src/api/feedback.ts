import { api } from './client'
import { feedbackRoutes } from './routes'
import type { CorrectedCopyDetail, CorrectedDocxResult, FeedbackDetail, FeedbackFormData } from '../types'

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

/** Upload the reviewer's corrected copy; the server diffs and stores it, and
 *  answers with a one-line count. Throws ApiError (with the server's message). */
export async function uploadCorrectedDocx(runId: string, file: File): Promise<CorrectedDocxResult> {
  const body = new FormData()
  body.append('file', file)
  return api.post<CorrectedDocxResult>(feedbackRoutes.correctedDocx(runId), body)
}

/** Every reviewer's corrected copy of a run, newest first. Run owner, admin or staff. */
export async function getCorrectedCopies(runId: string): Promise<CorrectedCopyDetail[]> {
  return api.get<CorrectedCopyDetail[]>(feedbackRoutes.correctedCopies(runId))
}

/** Where to download one corrected copy: a presigned S3 URL, or null on local
 *  storage (download from feedbackRoutes.correctedCopy instead). Rejects with the
 *  backend's message when the download is refused (409 still scanning, 403 flagged). */
export async function getCorrectedCopyUrl(runId: string, copyId: number): Promise<string | null> {
  const { url } = await api.get<{ url: string | null }>(feedbackRoutes.correctedCopyUrl(runId, copyId))
  return url
}
