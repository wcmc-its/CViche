import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { XCircle, Clock, Loader2 } from 'lucide-react'
import type { AdminRun, FeedbackData, Stats } from '../types'
import { exportFeedbackCsv, getAdminRuns } from '../api/admin'
import { formatCost, formatDate, formatDuration } from '../utils'
import { parseFeedbackCsv } from './AdminFeedbackInsights'
import { useCanSeeCost } from '../contexts/AuthContext'
import WhoSubmitsCard from './WhoSubmitsCard'

const CARD = 'bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)]'
const MAX_ATTENTION_ROWS = 5
const MAX_FEEDBACK_ROWS = 3
// Recent completed runs scanned for "unusually long" ones.
const LONG_RUN_SCAN_LIMIT = 100

interface AttentionItem {
  run: AdminRun
  kind: 'failed' | 'long'
}

function reporter(run: AdminRun): string {
  return run.user_display_name || run.user_email || 'Unknown user'
}

function StatTile({ label, value, sublabel }: { label: string; value: string; sublabel: string }) {
  return (
    <div className={`${CARD} px-[18px] py-4`}>
      <p className="text-[13px] text-gray-500">{label}</p>
      <p className="mt-1 text-[26px] leading-8 font-semibold text-gray-900 tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-gray-500">{sublabel}</p>
    </div>
  )
}

function NeedsAttention({ p95 }: { p95: number | null | undefined }) {
  const [items, setItems] = useState<AttentionItem[] | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const failedParams = new URLSearchParams({ status: 'failed', limit: String(MAX_ATTENTION_ROWS) })
        const completeParams = new URLSearchParams({ status: 'complete', limit: String(LONG_RUN_SCAN_LIMIT) })
        const [failed, complete] = await Promise.all([
          getAdminRuns(failedParams.toString()),
          p95 != null ? getAdminRuns(completeParams.toString()) : Promise.resolve(null),
        ])
        const long = (complete?.runs ?? []).filter(
          (r) => r.duration_seconds != null && p95 != null && r.duration_seconds > p95,
        )
        const merged: AttentionItem[] = [
          ...failed.runs.map((run) => ({ run, kind: 'failed' as const })),
          ...long.map((run) => ({ run, kind: 'long' as const })),
        ]
          .sort((a, b) => (b.run.started_at ?? '').localeCompare(a.run.started_at ?? ''))
          .slice(0, MAX_ATTENTION_ROWS)
        if (!cancelled) setItems(merged)
      } catch (err) {
        console.error('Failed to load runs needing attention:', err)
        if (!cancelled) setError(true)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [p95])

  return (
    <section className={`${CARD} p-5 min-w-0`} aria-labelledby="needs-attention-heading">
      <h2 id="needs-attention-heading" className="text-[15px] font-semibold text-gray-900 mb-3">
        Needs attention
      </h2>
      {error ? (
        <p className="text-sm text-gray-500">Could not load runs.</p>
      ) : items === null ? (
        <Loader2 className="h-5 w-5 animate-spin text-gray-400" aria-label="Loading" />
      ) : items.length === 0 ? (
        <p className="text-sm text-gray-500">Nothing needs attention</p>
      ) : (
        <ul>
          {items.map(({ run, kind }) => (
            <li
              key={run.run_id}
              className="flex items-center justify-between gap-3 py-3 border-b border-sand-200 last:border-b-0 first:pt-1"
            >
              <div className="min-w-0">
                {kind === 'failed' ? (
                  <p className="flex items-center gap-1.5 text-sm font-medium text-red-600">
                    <XCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
                    Failed
                  </p>
                ) : (
                  <p className="flex items-center gap-1.5 text-sm font-medium text-orange-600">
                    <Clock className="h-4 w-4 shrink-0" aria-hidden="true" />
                    Unusually long · {formatDuration(run.duration_seconds)}
                  </p>
                )}
                <p className="mt-0.5 text-xs text-gray-500 [overflow-wrap:anywhere]">
                  {reporter(run)} · {run.filename} · {formatDate(run.started_at)}
                </p>
              </div>
              <Link
                to={`/run/${run.run_id}`}
                className="shrink-0 rounded-lg border border-sand-400 bg-white px-3 py-1.5 text-[13px] font-medium text-gray-900 hover:bg-sand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
              >
                {kind === 'failed' ? 'Open run' : 'Inspect'}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function StepTimes({ steps }: { steps: NonNullable<Stats['step_avg_seconds']> }) {
  const max = Math.max(1, ...steps.map((s) => s.avg_seconds))
  return (
    <section className={`${CARD} p-5 min-w-0`} aria-labelledby="step-times-heading">
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <h2 id="step-times-heading" className="text-[15px] font-semibold text-gray-900">
          Average time per step
        </h2>
        <span className="text-xs text-gray-500">completed runs</span>
      </div>
      {steps.length === 0 ? (
        <p className="text-sm text-gray-500">No completed runs yet</p>
      ) : (
        <ul className="space-y-2.5">
          {steps.map((s) => (
            <li key={s.stage_id} className="grid grid-cols-[minmax(0,1fr)_24%_3.5rem] items-center gap-3 text-[13px]">
              <span className="min-w-0 break-words text-gray-700">
                {s.stage_id} {s.step_name}
              </span>
              <span className="h-1.5 rounded-full bg-sand-100 overflow-hidden" aria-hidden="true">
                <span
                  className="block h-full rounded-full bg-[#8A7A58]"
                  style={{ width: `${Math.max(3, (s.avg_seconds / max) * 100)}%` }}
                />
              </span>
              <span className="text-right text-gray-500 tabular-nums">{formatDuration(Math.round(s.avg_seconds))}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function RecentFeedback({ onOpenFeedback }: { onOpenFeedback: () => void }) {
  const [rows, setRows] = useState<FeedbackData[] | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await exportFeedbackCsv()
        const parsed = res.ok ? parseFeedbackCsv(await res.text()) : []
        parsed.sort((a, b) => b.submitted_at.localeCompare(a.submitted_at))
        if (!cancelled) setRows(parsed.slice(0, MAX_FEEDBACK_ROWS))
      } catch (err) {
        console.error('Failed to load recent feedback:', err)
        if (!cancelled) setRows([])
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <section className={`${CARD} p-5 min-w-0`} aria-labelledby="recent-feedback-heading">
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <h2 id="recent-feedback-heading" className="text-[15px] font-semibold text-gray-900">
          Recent feedback
        </h2>
        <button
          type="button"
          onClick={onOpenFeedback}
          className="text-[13px] text-primary-600 hover:text-primary-700 focus-visible:outline-none focus-visible:underline"
        >
          All responses
        </button>
      </div>
      {rows === null ? (
        <Loader2 className="h-5 w-5 animate-spin text-gray-400" aria-label="Loading" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-gray-500">No feedback yet</p>
      ) : (
        <ul>
          {rows.map((f) => (
            <li key={f.id} className="py-3 border-b border-sand-200 last:border-b-0 first:pt-1">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900 [overflow-wrap:anywhere]">{f.user_email || 'Anonymous'}</p>
                <p className="mt-0.5 text-xs text-gray-500">
                  Run {f.run_id} · {formatDate(f.submitted_at)}
                </p>
              </div>
              {f.biggest_issue && (
                <p className="mt-1 text-[13px] text-gray-700 break-words">&ldquo;{f.biggest_issue}&rdquo;</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default function AdminOverview({
  stats,
  onOpenFeedback,
}: {
  stats: Stats | null
  onOpenFeedback: () => void
}) {
  const showCost = useCanSeeCost()
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile label="Total runs" value={stats?.total_runs.toLocaleString() ?? '0'} sublabel="All time" />
        <StatTile label="Active users" value={stats?.active_users.toLocaleString() ?? '0'} sublabel="Last 30 days" />
        {showCost && (
          <StatTile label="Total cost" value={stats ? formatCost(stats.total_cost) : '$0.00'} sublabel="All time" />
        )}
        <StatTile
          label="Feedback rate"
          value={`${stats?.feedback_rate.toFixed(1) ?? '0.0'}%`}
          sublabel="of completed runs"
        />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 items-start">
        <WhoSubmitsCard split={stats?.submissions} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 items-start">
        <NeedsAttention p95={stats?.p95_duration_seconds} />
        <StepTimes steps={stats?.step_avg_seconds ?? []} />
        <RecentFeedback onOpenFeedback={onOpenFeedback} />
      </div>
    </div>
  )
}
