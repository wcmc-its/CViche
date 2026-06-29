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

// Output-rendering options the user picks at upload (issue #153). Defaults
// mirror the backend Run column defaults: track changes ON, classification
// comments OFF. Omitting the argument keeps that behavior.
export interface UploadOptions {
  includeTrackChanges: boolean
  includeClassificationComments: boolean
  stripWcmInstructions: boolean
}

export async function uploadFile(
  file: File,
  options: UploadOptions = { includeTrackChanges: true, includeClassificationComments: false, stripWcmInstructions: true },
): Promise<UploadResult> {
  const formData = new FormData()
  formData.append('file', file)
  // FastAPI bool Form parsing accepts 'true'/'false' (and 1/0). Send explicit
  // strings so an unchecked box is transmitted as false rather than omitted.
  formData.append('include_track_changes', String(options.includeTrackChanges))
  formData.append('include_classification_comments', String(options.includeClassificationComments))
  formData.append('strip_wcm_instructions', String(options.stripWcmInstructions))
  return api.post<UploadResult>(uploadRoutes.upload(), formData)
}
