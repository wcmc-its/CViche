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
