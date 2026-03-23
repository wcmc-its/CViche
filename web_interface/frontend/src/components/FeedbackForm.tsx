import { useState, useEffect, useRef, useCallback } from 'react'
import { CheckCircle2, Loader2 } from 'lucide-react'
import ErrorBanner from './ErrorBanner'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FeedbackFormProps {
  runId: string
}

interface FeedbackFormData {
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

interface WcmSection {
  section_id: string
  section_name: string
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

const STEP_HEADINGS = [
  'Your Role & Ratings',
  'Issues Found',
  'Effort & Enrichment',
  'Recommend',
]

// ---------------------------------------------------------------------------
// Internal sub-components
// ---------------------------------------------------------------------------

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
      <div role="radiogroup" aria-label={ariaLabel} className="flex gap-2">
        {buttons.map((n) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={value === n}
            onClick={() => onChange(n)}
            className={`flex-1 py-2 rounded-lg border-2 text-sm font-medium transition-colors ${
              value === n
                ? 'border-primary-600 bg-primary-50 text-primary-700'
                : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
            }`}
          >
            {n}
          </button>
        ))}
      </div>
      <div className="flex justify-between mt-1 text-xs text-gray-500">
        <span>{lowLabel}</span>
        <span>{highLabel}</span>
      </div>
    </div>
  )
}

function ProgressDots({ currentStep }: { currentStep: number }) {
  return (
    <div className="flex items-center gap-3 mb-6">
      <div
        className="flex items-center gap-2"
        aria-label={`Form progress: step ${currentStep} of 4`}
      >
        {[1, 2, 3, 4].map((step) => (
          <div
            key={step}
            className={`rounded-full w-2.5 h-2.5 ${
              step <= currentStep ? 'bg-primary-600' : 'bg-gray-300'
            }`}
          />
        ))}
      </div>
      <span className="text-sm text-gray-500">Step {currentStep} of 4</span>
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
      <p className="text-sm text-gray-600 mt-1">
        Thank you for your feedback.{formattedDate ? ` Submitted on ${formattedDate}.` : ''}
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function FeedbackForm({ runId }: FeedbackFormProps) {
  const [currentStep, setCurrentStep] = useState<number>(1)
  const [formData, setFormData] = useState<FeedbackFormData>(INITIAL_FORM_DATA)
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [existingFeedback, setExistingFeedback] = useState<Record<string, unknown> | null>(null)
  const [wcmSections, setWcmSections] = useState<WcmSection[]>([])
  const [stepOpacity, setStepOpacity] = useState(1)

  const stepHeadingRef = useRef<HTMLHeadingElement>(null)

  // ---- helpers ----

  const updateField = useCallback(
    <K extends keyof FeedbackFormData>(key: K, value: FeedbackFormData[K]) => {
      setFormData((prev) => ({ ...prev, [key]: value }))
    },
    [],
  )

  const canAdvance = (step: number): boolean => {
    switch (step) {
      case 1:
        if (formData.reviewer_role === '') return false
        if (formData.reviewer_role === 'other' && formData.reviewer_role_other.trim() === '')
          return false
        if (formData.overall_usefulness === null) return false
        return true
      case 2:
        return true
      case 3:
        return formData.manual_conversion_effort !== '' && formData.correction_effort !== ''
      case 4:
        return formData.likelihood_to_recommend !== null
      default:
        return false
    }
  }

  const changeStep = (nextStep: number) => {
    setStepOpacity(0)
    setTimeout(() => {
      setCurrentStep(nextStep)
      setStepOpacity(1)
      // Focus the heading of the new step after transition
      setTimeout(() => {
        stepHeadingRef.current?.focus()
      }, 50)
    }, 200)
  }

  // ---- fetch existing feedback on mount ----

  useEffect(() => {
    let cancelled = false

    async function loadFeedback() {
      try {
        const res = await fetch(`/api/run/${runId}/feedback`)
        if (!res.ok) throw new Error('fetch-failed')
        const data = await res.json()

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
      const res = await fetch(`/api/run/${runId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

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

  // ---- issue helpers ----

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

  // ---- render: loading state ----

  if (loading) {
    return (
      <div className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8">
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" aria-label="Loading feedback form" />
        </div>
      </div>
    )
  }

  // ---- render: existing feedback ----

  if (existingFeedback) {
    return (
      <div className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8">
        <FeedbackSubmittedCard
          submittedAt={(existingFeedback as Record<string, unknown>).submitted_at as string | null}
        />
      </div>
    )
  }

  // ---- render: success after submission ----

  if (submitted) {
    return (
      <div className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8">
        <div className="flex flex-col items-center text-center py-8">
          <CheckCircle2 className="h-8 w-8 text-success-600 mb-3" aria-hidden="true" />
          <h3 className="text-xl font-semibold text-gray-900">Thank you!</h3>
          <p className="text-sm text-gray-600 mt-2">
            Your feedback has been recorded. It helps us improve CViche for everyone.
          </p>
        </div>
      </div>
    )
  }

  // ---- render: wizard form ----

  return (
    <div className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8">
      <ProgressDots currentStep={currentStep} />

      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {/* Step content with opacity transition */}
      <div
        className="transition-opacity duration-200"
        style={{ opacity: stepOpacity }}
      >
        <h2
          ref={stepHeadingRef}
          tabIndex={-1}
          className="text-xl font-semibold text-gray-900 mb-4 outline-none"
        >
          {STEP_HEADINGS[currentStep - 1]}
        </h2>

        {/* ---- Step 1: Your Role & Ratings ---- */}
        {currentStep === 1 && (
          <div className="space-y-6">
            {/* Reviewer role */}
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">
                Your role
              </label>
              <div className="space-y-2">
                {REVIEWER_ROLES.map((role) => (
                  <label
                    key={role.value}
                    className={`flex items-center gap-3 p-3 rounded-lg border-2 cursor-pointer transition-colors ${
                      formData.reviewer_role === role.value
                        ? 'border-primary-600 bg-primary-50 text-primary-700'
                        : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                    }`}
                  >
                    <input
                      type="radio"
                      name="reviewer_role"
                      value={role.value}
                      checked={formData.reviewer_role === role.value}
                      onChange={() => updateField('reviewer_role', role.value)}
                      className="sr-only"
                      aria-required="true"
                    />
                    <span className="text-sm font-medium">{role.label}</span>
                  </label>
                ))}
              </div>
              {formData.reviewer_role === 'other' && (
                <input
                  type="text"
                  value={formData.reviewer_role_other}
                  onChange={(e) => updateField('reviewer_role_other', e.target.value)}
                  placeholder="Describe your role"
                  className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 placeholder-gray-400 focus:border-primary-500 focus:ring-2 focus:ring-primary-500 focus:outline-none transition-colors"
                  aria-required="true"
                />
              )}
            </div>

            {/* Overall usefulness (required, 1-5) */}
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">
                How useful was the CViche output?
              </label>
              <RatingButtonRow
                min={1}
                max={5}
                value={formData.overall_usefulness}
                onChange={(v) => updateField('overall_usefulness', v)}
                lowLabel="Not useful"
                highLabel="Extremely useful"
                ariaLabel="How useful was the CViche output?"
              />
            </div>

            {/* Overall accuracy (optional, 1-10) */}
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-0.5">
                How accurate was the output?
              </label>
              <span className="block text-xs text-gray-500 mb-2">(Optional)</span>
              <RatingButtonRow
                min={1}
                max={10}
                value={formData.overall_accuracy}
                onChange={(v) => updateField('overall_accuracy', v)}
                lowLabel="Not at all accurate"
                highLabel="Perfectly accurate"
                ariaLabel="How accurate was the output?"
              />
            </div>

            {/* Overall completeness (optional, 1-10) */}
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-0.5">
                How complete was the output?
              </label>
              <span className="block text-xs text-gray-500 mb-2">(Optional)</span>
              <RatingButtonRow
                min={1}
                max={10}
                value={formData.overall_completeness}
                onChange={(v) => updateField('overall_completeness', v)}
                lowLabel="Very incomplete"
                highLabel="Fully complete"
                ariaLabel="How complete was the output?"
              />
            </div>
          </div>
        )}

        {/* ---- Step 2: Issues Found ---- */}
        {currentStep === 2 && (
          <div className="space-y-4">
            <p className="text-sm text-gray-700 mb-2">Check any issues you found:</p>

            {ISSUE_FIELDS.map((issue) => {
              const checked = formData[issue.key] !== null

              return (
                <div key={issue.key}>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => {
                        if (checked) {
                          updateField(issue.key, null)
                        } else {
                          updateField(issue.key, '')
                        }
                      }}
                      className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                    />
                    <span className="text-sm text-gray-900">{issue.label}</span>
                  </label>
                  {checked && (
                    <input
                      type="text"
                      value={formData[issue.key] ?? ''}
                      onChange={(e) => updateField(issue.key, e.target.value)}
                      placeholder={issue.placeholder}
                      className="mt-2 ml-7 w-[calc(100%-1.75rem)] rounded-lg border border-gray-300 px-3 py-2 text-gray-900 placeholder-gray-400 focus:border-primary-500 focus:ring-2 focus:ring-primary-500 focus:outline-none transition-colors"
                    />
                  )}
                </div>
              )
            })}

            {/* Issue locations -- conditional on any issue checked */}
            {anyIssueChecked && wcmSections.length > 0 && (
              <div className="mt-4">
                <label className="block text-sm font-semibold text-gray-900 mb-2">
                  Which sections were affected?
                </label>
                <div className="space-y-2">
                  {wcmSections.map((section) => (
                    <label
                      key={section.section_id}
                      className="flex items-center gap-3 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={formData.issue_locations.includes(section.section_id)}
                        onChange={() => toggleIssueLocation(section.section_id)}
                        className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                      />
                      <span className="text-sm text-gray-900">
                        {section.section_id}: {section.section_name}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* Biggest issue -- always visible */}
            <div className="mt-4">
              <label className="block text-sm font-semibold text-gray-900 mb-0.5">
                What was the biggest issue, if any?
              </label>
              <span className="block text-xs text-gray-500 mb-2">(Optional)</span>
              <textarea
                value={formData.biggest_issue}
                onChange={(e) => updateField('biggest_issue', e.target.value)}
                rows={3}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 placeholder-gray-400 focus:border-primary-500 focus:ring-2 focus:ring-primary-500 focus:outline-none transition-colors"
              />
            </div>
          </div>
        )}

        {/* ---- Step 3: Effort & Enrichment ---- */}
        {currentStep === 3 && (
          <div className="space-y-6">
            {/* Manual conversion effort */}
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">
                Without CViche, how long would it take to manually convert this CV to WCM format?
              </label>
              <select
                value={formData.manual_conversion_effort}
                onChange={(e) => updateField('manual_conversion_effort', e.target.value)}
                aria-required="true"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 focus:border-primary-500 focus:ring-2 focus:ring-primary-500 focus:outline-none transition-colors"
              >
                <option value="" disabled>
                  Select...
                </option>
                {EFFORT_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Correction effort */}
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">
                How long did it take to correct the CViche output?
              </label>
              <select
                value={formData.correction_effort}
                onChange={(e) => updateField('correction_effort', e.target.value)}
                aria-required="true"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 focus:border-primary-500 focus:ring-2 focus:ring-primary-500 focus:outline-none transition-colors"
              >
                <option value="" disabled>
                  Select...
                </option>
                {EFFORT_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Enrichment quality (optional, 1-5) */}
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-0.5">
                How would you rate the quality of AI-enriched data?
              </label>
              <span className="block text-xs text-gray-500 mb-2">(Optional)</span>
              <RatingButtonRow
                min={1}
                max={5}
                value={formData.enrichment_quality}
                onChange={(v) => updateField('enrichment_quality', v)}
                lowLabel="Poor quality"
                highLabel="Excellent quality"
                ariaLabel="How would you rate the quality of AI-enriched data?"
              />
            </div>

            {/* Summary generated toggle */}
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">
                Did CViche generate a research summary for this CV?
              </label>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => updateField('summary_generated', true)}
                  className={`flex-1 py-3 px-4 rounded-lg border-2 text-sm font-medium transition-colors ${
                    formData.summary_generated === true
                      ? 'border-primary-600 bg-primary-50 text-primary-700'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                  }`}
                >
                  Yes
                </button>
                <button
                  type="button"
                  onClick={() => updateField('summary_generated', false)}
                  className={`flex-1 py-3 px-4 rounded-lg border-2 text-sm font-medium transition-colors ${
                    formData.summary_generated === false
                      ? 'border-primary-600 bg-primary-50 text-primary-700'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                  }`}
                >
                  No
                </button>
              </div>
            </div>

            {/* Summary quality -- visible only when summary_generated === true */}
            {formData.summary_generated === true && (
              <div>
                <label className="block text-sm font-semibold text-gray-900 mb-2">
                  How would you rate the research summary?
                </label>
                <RatingButtonRow
                  min={1}
                  max={5}
                  value={formData.summary_quality}
                  onChange={(v) => updateField('summary_quality', v)}
                  lowLabel="Poor quality"
                  highLabel="Excellent quality"
                  ariaLabel="How would you rate the research summary?"
                />
              </div>
            )}
          </div>
        )}

        {/* ---- Step 4: Recommend ---- */}
        {currentStep === 4 && (
          <div className="space-y-6">
            {/* Likelihood to recommend (required, 1-5) */}
            <div>
              <label className="block text-sm font-semibold text-gray-900 mb-2">
                How likely are you to recommend CViche to a colleague?
              </label>
              <RatingButtonRow
                min={1}
                max={5}
                value={formData.likelihood_to_recommend}
                onChange={(v) => updateField('likelihood_to_recommend', v)}
                lowLabel="Not at all likely"
                highLabel="Extremely likely"
                ariaLabel="How likely are you to recommend CViche to a colleague?"
              />
            </div>
          </div>
        )}
      </div>

      {/* ---- Navigation buttons ---- */}
      <div className="flex justify-between mt-6">
        {currentStep > 1 && (
          <button
            type="button"
            onClick={() => changeStep(currentStep - 1)}
            className="px-6 py-2 rounded-lg border border-gray-300 text-gray-700 font-medium hover:bg-gray-50 transition-colors"
          >
            Back
          </button>
        )}
        <div className="ml-auto">
          {currentStep < 4 ? (
            <button
              type="button"
              disabled={!canAdvance(currentStep)}
              onClick={() => changeStep(currentStep + 1)}
              className="px-6 py-3 rounded-lg bg-primary-600 text-white font-semibold hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
            >
              Next
            </button>
          ) : (
            <button
              type="button"
              disabled={!canAdvance(4) || submitting}
              onClick={handleSubmit}
              className="px-6 py-3 rounded-lg bg-primary-600 text-white font-semibold hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  Submitting...
                </>
              ) : (
                'Submit Feedback'
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
