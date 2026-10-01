/** Question wording and answer options shared by the feedback form and the read-only summary. */

export const REVIEWER_ROLES = [
  { value: 'cv_owner', label: 'I am the CV owner' },
  { value: 'department_admin', label: 'Department administrator' },
  { value: 'faculty_affairs', label: 'Faculty affairs staff' },
  { value: 'other', label: 'Other' },
]

export const ISSUE_FIELDS = [
  { key: 'issue_missing_content' as const, label: 'Missing content', description: 'Entries or sections from the original are absent' },
  { key: 'issue_split_merged' as const, label: 'Split or merged entries', description: 'One entry broken in two, or two combined' },
  { key: 'issue_wrong_section' as const, label: 'Wrong section', description: 'An entry landed under the wrong heading' },
  { key: 'issue_inaccurate' as const, label: 'Inaccurate details', description: 'Dates, titles, names or journals are wrong' },
  { key: 'issue_ai_enrichment' as const, label: 'PubMed enrichment errors', description: 'Wrong citation, journal or PMID added' },
  { key: 'issue_formatting' as const, label: 'Formatting', description: "Layout, ordering or style doesn't match the WCM template" },
]

export const EFFORT_OPTIONS = [
  { value: '0 minutes', label: '0 minutes' },
  { value: '< 5 minutes', label: 'Less than 5 minutes' },
  { value: '5-15 minutes', label: '5-15 minutes' },
  { value: '15-30 minutes', label: '15-30 minutes' },
  { value: '30-60 minutes', label: '30-60 minutes' },
  { value: '1-2 hours', label: '1-2 hours' },
  { value: '2-4 hours', label: '2-4 hours' },
  { value: '4-8 hours', label: '4-8 hours' },
  { value: '8+ hours', label: '8+ hours' },
  { value: 'not_sure', label: "I'm not sure" },
]

/** Question text per answer field. */
export const QUESTION_LABELS = {
  reviewer_role: 'Your role',
  overall_usefulness: 'How useful was the CViche output?',
  overall_accuracy: 'How accurate was the output?',
  overall_completeness: 'How complete was the output?',
  issue_locations: 'Which sections were affected?',
  biggest_issue: 'What was the biggest issue, if any?',
  manual_conversion_effort: 'Without CViche, how long would it take to convert this CV by hand?',
  correction_effort: 'How long did it take to correct the CViche output?',
  enrichment_quality: 'Quality of AI-enriched data',
  summary_generated: 'Did CViche generate a research summary for this CV?',
  summary_quality: 'How would you rate the research summary?',
  likelihood_to_recommend: 'How likely are you to recommend CViche to a colleague?',
} as const

/** Heading over the ticked problem cards in the summary. */
export const PROBLEMS_LABEL = 'Problems'

export interface RatingScale {
  min: number
  max: number
  lowLabel: string
  highLabel: string
}

const QUALITY_5: RatingScale = { min: 1, max: 5, lowLabel: 'Poor quality', highLabel: 'Excellent quality' }

/** Scale per rated field. */
export const RATING_SCALES = {
  overall_usefulness: { min: 1, max: 5, lowLabel: 'Not useful', highLabel: 'Extremely useful' },
  overall_accuracy: { min: 1, max: 10, lowLabel: 'Not at all accurate', highLabel: 'Perfectly accurate' },
  overall_completeness: { min: 1, max: 10, lowLabel: 'Very incomplete', highLabel: 'Fully complete' },
  enrichment_quality: QUALITY_5,
  summary_quality: QUALITY_5,
  likelihood_to_recommend: { min: 1, max: 5, lowLabel: 'Not at all likely', highLabel: 'Extremely likely' },
} as const satisfies Record<string, RatingScale>
