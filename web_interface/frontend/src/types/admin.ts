export interface Stats {
  total_runs: number
  active_users: number
  total_cost: number
  feedback_rate: number
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
}

export interface QualityScoreResult {
  run_id: string
  totalScore: number
  band: string
  dimensionScores: Array<{ name: string; score: number; max: number; penalty?: number; detail?: string }>
  flags: string[]
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
