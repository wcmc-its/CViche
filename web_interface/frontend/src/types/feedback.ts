export interface FeedbackFormData {
  reviewer_role: string
  reviewer_role_other: string
  overall_usefulness: number | null
  overall_accuracy: number | null
  overall_completeness: number | null
  issue_missing_content: string | null
  issue_split_merged: string | null
  issue_wrong_section: string | null
  issue_inaccurate: string | null
  issue_ai_enrichment: string | null
  issue_formatting: string | null
  issue_locations: string[]
  biggest_issue: string
  manual_conversion_effort: string
  correction_effort: string
  enrichment_quality: number | null
  summary_generated: boolean | null
  summary_quality: number | null
  likelihood_to_recommend: number | null
}

export interface WcmSection {
  section_id: string
  section_name: string
}

/** One stored feedback row from GET /api/run/:id/feedback/all (newest first). */
export interface FeedbackDetail {
  id: number
  run_id: string
  user_id: number
  display_name: string
  reviewer_role: string
  overall_accuracy: number | null
  overall_completeness: number | null
  overall_usefulness: number
  manual_conversion_effort: string
  correction_effort: string
  enrichment_quality: number | null
  /** Boolean stored as 0/1. */
  summary_generated: number | null
  summary_quality: number | null
  issue_missing_content: string | null
  issue_split_merged: string | null
  issue_wrong_section: string | null
  issue_inaccurate: string | null
  issue_ai_enrichment: string | null
  issue_formatting: string | null
  issue_locations: string[] | null
  biggest_issue: string | null
  likelihood_to_recommend: number
  submitted_at: string | null
}

/** A reviewer's answer for one group of doctor findings (#1587). */
export type ReviewVerdict = 'fixed' | 'not_a_problem' | 'cant_tell'

/** GET /api/run/:id/feedback/verdict-groups: one kind of problem the run's Fix list shows. */
export interface VerdictGroup {
  lint: string
  shape: string | null
  /** The Fix list's plain title for the problem. */
  title: string
  /** How many findings of this kind the run has. */
  count: number
}

/** One entry of the optional `verdicts` list in a feedback submit. */
export interface FeedbackVerdictSubmit {
  lint: string
  shape: string | null
  verdict: ReviewVerdict
}

/** POST /api/run/:id/feedback/corrected-docx: a one-line confirmation only. */
export interface CorrectedDocxResult {
  changes: number
  summary: string
}
