export interface Stats {
  total_runs: number
  active_users: number
  total_cost: number
  feedback_rate: number
  avg_duration_seconds?: number | null
  p95_duration_seconds?: number | null
  step_avg_seconds?: StepAvg[]
  submissions?: SubmissionSplit
}

export interface DepartmentSubmissions {
  /** null: the submitter has no ED department. */
  department: string | null
  own_cv: number
  on_behalf: number
}

/** Who submits CVs: faculty themselves (own_cv) vs on their behalf. */
export interface SubmissionSplit {
  own_cv: number
  on_behalf: number
  departments: DepartmentSubmissions[]
}

export interface ConsentPublishPreview {
  current_version: string
  next_version: string
  /** Active users whose consent_version is not next_version. */
  users_to_reconsent: number
}

export interface StepAvg {
  stage_id: string
  step_name: string
  avg_seconds: number
}

export interface AdminUser {
  id: number
  email: string
  display_name: string
  role: string
  status: string
  daily_limit: number | null
  monthly_limit: number | null
  runs_today: number
  total_runs: number
  total_cost: number
  feedback_count: number
  completed_run_count: number
  last_active_at: string | null
  created_at: string | null
}

export interface AdminRun {
  run_id: string
  user_email: string | null
  user_display_name: string | null
  filename: string
  status: string
  duration_seconds: number | null
  total_cost: number
  started_at: string | null
  has_feedback: boolean
  quality_score: number | null
  quality_band: string | null
  // false when the score was computed with a scored file missing or unreadable (#745);
  // null when not scored, or scored before the field existed
  quality_data_complete: boolean | null
  quality_missing_evidence: string[]
}

/** One weighted scorer dimension: `score` earned out of `max`, `penalty` lost. */
export interface DimensionScore {
  name: string
  score: number
  max: number
  penalty: number
  detail: string
}

export interface QualityScoreResult {
  run_id: string
  totalScore: number
  band: string
  dimensionScores: DimensionScore[]
  flags: string[]
  data_complete: boolean | null
  missing_evidence: string[]
}

export interface AdminRunsResponse {
  runs: AdminRun[]
  total: number
  has_more: boolean
  offset: number
  limit: number
}

export interface SystemConfig {
  allowed_users: string[]
  admin_users: string[]
  rate_limit_daily: number
  rate_limit_monthly: number
  consent_version: string
  auth_mode: string
}

export interface FeedbackData {
  id: number
  run_id: string
  user_email: string
  reviewer_role: string
  overall_accuracy: number | null
  overall_completeness: number | null
  overall_usefulness: number
  manual_conversion_effort: string
  correction_effort: string
  enrichment_quality: number | null
  summary_generated: number | null
  summary_quality: number | null
  issue_missing_content: string
  issue_split_merged: string
  issue_wrong_section: string
  issue_inaccurate: string
  issue_ai_enrichment: string
  issue_formatting: string
  issue_locations: string
  biggest_issue: string
  likelihood_to_recommend: number
  submitted_at: string
}

export interface AggregatedScores {
  accuracy: { avg: number; count: number }
  completeness: { avg: number; count: number }
  usefulness: { avg: number; count: number }
  recommend: { avg: number; count: number }
}
