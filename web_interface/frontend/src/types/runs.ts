export interface StepSummary {
  step_number: number
  stage_id?: string
  step_name: string
  status: string
  duration_seconds?: number
  cost: number | null
  output_files?: string
}

export interface RunStatus {
  run_id: string
  filename: string
  status: string
  total_cost: number | null
  total_tokens: number
  input_tokens: number
  output_tokens: number
  total_duration_seconds?: number
  /** Input-scaled wall-clock estimate (s) from upload; stall watchdog scales its
   *  "taking longer than expected" threshold off this. Absent on older runs. */
  estimated_duration_seconds?: number | null
  error_message?: string
  steps: StepSummary[]
}

/** Who ran a run; only sent by GET /api/runs?scope=all (admin). */
export interface RunBy {
  id: number
  display_name: string
  cwid: string | null
  email: string | null
  department: string | null
}

export interface RunSummary {
  run_id: string
  filename: string
  status: string
  started_at: string
  completed_at: string | null
  total_cost: number | null
  total_duration_seconds: number | null
  /** CV owner inferred by stage 4; null until stage 4 completes or if none was inferred. */
  cv_owner_name?: string | null
  /** "own_cv" (faculty uploaded their own CV) or "authorized_admin". */
  submission_type?: string | null
  /** Populated only with scope=all; null for runs without a user. */
  run_by?: RunBy | null
}

export type RunListScope = 'mine' | 'all'

/** Query params for GET /api/runs and /api/runs/filter-options. The filters are
 *  honoured only with scope 'all' (admin); `run_by` is a user id or 'self'. */
export interface RunListParams {
  scope?: RunListScope
  run_by?: number | 'self'
  faculty?: string
  department?: string
}

export interface FilterCount {
  value: string
  count: number
}

export interface RunByOption extends RunBy {
  count: number
}

export interface RunFilterOptions {
  departments: FilterCount[]
  faculty: FilterCount[]
  run_by: RunByOption[]
  /** Runs where the faculty member uploaded their own CV (run_by = 'self'). */
  self_count: number
}

export interface FeedbackStatus {
  run_id: string
  has_feedback: boolean
}

export interface PaginatedRuns {
  runs: RunSummary[]
  total: number
  has_more: boolean
}
