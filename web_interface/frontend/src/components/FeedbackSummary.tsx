import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import type { FeedbackDetail } from '../types'
import { getRunFeedbackAll } from '../api/feedback'
import { useAuth } from '../contexts/AuthContext'
import { formatDate } from '../utils'
import ErrorBanner from './ErrorBanner'
import {
  EFFORT_OPTIONS,
  ISSUE_FIELDS,
  PROBLEMS_LABEL,
  QUESTION_LABELS,
  RATING_SCALES,
  REVIEWER_ROLES,
} from './feedbackQuestions'

const CARD = 'bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] p-5 sm:p-6'
const NO_ANSWER = '—'
/** Dashboard link that opens the Feedback tab (AdminDashboard reads ?tab=). */
export const ALL_FEEDBACK_HREF = '/admin?tab=Feedback'
const LOAD_ERROR = 'Unable to load the feedback for this run. Please try refreshing the page.'

interface Answer {
  label: string
  value: ReactNode
}

const optionLabel = (options: { value: string; label: string }[], value: string): string =>
  options.find((o) => o.value === value)?.label ?? value

const scoreText = (value: number | null, max: number): ReactNode =>
  value === null ? NO_ANSWER : `${value} / ${max}`

const textOrDash = (value: string | null): ReactNode => (value && value.trim() ? value : NO_ANSWER)

function Chips({ items }: { items: string[] }) {
  if (items.length === 0) return <>{NO_ANSWER}</>
  return (
    <ul className="flex flex-wrap gap-2">
      {items.map((item) => (
        <li key={item} className="rounded-full border border-ink bg-sand-50 px-2.5 py-0.5 text-[13px] font-medium text-gray-900">
          {item}
        </li>
      ))}
    </ul>
  )
}

/** Ticked problem cards: the card label as a chip, the reviewer's "which entries" text under it. */
function IssueChips({ feedback }: { feedback: FeedbackDetail }) {
  const ticked = ISSUE_FIELDS.filter((issue) => feedback[issue.key] !== null)
  if (ticked.length === 0) return <>{NO_ANSWER}</>
  return (
    <ul className="space-y-2">
      {ticked.map((issue) => {
        const detail = feedback[issue.key]
        return (
          <li key={issue.key}>
            <span className="inline-block rounded-full border border-ink bg-sand-50 px-2.5 py-0.5 text-[13px] font-medium text-gray-900">
              {issue.label}
            </span>
            {detail && <span className="mt-1 block text-sm text-gray-700 [overflow-wrap:anywhere]">{detail}</span>}
          </li>
        )
      })}
    </ul>
  )
}

function summaryGeneratedText(value: number | null): string {
  if (value === null) return NO_ANSWER
  return value ? 'Yes' : 'No'
}

/** Every question the form asks, in form order, with this submission's answer. */
function answersFor(f: FeedbackDetail): Answer[] {
  const answers: Answer[] = [
    { label: QUESTION_LABELS.overall_usefulness, value: scoreText(f.overall_usefulness, RATING_SCALES.overall_usefulness.max) },
    { label: QUESTION_LABELS.overall_accuracy, value: scoreText(f.overall_accuracy, RATING_SCALES.overall_accuracy.max) },
    { label: QUESTION_LABELS.overall_completeness, value: scoreText(f.overall_completeness, RATING_SCALES.overall_completeness.max) },
    { label: PROBLEMS_LABEL, value: <IssueChips feedback={f} /> },
    { label: QUESTION_LABELS.issue_locations, value: <Chips items={f.issue_locations ?? []} /> },
    { label: QUESTION_LABELS.biggest_issue, value: textOrDash(f.biggest_issue) },
    { label: QUESTION_LABELS.manual_conversion_effort, value: optionLabel(EFFORT_OPTIONS, f.manual_conversion_effort) },
    { label: QUESTION_LABELS.correction_effort, value: optionLabel(EFFORT_OPTIONS, f.correction_effort) },
    { label: QUESTION_LABELS.enrichment_quality, value: scoreText(f.enrichment_quality, RATING_SCALES.enrichment_quality.max) },
    { label: QUESTION_LABELS.summary_generated, value: summaryGeneratedText(f.summary_generated) },
  ]
  if (f.summary_generated === 1) {
    answers.push({ label: QUESTION_LABELS.summary_quality, value: scoreText(f.summary_quality, RATING_SCALES.summary_quality.max) })
  }
  answers.push({
    label: QUESTION_LABELS.likelihood_to_recommend,
    value: scoreText(f.likelihood_to_recommend, RATING_SCALES.likelihood_to_recommend.max),
  })
  return answers
}

function SubmissionCard({ feedback }: { feedback: FeedbackDetail }) {
  const role = optionLabel(REVIEWER_ROLES, feedback.reviewer_role)
  return (
    <article className="py-5 first:pt-0 last:pb-0 border-b border-sand-200 last:border-b-0">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-sm font-semibold text-gray-900">
          {feedback.display_name}
          <span className="ml-2 font-normal text-gray-500">{role}</span>
        </h3>
        <time className="text-[13px] text-gray-500" dateTime={feedback.submitted_at ?? undefined}>
          {formatDate(feedback.submitted_at)}
        </time>
      </header>
      <dl className="mt-3">
        {answersFor(feedback).map((answer) => (
          <div key={answer.label} className="py-2.5 border-t border-sand-200 first:border-t-0 grid gap-x-6 gap-y-1 sm:grid-cols-[240px_minmax(0,1fr)] items-start">
            <dt className="text-sm font-medium text-gray-900">{answer.label}</dt>
            <dd className="text-sm text-gray-700 [overflow-wrap:anywhere]">{answer.value}</dd>
          </div>
        ))}
      </dl>
    </article>
  )
}

/** Read-only feedback on a run: every submission, newest first. Shown once the viewer has reviewed it. */
export default function FeedbackSummary({ runId }: { runId: string }) {
  const isAdmin = useAuth().user?.role === 'admin'
  const [submissions, setSubmissions] = useState<FeedbackDetail[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getRunFeedbackAll(runId)
      .then((data) => { if (!cancelled) setSubmissions(data) })
      .catch((err) => {
        console.error('Error fetching run feedback:', err)
        if (!cancelled) setError(LOAD_ERROR)
      })
    return () => { cancelled = true }
  }, [runId])

  return (
    <section className={CARD} aria-label="Feedback">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-4">
        <h2 className="text-[17px] font-semibold text-gray-900">Feedback</h2>
        {isAdmin && (
          <Link to={ALL_FEEDBACK_HREF} className="text-[13px] text-primary-700 hover:underline">
            All feedback
          </Link>
        )}
      </div>
      {error && <ErrorBanner message={error} />}
      {!error && submissions === null && (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" aria-label="Loading feedback" />
        </div>
      )}
      {submissions?.map((feedback) => <SubmissionCard key={feedback.id} feedback={feedback} />)}
    </section>
  )
}
