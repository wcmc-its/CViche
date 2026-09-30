import { useState, useEffect, useCallback } from 'react'
import { CheckCircle2, Loader2 } from 'lucide-react'
import type { FeedbackFormData, WcmSection } from '../types'
import { getFeedback, submitFeedback } from '../api/feedback'
import ErrorBanner from './ErrorBanner'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FeedbackFormProps {
  runId: string
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const INITIAL_FORM_DATA: FeedbackFormData = {
  reviewer_role: '',
  reviewer_role_other: '',
  overall_usefulness: null,
  overall_accuracy: null,
  overall_completeness: null,
  issue_missing_content: null,
  issue_split_merged: null,
  issue_wrong_section: null,
  issue_inaccurate: null,
  issue_ai_enrichment: null,
  issue_formatting: null,
  issue_locations: [],
  biggest_issue: '',
  manual_conversion_effort: '',
  correction_effort: '',
  enrichment_quality: null,
  summary_generated: null,
  summary_quality: null,
  likelihood_to_recommend: null,
}

const REVIEWER_ROLES = [
  { value: 'cv_owner', label: 'I am the CV owner' },
  { value: 'department_admin', label: 'Department administrator' },
  { value: 'faculty_affairs', label: 'Faculty affairs staff' },
  { value: 'other', label: 'Other' },
]

const ISSUE_FIELDS = [
  { key: 'issue_missing_content' as const, label: 'Missing content', placeholder: 'What content is missing from the output?' },
  { key: 'issue_split_merged' as const, label: 'Split or merged entries', placeholder: 'Which entries were split or merged?' },
  { key: 'issue_wrong_section' as const, label: 'Wrong section placement', placeholder: 'Which entries were placed in the wrong section?' },
  { key: 'issue_inaccurate' as const, label: 'Inaccurate information', placeholder: 'What information was inaccurate?' },
  { key: 'issue_ai_enrichment' as const, label: 'AI enrichment errors', placeholder: 'Describe the enrichment errors you noticed' },
  { key: 'issue_formatting' as const, label: 'Formatting problems', placeholder: 'Describe the formatting issues' },
]

const EFFORT_OPTIONS = [
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


// ---------------------------------------------------------------------------
// Styling tokens (shared by the pieces below)
// ---------------------------------------------------------------------------

const CARD = 'bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] p-5 sm:p-6'
const TOGGLE_BASE =
  'inline-flex items-center justify-center rounded-lg border px-3 py-1.5 text-[13px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1'
const TOGGLE_ON = 'border-ink bg-ink text-white'
const TOGGLE_OFF = 'border-sand-400 bg-white text-gray-700 hover:bg-sand-50'
const TEXT_INPUT =
  'w-full rounded-lg border border-sand-400 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-primary-500 focus:ring-2 focus:ring-primary-500 focus:outline-none transition-colors'

// ---------------------------------------------------------------------------
// Internal sub-components
// ---------------------------------------------------------------------------

function SectionLabel({ children }: { children: string }) {
  return (
    <div className="mt-6 first:mt-0 pb-2 border-b border-sand-200 text-[11px] font-semibold uppercase tracking-wider text-gray-500">
      {children}
    </div>
  )
}

function QuestionRow({
  label,
  helper,
  children,
}: {
  label: string
  helper?: string
  children: React.ReactNode
}) {
  return (
    <div className="py-4 border-b border-sand-200 last:border-b-0 grid gap-x-6 gap-y-2 sm:grid-cols-[240px_minmax(0,1fr)] items-start">
      <div>
        <div className="text-sm font-medium text-gray-900">{label}</div>
        {helper && <div className="mt-0.5 text-xs text-gray-500">{helper}</div>}
      </div>
      <div>{children}</div>
    </div>
  )
}

function RatingButtonRow({
  min,
  max,
  value,
  onChange,
  lowLabel,
  highLabel,
  ariaLabel,
}: {
  min: number
  max: number
  value: number | null
  onChange: (v: number) => void
  lowLabel: string
  highLabel: string
  ariaLabel: string
}) {
  const buttons = []
  for (let i = min; i <= max; i++) {
    buttons.push(i)
  }

  return (
    <div>
      <div role="radiogroup" aria-label={ariaLabel} className="flex flex-wrap gap-2">
        {buttons.map((n) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={value === n}
            onClick={() => onChange(n)}
            className={`${TOGGLE_BASE} h-9 min-w-[2.25rem] px-0 ${value === n ? TOGGLE_ON : TOGGLE_OFF}`}
          >
            {n}
          </button>
        ))}
      </div>
      <div className="flex justify-between gap-3 mt-1.5 text-xs text-gray-500">
        <span>{lowLabel}</span>
        <span className="text-right">{highLabel}</span>
      </div>
    </div>
  )
}

function ChoiceRow<T extends string | boolean>({
  options,
  value,
  onChange,
  ariaLabel,
  required,
}: {
  options: { value: T; label: string }[]
  value: T | '' | null
  onChange: (v: T) => void
  ariaLabel: string
  required?: boolean
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      aria-required={required ? 'true' : undefined}
      className="flex flex-wrap gap-2"
    >
      {options.map((opt) => (
        <button
          key={String(opt.value)}
          type="button"
          role="radio"
          aria-checked={value === opt.value}
          onClick={() => onChange(opt.value)}
          className={`${TOGGLE_BASE} ${value === opt.value ? TOGGLE_ON : TOGGLE_OFF}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

function FeedbackSubmittedCard({ submittedAt }: { submittedAt?: string | null }) {
  const formattedDate = submittedAt
    ? new Date(submittedAt).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      })
    : null

  return (
    <div className="flex flex-col items-center text-center py-6">
      <CheckCircle2 className="h-6 w-6 text-success-600 mb-3" aria-hidden="true" />
      <h3 className="text-lg font-semibold text-gray-900">Feedback Submitted</h3>
      <p className="text-sm text-gray-500 mt-1">
        Thank you for your feedback.{formattedDate ? ` Submitted on ${formattedDate}.` : ''}
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function FeedbackForm({ runId }: FeedbackFormProps) {
  const [formData, setFormData] = useState<FeedbackFormData>(INITIAL_FORM_DATA)
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [existingFeedback, setExistingFeedback] = useState<Record<string, unknown> | null>(null)
  const [wcmSections, setWcmSections] = useState<WcmSection[]>([])

  // ---- helpers ----

  const updateField = useCallback(
    <K extends keyof FeedbackFormData>(key: K, value: FeedbackFormData[K]) => {
      setFormData((prev) => ({ ...prev, [key]: value }))
    },
    [],
  )

  // Required fields: role (plus text when "other"), usefulness, both effort
  // answers and likelihood to recommend. Same rules the four wizard steps had.
  const canSubmit =
    formData.reviewer_role !== '' &&
    !(formData.reviewer_role === 'other' && formData.reviewer_role_other.trim() === '') &&
    formData.overall_usefulness !== null &&
    formData.manual_conversion_effort !== '' &&
    formData.correction_effort !== '' &&
    formData.likelihood_to_recommend !== null

  // ---- fetch existing feedback on mount ----

  useEffect(() => {
    let cancelled = false

    async function loadFeedback() {
      try {
        const data = await getFeedback(runId)

        if (cancelled) return

        if (data.feedback) {
          setExistingFeedback(data.feedback)
        } else {
          // Extract wcm_sections that have a section_id
          const sections: WcmSection[] = (data.run_context?.wcm_sections || []).filter(
            (s: Record<string, unknown>) => s.section_id,
          )
          setWcmSections(sections)
        }
      } catch {
        if (!cancelled) {
          setError('Unable to load feedback form. Please try refreshing the page.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadFeedback()
    return () => {
      cancelled = true
    }
  }, [runId])

  // ---- submit handler ----

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)

    // Build the payload matching FeedbackSubmit schema
    const reviewerRole =
      formData.reviewer_role === 'other'
        ? formData.reviewer_role_other.trim()
        : formData.reviewer_role

    const payload: Record<string, unknown> = {
      reviewer_role: reviewerRole,
      overall_usefulness: formData.overall_usefulness,
      overall_accuracy: formData.overall_accuracy,
      overall_completeness: formData.overall_completeness,
      manual_conversion_effort: formData.manual_conversion_effort,
      correction_effort: formData.correction_effort,
      enrichment_quality: formData.enrichment_quality,
      summary_generated: formData.summary_generated,
      summary_quality: formData.summary_generated === true ? formData.summary_quality : null,
      issue_missing_content: formData.issue_missing_content,
      issue_split_merged: formData.issue_split_merged,
      issue_wrong_section: formData.issue_wrong_section,
      issue_inaccurate: formData.issue_inaccurate,
      issue_ai_enrichment: formData.issue_ai_enrichment,
      issue_formatting: formData.issue_formatting,
      issue_locations:
        formData.issue_locations.length > 0 ? formData.issue_locations : null,
      biggest_issue: formData.biggest_issue || null,
      likelihood_to_recommend: formData.likelihood_to_recommend,
    }

    try {
      const res = await submitFeedback(runId, payload as unknown as FeedbackFormData)

      if (res.status === 201) {
        setSubmitted(true)
        setSubmitting(false)
        return
      }

      if (res.status === 409) {
        setError('Feedback has already been submitted for this run.')
        setExistingFeedback({ duplicate: true })
        setSubmitting(false)
        return
      }

      if (res.status === 422) {
        try {
          const body = await res.json()
          setError(body.detail?.message || 'Validation error. Please check your responses.')
        } catch {
          setError('Validation error. Please check your responses.')
        }
        setSubmitting(false)
        return
      }

      if (res.status === 403) {
        setError("You don't have permission to submit feedback for this run.")
        setSubmitting(false)
        return
      }

      if (res.status === 404) {
        setError('This run could not be found. It may have been deleted.')
        setSubmitting(false)
        return
      }

      // Other errors
      setError('Something went wrong submitting your feedback. Check your connection and try again.')
      setSubmitting(false)
    } catch {
      setError(
        'Something went wrong submitting your feedback. Check your connection and try again.',
      )
      setSubmitting(false)
    }
  }

  const anyIssueChecked =
    formData.issue_missing_content !== null ||
    formData.issue_split_merged !== null ||
    formData.issue_wrong_section !== null ||
    formData.issue_inaccurate !== null ||
    formData.issue_ai_enrichment !== null ||
    formData.issue_formatting !== null

  const toggleIssueLocation = (sectionId: string) => {
    setFormData((prev) => {
      const locs = prev.issue_locations.includes(sectionId)
        ? prev.issue_locations.filter((id) => id !== sectionId)
        : [...prev.issue_locations, sectionId]
      return { ...prev, issue_locations: locs }
    })
  }

  // ---- progress ----

  const answered: boolean[] = [
    formData.reviewer_role !== '',
    formData.overall_usefulness !== null,
    formData.overall_accuracy !== null,
    formData.overall_completeness !== null,
    formData.manual_conversion_effort !== '',
    formData.correction_effort !== '',
    formData.enrichment_quality !== null,
    formData.summary_generated !== null,
    formData.likelihood_to_recommend !== null,
  ]
  if (formData.summary_generated === true) answered.push(formData.summary_quality !== null)
  const answeredCount = answered.filter(Boolean).length

  // ---- render: loading state ----

  if (loading) {
    return (
      <div className={CARD}>
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" aria-label="Loading feedback form" />
        </div>
      </div>
    )
  }

  // ---- render: existing feedback ----

  if (existingFeedback) {
    return (
      <div className={CARD}>
        <FeedbackSubmittedCard
          submittedAt={(existingFeedback as Record<string, unknown>).submitted_at as string | null}
        />
      </div>
    )
  }

  // ---- render: success after submission ----

  if (submitted) {
    return (
      <div className={CARD}>
        <div className="flex flex-col items-center text-center py-8">
          <CheckCircle2 className="h-8 w-8 text-success-600 mb-3" aria-hidden="true" />
          <h3 className="text-xl font-semibold text-gray-900">Thank you!</h3>
          <p className="text-sm text-gray-500 mt-2">
            Your feedback has been recorded. It helps us improve CViche for everyone.
          </p>
        </div>
      </div>
    )
  }

  // ---- render: form ----

  // A <section>, not a <form>: Enter in a text box must not submit a
  // half-finished review.
  return (
    <section className={CARD} aria-label="Review this output">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-[17px] font-semibold text-gray-900">Review this output</h2>
        <span className="text-[13px] text-gray-500">
          About 2 minutes · {answeredCount} of {answered.length} answered
        </span>
      </div>
      <p className="mt-2 mb-5 text-[13px] text-gray-500">
        Compare the WCM document with the original CV. Every answer is optional.
      </p>

      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} onDismiss={() => setError(null)} />
        </div>
      )}

      <SectionLabel>You</SectionLabel>
      <QuestionRow label="Your role" helper="Required">
        <ChoiceRow
          options={REVIEWER_ROLES}
          value={formData.reviewer_role}
          onChange={(v) => updateField('reviewer_role', v)}
          ariaLabel="Your role"
          required
        />
        {formData.reviewer_role === 'other' && (
          <input
            type="text"
            value={formData.reviewer_role_other}
            onChange={(e) => updateField('reviewer_role_other', e.target.value)}
            placeholder="Describe your role"
            className={`mt-2 ${TEXT_INPUT}`}
            aria-required="true"
            aria-label="Describe your role"
          />
        )}
      </QuestionRow>

      <SectionLabel>Quality</SectionLabel>
      <QuestionRow label="How useful was the CViche output?" helper="Required">
        <RatingButtonRow
          min={1}
          max={5}
          value={formData.overall_usefulness}
          onChange={(v) => updateField('overall_usefulness', v)}
          lowLabel="Not useful"
          highLabel="Extremely useful"
          ariaLabel="How useful was the CViche output?"
        />
      </QuestionRow>
      <QuestionRow label="How accurate was the output?" helper="Optional">
        <RatingButtonRow
          min={1}
          max={10}
          value={formData.overall_accuracy}
          onChange={(v) => updateField('overall_accuracy', v)}
          lowLabel="Not at all accurate"
          highLabel="Perfectly accurate"
          ariaLabel="How accurate was the output?"
        />
      </QuestionRow>
      <QuestionRow label="How complete was the output?" helper="Optional">
        <RatingButtonRow
          min={1}
          max={10}
          value={formData.overall_completeness}
          onChange={(v) => updateField('overall_completeness', v)}
          lowLabel="Very incomplete"
          highLabel="Fully complete"
          ariaLabel="How complete was the output?"
        />
      </QuestionRow>

      <SectionLabel>Problems</SectionLabel>
      <QuestionRow label="Check any issues you found" helper="Optional">
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {ISSUE_FIELDS.map((issue) => {
              const checked = formData[issue.key] !== null
              return (
                <button
                  key={issue.key}
                  type="button"
                  role="checkbox"
                  aria-checked={checked}
                  onClick={() => updateField(issue.key, checked ? null : '')}
                  className={`${TOGGLE_BASE} ${checked ? TOGGLE_ON : TOGGLE_OFF}`}
                >
                  {issue.label}
                </button>
              )
            })}
          </div>
          {ISSUE_FIELDS.filter((issue) => formData[issue.key] !== null).map((issue) => (
            <input
              key={issue.key}
              type="text"
              value={formData[issue.key] ?? ''}
              onChange={(e) => updateField(issue.key, e.target.value)}
              placeholder={issue.placeholder}
              aria-label={issue.label}
              className={TEXT_INPUT}
            />
          ))}
        </div>
      </QuestionRow>

      {anyIssueChecked && wcmSections.length > 0 && (
        <QuestionRow label="Which sections were affected?">
          <div className="flex flex-wrap gap-2">
            {wcmSections.map((section) => {
              const on = formData.issue_locations.includes(section.section_id)
              return (
                <button
                  key={section.section_id}
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  onClick={() => toggleIssueLocation(section.section_id)}
                  className={`${TOGGLE_BASE} ${on ? TOGGLE_ON : TOGGLE_OFF}`}
                >
                  {section.section_id}: {section.section_name}
                </button>
              )
            })}
          </div>
        </QuestionRow>
      )}

      <QuestionRow label="What was the biggest issue, if any?" helper="Optional">
        <textarea
          value={formData.biggest_issue}
          onChange={(e) => updateField('biggest_issue', e.target.value)}
          rows={3}
          aria-label="What was the biggest issue, if any?"
          className={TEXT_INPUT}
        />
      </QuestionRow>

      <SectionLabel>Time</SectionLabel>
      <QuestionRow
        label="Without CViche, how long would it take to convert this CV by hand?"
        helper="Required"
      >
        <ChoiceRow
          options={EFFORT_OPTIONS}
          value={formData.manual_conversion_effort}
          onChange={(v) => updateField('manual_conversion_effort', v)}
          ariaLabel="Without CViche, how long would it take to manually convert this CV to WCM format?"
          required
        />
      </QuestionRow>
      <QuestionRow label="How long did it take to correct the CViche output?" helper="Required">
        <ChoiceRow
          options={EFFORT_OPTIONS}
          value={formData.correction_effort}
          onChange={(v) => updateField('correction_effort', v)}
          ariaLabel="How long did it take to correct the CViche output?"
          required
        />
      </QuestionRow>

      <SectionLabel>Enrichment</SectionLabel>
      <QuestionRow label="Quality of AI-enriched data" helper="Optional">
        <RatingButtonRow
          min={1}
          max={5}
          value={formData.enrichment_quality}
          onChange={(v) => updateField('enrichment_quality', v)}
          lowLabel="Poor quality"
          highLabel="Excellent quality"
          ariaLabel="How would you rate the quality of AI-enriched data?"
        />
      </QuestionRow>
      <QuestionRow label="Did CViche generate a research summary for this CV?" helper="Optional">
        <ChoiceRow
          options={[
            { value: true, label: 'Yes' },
            { value: false, label: 'No' },
          ]}
          value={formData.summary_generated}
          onChange={(v) => updateField('summary_generated', v)}
          ariaLabel="Did CViche generate a research summary for this CV?"
        />
      </QuestionRow>
      {formData.summary_generated === true && (
        <QuestionRow label="How would you rate the research summary?" helper="Optional">
          <RatingButtonRow
            min={1}
            max={5}
            value={formData.summary_quality}
            onChange={(v) => updateField('summary_quality', v)}
            lowLabel="Poor quality"
            highLabel="Excellent quality"
            ariaLabel="How would you rate the research summary?"
          />
        </QuestionRow>
      )}

      <SectionLabel>Overall</SectionLabel>
      <QuestionRow label="How likely are you to recommend CViche to a colleague?" helper="Required">
        <RatingButtonRow
          min={1}
          max={5}
          value={formData.likelihood_to_recommend}
          onChange={(v) => updateField('likelihood_to_recommend', v)}
          lowLabel="Not at all likely"
          highLabel="Extremely likely"
          ariaLabel="How likely are you to recommend CViche to a colleague?"
        />
      </QuestionRow>

      <div className="mt-4 pt-4 border-t border-sand-200 flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-gray-500">
          {canSubmit ? '' : 'Answer the questions marked Required to submit.'}
        </span>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit || submitting}
          className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-semibold hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
        >
          {submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Submitting...
            </>
          ) : (
            'Submit review'
          )}
        </button>
      </div>
    </section>
  )
}
