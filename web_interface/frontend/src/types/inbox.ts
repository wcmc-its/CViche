/** One emailed CV held for the user to confirm (GET /api/inbox, #1298). */
export interface InboxItem {
  id: number
  filename: string
  size_bytes: number
  received_at: string
  /** Set when a run already holds this exact file; run_id only for the run's own submitter or an admin. */
  duplicate: { last_processed_on: string; run_id: string | null } | null
}

export interface InboxSubmitResult {
  id: number
  status: 'submitted' | 'failed'
  run_id: string | null
  /** On failure: the code /upload would have answered ('duplicate_file', 'rate_limited', ...). */
  error: string | null
  message: string | null
  last_processed_on: string | null
}
