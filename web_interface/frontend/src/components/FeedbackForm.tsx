import { useState, useEffect, useCallback } from 'react'
import { Check, Loader2 } from 'lucide-react'
import type { FeedbackFormData, WcmSection } from '../types'
import { getFeedback, submitFeedback } from '../api/feedback'
import ErrorBanner from './ErrorBanner'
import FeedbackSummary from './FeedbackSummary'
import {
  EFFORT_OPTIONS,
  ISSUE_FIELDS,
  PROBLEMS_LABEL,
  QUESTION_LABELS,
  RATING_SCALES,
  REVIEWER_ROLES,
  SUMMARY_GENERATED_OPTIONS,
} from './feedbackQuestions'

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

const ISSUE_PLACEHOLDER = 'Which entries?'


// ---------------------------------------------------------------------------
// Styling tokens (shared by the pieces below)
// ---------------------------------------------------------------------------

const CARD = 'bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] p-5 sm:p-6'
const TOGGLE_BASE =
  'inline-flex items-center justify-center rounded-lg border px-3 py-1.5 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1'
const TOGGLE_ON = 'border-ink bg-ink text-white'
const TOGGLE_OFF = 'border-sand-400 bg-white text-gray-700 hover:bg-sand-50'
const TEXT_INPUT =
  'w-full rounded-lg border border-sand-400 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-primary-500 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none transition-colors'

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

        // Extract wcm_sections that have a section_id (the summary names them too)
        const sections: WcmSection[] = (data.run_context?.wcm_sections || []).filter(
          (s: Record<string, unknown>) => s.section_id,
        )
        setWcmSections(sections)
        if (data.feedback) setExistingFeedback(data.feedback)
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

  // ---- render: already reviewed (earlier, just now, or a duplicate submit) ----

  if (existingFeedback || submitted) return <FeedbackSummary runId={runId} sections={wcmSections} />

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
      <QuestionRow label={QUESTION_LABELS.reviewer_role} helper="Required">
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
      <QuestionRow label={QUESTION_LABELS.overall_usefulness} helper="Required">
        <RatingButtonRow
          {...RATING_SCALES.overall_usefulness}
          value={formData.overall_usefulness}
          onChange={(v) => updateField('overall_usefulness', v)}
          ariaLabel={QUESTION_LABELS.overall_usefulness}
        />
      </QuestionRow>
      <QuestionRow label={QUESTION_LABELS.overall_accuracy} helper="Optional">
        <RatingButtonRow
          {...RATING_SCALES.overall_accuracy}
          value={formData.overall_accuracy}
          onChange={(v) => updateField('overall_accuracy', v)}
          ariaLabel={QUESTION_LABELS.overall_accuracy}
        />
      </QuestionRow>
      <QuestionRow label={QUESTION_LABELS.overall_completeness} helper="Optional">
        <RatingButtonRow
          {...RATING_SCALES.overall_completeness}
          value={formData.overall_completeness}
          onChange={(v) => updateField('overall_completeness', v)}
          ariaLabel={QUESTION_LABELS.overall_completeness}
        />
      </QuestionRow>

      <SectionLabel>{PROBLEMS_LABEL}</SectionLabel>
      <p className="mt-3 text-sm text-gray-500">
        Select any that apply and say where, so we can find the entries.
      </p>
      <div className="mt-3 mb-2 space-y-2">
        {ISSUE_FIELDS.map((issue) => {
          const checked = formData[issue.key] !== null
          return (
            <label
              key={issue.key}
              className={`flex cursor-pointer items-start gap-3 rounded-xl border px-4 py-3 transition-colors ${
                checked ? 'border-ink bg-sand-50' : 'border-sand-300 bg-white hover:bg-sand-50'
              }`}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => updateField(issue.key, checked ? null : '')}
                className="peer sr-only"
              />
              <span
                aria-hidden="true"
                className={`mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded border peer-focus-visible:ring-2 peer-focus-visible:ring-primary-500 peer-focus-visible:ring-offset-1 ${
                  checked ? 'border-ink bg-ink text-white' : 'border-sand-400 bg-white'
                }`}
              >
                {checked && <Check className="h-3 w-3" strokeWidth={3} />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-gray-900">{issue.label}</span>
                <span className="block text-xs text-gray-500">{issue.description}</span>
                {checked && (
                  <input
                    type="text"
                    value={formData[issue.key] ?? ''}
                    onChange={(e) => updateField(issue.key, e.target.value)}
                    placeholder={ISSUE_PLACEHOLDER}
                    aria-label={`${issue.label}: ${ISSUE_PLACEHOLDER}`}
                    className={`mt-2 ${TEXT_INPUT}`}
                  />
                )}
              </span>
            </label>
          )
        })}
      </div>

      {anyIssueChecked && wcmSections.length > 0 && (
        <QuestionRow label={QUESTION_LABELS.issue_locations}>
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

      <QuestionRow label={QUESTION_LABELS.biggest_issue} helper="Optional">
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
        label={QUESTION_LABELS.manual_conversion_effort}
        helper="Required"
      >
        <ChoiceRow
          options={EFFORT_OPTIONS}
          value={formData.manual_conversion_effort}
          onChange={(v) => updateField('manual_conversion_effort', v)}
          ariaLabel={QUESTION_LABELS.manual_conversion_effort}
          required
        />
      </QuestionRow>
      <QuestionRow label={QUESTION_LABELS.correction_effort} helper="Required">
        <ChoiceRow
          options={EFFORT_OPTIONS}
          value={formData.correction_effort}
          onChange={(v) => updateField('correction_effort', v)}
          ariaLabel={QUESTION_LABELS.correction_effort}
          required
        />
      </QuestionRow>

      <SectionLabel>Enrichment</SectionLabel>
      <QuestionRow label={QUESTION_LABELS.enrichment_quality} helper="Optional">
        <RatingButtonRow
          {...RATING_SCALES.enrichment_quality}
          value={formData.enrichment_quality}
          onChange={(v) => updateField('enrichment_quality', v)}
          ariaLabel={QUESTION_LABELS.enrichment_quality}
        />
      </QuestionRow>
      <QuestionRow label={QUESTION_LABELS.summary_generated} helper="Optional">
        <ChoiceRow
          options={SUMMARY_GENERATED_OPTIONS}
          value={formData.summary_generated}
          onChange={(v) => updateField('summary_generated', v)}
          ariaLabel={QUESTION_LABELS.summary_generated}
        />
      </QuestionRow>
      {formData.summary_generated === true && (
        <QuestionRow label={QUESTION_LABELS.summary_quality} helper="Optional">
          <RatingButtonRow
            {...RATING_SCALES.summary_quality}
            value={formData.summary_quality}
            onChange={(v) => updateField('summary_quality', v)}
            ariaLabel={QUESTION_LABELS.summary_quality}
          />
        </QuestionRow>
      )}

      <SectionLabel>Overall</SectionLabel>
      <QuestionRow label={QUESTION_LABELS.likelihood_to_recommend} helper="Required">
        <RatingButtonRow
          {...RATING_SCALES.likelihood_to_recommend}
          value={formData.likelihood_to_recommend}
          onChange={(v) => updateField('likelihood_to_recommend', v)}
          ariaLabel={QUESTION_LABELS.likelihood_to_recommend}
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
