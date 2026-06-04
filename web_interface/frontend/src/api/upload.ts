import { api } from './client'
import { uploadRoutes } from './routes'
import type { Estimate } from '../types'

export async function getEstimate(file: File): Promise<Estimate> {
  const formData = new FormData()
  formData.append('file', file)
  return api.post<Estimate>(uploadRoutes.estimate(), formData)
}

export interface UploadResult {
  run_id: string
  // True when the backend's cheap, no-LLM heuristic thinks this upload is a
  // blank/near-blank WCM CV template. The UI warns and requires an
  // acknowledgement before starting a (paid) run when this is set.
  wcm_template_warning: boolean
  // Fraction of non-trivial lines that matched the blank-template string set
  // (~0 = clearly a real CV, ~1 = clearly an unfilled template). null when the
  // backend couldn't compute it. Available for logging/telemetry; the UI shows
  // a qualitative warning rather than this raw number.
  wcm_template_match_ratio: number | null
}

export async function uploadFile(file: File): Promise<UploadResult> {
  const formData = new FormData()
  formData.append('file', file)
  return api.post<UploadResult>(uploadRoutes.upload(), formData)
}
