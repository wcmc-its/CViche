import { api } from './client'
import type { ApiError } from './client'
import { uploadRoutes } from './routes'
import type { BatchEstimate, Estimate } from '../types'

export async function getEstimate(file: File): Promise<Estimate> {
  const formData = new FormData()
  formData.append('file', file)
  return api.post<Estimate>(uploadRoutes.estimate(), formData)
}

/** One /estimate call for several files (a batch): a row per file plus totals.
 *  Counts once against the estimate rate limit however many files it carries. */
export async function getBatchEstimate(files: File[]): Promise<BatchEstimate> {
  const formData = new FormData()
  for (const file of files) formData.append('files', file)
  return api.post<BatchEstimate>(uploadRoutes.estimate(), formData)
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

// Output-rendering options the user picks at upload (issue #153) plus the
// per-upload role attestation. Track changes is not an option: it is always
// on (Paul, 2026-10-01) and sent explicitly on every upload.
export interface UploadOptions {
  stripWcmInstructions: boolean
  submissionType: 'own_cv' | 'authorized_admin'
  /** The batch this file joins (POST /api/batches); absent for a single upload. */
  batchId?: string
  /** Resend of a file the server already ran, after the user agreed to run it again. */
  confirmDuplicate?: boolean
}

const HTTP_CONFLICT = 409
const DUPLICATE_FILE_CODE = 'duplicate_file'

/** POST /upload refused because this exact file was already processed (#1286); err.message says when. */
export function isDuplicateError(err: unknown): boolean {
  const { status, code } = (err ?? {}) as Partial<ApiError>
  return status === HTTP_CONFLICT && code === DUPLICATE_FILE_CODE
}

export async function uploadFile(file: File, options: UploadOptions): Promise<UploadResult> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('submission_type', options.submissionType)
  // FastAPI bool Form parsing accepts 'true'/'false' (and 1/0). Send explicit
  // strings so an unchecked box is transmitted as false rather than omitted.
  formData.append('strip_wcm_instructions', String(options.stripWcmInstructions))
  formData.append('include_track_changes', 'true')
  if (options.batchId) formData.append('batch_id', options.batchId)
  if (options.confirmDuplicate) formData.append('confirm_duplicate', 'true')
  return api.post<UploadResult>(uploadRoutes.upload(), formData)
}
