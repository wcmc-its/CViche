import { api } from './client'
import type { FeedbackFormData } from '../types'

export async function getFeedback(runId: string): Promise<any> {
  return api.get(`/api/run/${runId}/feedback`)
}

export async function submitFeedback(runId: string, data: FeedbackFormData): Promise<Response> {
  // Returns raw Response because FeedbackForm needs status-specific handling
  // (201 success, 409 duplicate, 422 validation, 403 forbidden, 404 not found)
  return api.postRaw(`/api/run/${runId}/feedback`, data)
}
