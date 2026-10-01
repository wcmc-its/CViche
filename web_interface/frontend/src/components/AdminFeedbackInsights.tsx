import { useState, useEffect } from 'react'
import { Loader2, Download, MessageSquare, Trash2 } from 'lucide-react'
import type { FeedbackData, AggregatedScores } from '../types'
import { exportFeedbackCsv, deleteFeedback } from '../api/admin'
import { adminRoutes } from '../api/routes'
import DeleteFeedbackModal from './DeleteFeedbackModal'

const EFFORT_LABELS: Record<string, string> = {
  '< 5 minutes': '< 5 min',
  '5-15 minutes': '5-15 min',
  '15-30 minutes': '15-30 min',
  '30-60 minutes': '30-60 min',
  '1-2 hours': '1-2 hrs',
  '2-4 hours': '2-4 hrs',
  '4-8 hours': '4-8 hrs',
  '8+ hours': '8+ hrs',
  'not_sure': 'Not sure',
  '0 minutes': '0 min',
}

const EFFORT_ORDER = [
  '0 minutes',
  '< 5 minutes',
  '5-15 minutes',
  '15-30 minutes',
  '30-60 minutes',
  '1-2 hours',
  '2-4 hours',
  '4-8 hours',
  '8+ hours',
  'not_sure',
]

// Every structured answer, in questionnaire order. The Individual Submissions
// list renders each non-empty one so admins see the full response, not just the
// biggest-issue free text.
const ANSWER_FIELDS: { key: keyof FeedbackData; label: string }[] = [
  { key: 'reviewer_role', label: 'Reviewer role' },
  { key: 'overall_accuracy', label: 'Accuracy (1-10)' },
  { key: 'overall_completeness', label: 'Completeness (1-10)' },
  { key: 'overall_usefulness', label: 'Usefulness (1-5)' },
  { key: 'enrichment_quality', label: 'Enrichment quality (1-5)' },
  { key: 'summary_generated', label: 'Summary generated' },
  { key: 'summary_quality', label: 'Summary quality (1-5)' },
  { key: 'manual_conversion_effort', label: 'Manual conversion effort' },
  { key: 'correction_effort', label: 'Correction effort' },
  { key: 'issue_missing_content', label: 'Missing content' },
  { key: 'issue_split_merged', label: 'Split / merged' },
  { key: 'issue_wrong_section', label: 'Wrong section' },
  { key: 'issue_inaccurate', label: 'Inaccurate' },
  { key: 'issue_ai_enrichment', label: 'AI enrichment' },
  { key: 'issue_formatting', label: 'Formatting' },
  { key: 'issue_locations', label: 'Issue locations' },
  { key: 'likelihood_to_recommend', label: 'Likelihood to recommend (1-5)' },
]

// Render an answer as a display string; '' means "not answered" (hide it).
function formatAnswer(key: keyof FeedbackData, value: string | number | null): string {
  if (value === null || value === undefined || value === '') return ''
  if (key === 'summary_generated') return value ? 'Yes' : 'No'
  if (key === 'issue_locations') {
    try {
      const arr = JSON.parse(String(value))
      return Array.isArray(arr) ? arr.join(', ') : String(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

export function parseFeedbackCsv(csv: string): FeedbackData[] {
  // Split into rows on newlines that are NOT inside a quoted field — the
  // free-text biggest_issue column can contain embedded newlines, which a
  // naive split('\n') would shatter into bogus rows.
  const lines = splitCSVRows(csv).filter((l) => l.trim())
  if (lines.length <= 1) return []

  const headers = lines[0].split(',')
  return lines.slice(1).map((line) => {
    const values = parseCSVLine(line)
    const row: Record<string, string> = {}
    headers.forEach((h, i) => {
      row[h.trim()] = (values[i] || '').trim()
    })
    const intOrNull = (k: string) => (row[k] ? parseInt(row[k]) : null)
    return {
      id: parseInt(row['id']) || 0,
      run_id: row['run_id'] || '',
      user_email: row['user_email'] || '',
      reviewer_role: row['reviewer_role'] || '',
      overall_accuracy: intOrNull('overall_accuracy'),
      overall_completeness: intOrNull('overall_completeness'),
      overall_usefulness: parseInt(row['overall_usefulness']) || 0,
      manual_conversion_effort: row['manual_conversion_effort'] || '',
      correction_effort: row['correction_effort'] || '',
      enrichment_quality: intOrNull('enrichment_quality'),
      summary_generated: intOrNull('summary_generated'),
      summary_quality: intOrNull('summary_quality'),
      issue_missing_content: row['issue_missing_content'] || '',
      issue_split_merged: row['issue_split_merged'] || '',
      issue_wrong_section: row['issue_wrong_section'] || '',
      issue_inaccurate: row['issue_inaccurate'] || '',
      issue_ai_enrichment: row['issue_ai_enrichment'] || '',
      issue_formatting: row['issue_formatting'] || '',
      issue_locations: row['issue_locations'] || '',
      biggest_issue: row['biggest_issue'] || '',
      likelihood_to_recommend: parseInt(row['likelihood_to_recommend']) || 0,
      submitted_at: row['submitted_at'] || '',
    }
  })
}

function splitCSVRows(csv: string): string[] {
  const rows: string[] = []
  let current = ''
  let inQuotes = false
  for (let i = 0; i < csv.length; i++) {
    const ch = csv[i]
    if (ch === '"') {
      inQuotes = !inQuotes
      current += ch
    } else if ((ch === '\n' || ch === '\r') && !inQuotes) {
      // Treat \r\n as a single break; don't emit an empty row for the \n.
      if (ch === '\r' && csv[i + 1] === '\n') i++
      rows.push(current)
      current = ''
    } else {
      current += ch
    }
  }
  if (current) rows.push(current)
  return rows
}

function parseCSVLine(line: string): string[] {
  const result: string[] = []
  let current = ''
  let inQuotes = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (ch === '"') {
      // A doubled quote ("") inside a quoted field is an escaped literal quote.
      if (inQuotes && line[i + 1] === '"') {
        current += '"'
        i++
      } else {
        inQuotes = !inQuotes
      }
    } else if (ch === ',' && !inQuotes) {
      result.push(current)
      current = ''
    } else {
      current += ch
    }
  }
  result.push(current)
  return result
}

export default function AdminFeedbackInsights() {
  const [feedback, setFeedback] = useState<FeedbackData[]>([])
  const [loading, setLoading] = useState(true)
  // Row queued for deletion (drives the confirmation modal). Null when closed.
  const [pendingDelete, setPendingDelete] = useState<FeedbackData | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  useEffect(() => {
    fetchFeedback()
  }, [])

  const fetchFeedback = async () => {
    try {
      const res = await exportFeedbackCsv()
      if (res.ok) {
        const csvText = await res.text()
        const parsed = parseFeedbackCsv(csvText)
        setFeedback(parsed)
      }
    } catch (err) {
      console.error('Failed to fetch feedback:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await deleteFeedback(pendingDelete.id)
      setPendingDelete(null)
      await fetchFeedback()
    } catch (err) {
      console.error('Failed to delete feedback:', err)
      const message =
        err && typeof err === 'object' && 'message' in err
          ? String((err as { message: unknown }).message)
          : 'Failed to delete feedback. Please try again.'
      setDeleteError(message)
    } finally {
      setIsDeleting(false)
    }
  }

  const handleExport = () => {
    window.open(adminRoutes.exportFeedback(), '_blank')
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    )
  }

  if (feedback.length === 0) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-sand-300 p-12 text-center">
        <MessageSquare className="h-12 w-12 text-gray-300 mx-auto mb-4" aria-hidden="true" />
        <h3 className="text-lg font-medium text-gray-900 mb-2">No feedback collected yet</h3>
        <p className="text-sm text-gray-500 max-w-md mx-auto">
          Feedback data will appear here once users start submitting reviews of their pipeline runs.
        </p>
      </div>
    )
  }

  // Compute aggregated scores
  const scores: AggregatedScores = computeAggregatedScores(feedback)

  // Compute effort distributions
  const manualEffortDist = computeDistribution(
    feedback.map((f) => f.manual_conversion_effort)
  )
  const correctionEffortDist = computeDistribution(
    feedback.map((f) => f.correction_effort)
  )

  return (
    <div className="space-y-6">
      {/* Export button */}
      <div className="flex justify-end">
        <button
          onClick={handleExport}
          className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
        >
          <Download className="w-4 h-4" aria-hidden="true" />
          Export CSV
        </button>
      </div>

      {/* Average Scores */}
      <div className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
        <h3 className="text-sm font-semibold text-gray-900 mb-4">Average Scores</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          <ScoreCard
            label="Accuracy"
            value={scores.accuracy.avg}
            max={10}
            count={scores.accuracy.count}
          />
          <ScoreCard
            label="Completeness"
            value={scores.completeness.avg}
            max={10}
            count={scores.completeness.count}
          />
          <ScoreCard
            label="Usefulness"
            value={scores.usefulness.avg}
            max={5}
            count={scores.usefulness.count}
          />
          <ScoreCard
            label="Likelihood to Recommend"
            value={scores.recommend.avg}
            max={5}
            count={scores.recommend.count}
          />
        </div>
      </div>

      {/* Time Savings Analysis */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
          <h3 className="text-sm font-semibold text-gray-900 mb-4">
            Manual Conversion Effort
          </h3>
          <p className="text-xs text-gray-500 mb-3">
            "How long would manual reformatting take?"
          </p>
          <EffortBars distribution={manualEffortDist} total={feedback.length} />
        </div>
        <div className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
          <h3 className="text-sm font-semibold text-gray-900 mb-4">
            Correction Effort
          </h3>
          <p className="text-xs text-gray-500 mb-3">
            "How long to correct the CViche output?"
          </p>
          <EffortBars distribution={correctionEffortDist} total={feedback.length} />
        </div>
      </div>

      {/* Summary stats */}
      <div className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
        <h3 className="text-sm font-semibold text-gray-900 mb-2">
          Total Responses: {feedback.length}
        </h3>
        <p className="text-xs text-gray-500">
          Covering {new Set(feedback.map((f) => f.run_id)).size} unique runs
          from {new Set(feedback.map((f) => f.user_email)).size} users.
        </p>
      </div>

      {/* Individual submissions — lets an admin purge a garbage/abusive
          response that would otherwise pollute the aggregates above. */}
      <div className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
        <h3 className="text-sm font-semibold text-gray-900 mb-4">
          Individual Submissions
        </h3>
        {deleteError && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {deleteError}
          </div>
        )}
        <ul className="divide-y divide-gray-100">
          {feedback.map((f) => (
            <li key={f.id} className="flex items-start justify-between gap-4 py-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="font-mono font-medium text-gray-700">{f.run_id}</span>
                  <span>·</span>
                  <span className="truncate">{f.user_email || 'unknown'}</span>
                  {f.submitted_at && (
                    <>
                      <span>·</span>
                      <span>{f.submitted_at}</span>
                    </>
                  )}
                </div>
                <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
                  {ANSWER_FIELDS.map(({ key, label }) => {
                    const val = formatAnswer(key, f[key] as string | number | null)
                    if (!val) return null
                    return (
                      <div key={key} className="min-w-0">
                        <dt className="text-gray-400">{label}</dt>
                        <dd className="text-gray-800 break-words">{val}</dd>
                      </div>
                    )
                  })}
                </dl>
                <p className="mt-2 text-sm text-gray-800 break-words">
                  <span className="text-xs text-gray-400">Biggest issue: </span>
                  {f.biggest_issue.trim() || (
                    <span className="italic text-gray-400">(no biggest-issue text)</span>
                  )}
                </p>
              </div>
              <button
                onClick={() => {
                  setDeleteError(null)
                  setPendingDelete(f)
                }}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
                aria-label={`Delete feedback for run ${f.run_id}`}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                Delete
              </button>
            </li>
          ))}
        </ul>
      </div>

      <DeleteFeedbackModal
        isOpen={pendingDelete !== null}
        runId={pendingDelete?.run_id ?? ''}
        biggestIssue={pendingDelete?.biggest_issue ?? ''}
        isDeleting={isDeleting}
        onConfirm={handleConfirmDelete}
        onClose={() => {
          if (!isDeleting) setPendingDelete(null)
        }}
      />
    </div>
  )
}

function computeAggregatedScores(feedback: FeedbackData[]): AggregatedScores {
  const accuracy = feedback.filter((f) => f.overall_accuracy !== null)
  const completeness = feedback.filter((f) => f.overall_completeness !== null)

  return {
    accuracy: {
      avg: accuracy.length > 0
        ? accuracy.reduce((s, f) => s + (f.overall_accuracy ?? 0), 0) / accuracy.length
        : 0,
      count: accuracy.length,
    },
    completeness: {
      avg: completeness.length > 0
        ? completeness.reduce((s, f) => s + (f.overall_completeness ?? 0), 0) / completeness.length
        : 0,
      count: completeness.length,
    },
    usefulness: {
      avg: feedback.length > 0
        ? feedback.reduce((s, f) => s + f.overall_usefulness, 0) / feedback.length
        : 0,
      count: feedback.length,
    },
    recommend: {
      avg: feedback.length > 0
        ? feedback.reduce((s, f) => s + f.likelihood_to_recommend, 0) / feedback.length
        : 0,
      count: feedback.length,
    },
  }
}

function computeDistribution(values: string[]): Record<string, number> {
  const dist: Record<string, number> = {}
  for (const v of values) {
    if (v) dist[v] = (dist[v] || 0) + 1
  }
  return dist
}

function ScoreCard({
  label,
  value,
  max,
  count,
}: {
  label: string
  value: number
  max: number
  count: number
}) {
  const percentage = max > 0 ? (value / max) * 100 : 0

  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-sm text-gray-600">{label}</span>
        <span className="text-xs text-gray-400">n={count}</span>
      </div>
      <div className="flex items-baseline gap-1 mb-2">
        <span className="text-2xl font-bold text-gray-900">
          {count > 0 ? value.toFixed(1) : '--'}
        </span>
        <span className="text-sm text-gray-400">/ {max}</span>
      </div>
      <div className="w-full bg-gray-100 rounded-full h-2">
        <div
          className="bg-primary-500 h-2 rounded-full transition-all"
          style={{ width: `${count > 0 ? percentage : 0}%` }}
        />
      </div>
    </div>
  )
}

function EffortBars({
  distribution,
  total,
}: {
  distribution: Record<string, number>
  total: number
}) {
  if (total === 0) return <p className="text-sm text-gray-400">No data</p>

  // Order bars by the standard effort order
  const orderedKeys = EFFORT_ORDER.filter((k) => distribution[k])

  // Also add any keys not in the standard order
  const extraKeys = Object.keys(distribution).filter(
    (k) => !EFFORT_ORDER.includes(k)
  )
  const allKeys = [...orderedKeys, ...extraKeys]

  const maxCount = Math.max(...Object.values(distribution), 1)

  return (
    <div className="space-y-2">
      {allKeys.map((key) => {
        const count = distribution[key] || 0
        const pct = Math.round((count / total) * 100)
        const barWidth = (count / maxCount) * 100

        return (
          <div key={key} className="flex items-center gap-3">
            <span className="text-xs text-gray-600 w-20 text-right shrink-0">
              {EFFORT_LABELS[key] || key}
            </span>
            <div className="flex-1 bg-gray-100 rounded-full h-4 relative">
              <div
                className="bg-primary-500 h-4 rounded-full transition-all"
                style={{ width: `${barWidth}%` }}
              />
            </div>
            <span className="text-xs text-gray-500 w-14 shrink-0">
              {count} ({pct}%)
            </span>
          </div>
        )
      })}
    </div>
  )
}
