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
  /** When the run was created (ISO, from GET /run/{id}/status). */
  started_at?: string
  total_cost: number | null
  total_tokens: number
  input_tokens: number
  output_tokens: number
  total_duration_seconds?: number
  /** Input-scaled wall-clock estimate (s) from upload; stall watchdog scales its
   *  "taking longer than expected" threshold off this. Absent on older runs. */
  estimated_duration_seconds?: number | null
  error_message?: string
  /** CV owner inferred by stage 4; null until inferred. */
  cv_owner_name?: string | null
  /** Who ran it. Admin only. */
  run_by?: RunBy | null
  /** A PDF's scanned pages, whose text is missing from the output (#1282). */
  scanned_pages?: number[]
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

/** One reviewer's submission on a run (scope=all only). */
export interface FeedbackReviewer {
  display_name: string
  role: string
  submitted_at: string | null
}

/** Feedback left on a run by ANY reviewer, including others on your own run. */
export interface RunFeedbackSummary {
  count: number
  given_by_me: boolean
  last_at: string | null
  /** Admin scope=all only, newest first; null under scope=mine. */
  reviewers: FeedbackReviewer[] | null
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
  /** Advisory 0-100 quality score. Admin scope=all only; null when unscored. */
  quality_score?: number | null
  /** GREEN / YELLOW / RED. Admin scope=all only. */
  quality_band?: QualityBand | null
  /** The hard-fail cap that lowered the score, else null. Admin scope=all only. */
  quality_cap?: number | null
  feedback: RunFeedbackSummary
  /** The batch upload this run belongs to; null for a single upload. */
  batch_id?: string | null
}

export type QualityBand = 'GREEN' | 'YELLOW' | 'RED'
export type DoctorSeverity = 'ERROR' | 'WARN' | 'INFO'

export interface QualityDimension {
  name: string
  weight: number
  points: number
}

export interface DoctorFindingGroup {
  lint: string
  /** Most severe of the lint's instances. */
  severity: DoctorSeverity
  /** Plain-English explanation of the lint. */
  message: string
  /** Instances of this lint in the run. */
  count: number
  /** Share of runs the lint fires on (0-1); null when unmeasured. */
  prevalence: number | null
  /** This lint is the gate behind the run's applied cap. */
  caps_score: boolean
}

export interface RunDoctorReport {
  /** Distinct lints per severity (not instances). */
  counts: { error: number; warn: number; info: number }
  /** One row per lint, rarest first. */
  findings: DoctorFindingGroup[]
  /** Lints skipped or unreadable because an input artifact was absent. */
  not_run: number
}

/** GET /api/run/:id/run-quality (admin). Score fields are null together when no
 *  score is cached; `doctor` is null when no report was stored. */
export interface RunQualityReport {
  run_id: string
  score: number | null
  band: QualityBand | null
  /** "Ship" / "Needs human cleanup" / "Don't deliver". */
  band_meaning: string | null
  provisional: boolean
  cap: number | null
  cap_reason: string | null
  cap_lint: string | null
  /** Weighted total before the cap. */
  earned: number | null
  total_weight: number | null
  data_complete: boolean | null
  /** Weighted dimensions in the scorer's order. */
  dimensions: QualityDimension[]
  doctor: RunDoctorReport | null
}

/** GET /api/run/:id/review-note. Never carries the score. */
export interface RunReviewNote {
  needs_cleanup: boolean
}

export type RunListScope = 'mine' | 'all'

/** Query params for GET /api/runs and /api/runs/filter-options. The filters are
 *  honoured only with scope 'all' (admin); `run_by` is a user id or 'self'. */
export type RunFeedbackFilter = 'given' | 'needed'

/** Whether the uploaded CV was written in the WCM CV template; 'unknown' = not classified. */
export type RunInputFormatFilter = 'wcm' | 'other' | 'unknown'

/** Status pills: 'running' = queued or running; 'failed' = failed runs; 'red' = score band RED (admin only: 403 under scope 'mine'). */
export type RunStatusFilter = 'running' | 'failed' | 'red'

export interface RunListParams {
  scope?: RunListScope
  /** 'given' = any reviewer left feedback; 'needed' = complete run with none. Both scopes. */
  feedback?: RunFeedbackFilter
  input_format?: RunInputFormatFilter
  /** A user id, 'self' (the faculty member uploaded it) or 'on_behalf' (an authorized admin did). */
  run_by?: number | 'self' | 'on_behalf'
  /** Both scopes; 'red' is admin only. */
  status?: RunStatusFilter
  faculty?: string
  department?: string
}

export interface FilterCount {
  value: string
  count: number
}

/** A faculty option; the API sends these newest run first. */
export interface FacultyOption extends FilterCount {
  last_run_at: string | null
}

export interface RunByOption extends RunBy {
  count: number
}

/** Runs behind each status pill; applies every filter except status. */
export interface StatusFilterCounts {
  all: number
  running: number
  awaiting_feedback: number
  failed: number
  red: number
}

export interface RunFilterOptions {
  departments: FilterCount[]
  faculty: FacultyOption[]
  run_by: RunByOption[]
  /** Runs where the faculty member uploaded their own CV (run_by = 'self'). */
  self_count: number
  /** Runs an authorized admin submitted on the faculty member's behalf (run_by = 'on_behalf'). */
  on_behalf_count: number
  status: StatusFilterCounts
  /** Runs per feedback filter value; applies every filter except feedback. */
  feedback: { given: number; needed: number }
  /** Runs per input-format filter value; applies every filter except input format. */
  input_format: { wcm: number; other: number; unknown: number }
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
