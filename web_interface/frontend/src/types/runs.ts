export interface StepSummary {
  step_number: number
  stage_id?: string
  step_name: string
  status: string
  duration_seconds?: number
  cost: number
  output_files?: string
}

export interface RunStatus {
  run_id: string
  filename: string
  status: string
  total_cost: number
  total_tokens: number
  input_tokens: number
  output_tokens: number
  total_duration_seconds?: number
  error_message?: string
  steps: StepSummary[]
}

export interface RunSummary {
  run_id: string
  filename: string
  status: string
  started_at: string
  completed_at: string | null
  total_cost: number
  total_duration_seconds: number | null
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
