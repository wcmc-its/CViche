import type { RunBy } from './runs'
import type { Estimate } from './upload'

/** One batch in GET /api/batches (the Runs page's Batch filter). */
export interface BatchSummary {
  id: string
  submitted_by: RunBy | null
  created_at: string
  run_count: number
  /** Valid files the submitter sent; above run_count when some never uploaded. */
  files_submitted: number
}

/** How many of a batch's runs are in each status. `created` = uploaded, not started. */
export interface BatchStatusCounts {
  complete: number
  running: number
  queued: number
  failed: number
  cancelled: number
  created: number
}

/** One run in the batch view. */
export interface BatchRunRow {
  run_id: string
  filename: string
  cv_owner_name: string | null
  status: string
  /** Queued rows only: queued batch runs ahead of this one. */
  queue_position: number | null
  /** Admin only; null for everyone else and for unscored runs. */
  quality_score: number | null
}

/** GET /api/batches/:id */
export interface BatchDetail extends BatchSummary {
  status_counts: BatchStatusCounts
  runs: BatchRunRow[]
}

/** One queue in GET /api/queue; workers and the wait are null when unknown. */
export interface QueueLane {
  workers: number | null
  ahead: number
  est_wait_minutes: number | null
}

/** GET /api/queue. The lanes are null unless dispatch_mode is 'queue'. */
export interface QueueOverview {
  dispatch_mode: string
  single: QueueLane | null
  batch: QueueLane | null
}

/** One file of a multi-file POST /api/estimate: its estimate, or why it has none. */
export interface BatchEstimateFile {
  filename: string
  estimate: Estimate | null
  error: string | null
}

/** Multi-file POST /api/estimate; cost fields are null for non-admins. */
export interface BatchEstimate {
  files: BatchEstimateFile[]
  estimated_time_seconds_min: number
  estimated_time_seconds_max: number
  estimated_cost_min: number | null
  estimated_cost_max: number | null
  num_steps: number
  pricing_model: string | null
}
