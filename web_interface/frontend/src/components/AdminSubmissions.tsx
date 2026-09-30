import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Loader2,
  Clock,
  FileText,
  MessageSquare,
  ChevronDown,
  ChevronUp,
  Search,
  AlertCircle,
} from 'lucide-react'
import type { AdminRun } from '../types'
import { getAdminRuns, computeRunScore } from '../api/admin'
import { formatDate, formatDuration, formatCost } from '../utils'
import StatusIcon from './shared/StatusIcon'

type SortField = 'started_at' | 'total_cost' | 'duration_seconds' | 'filename' | 'status'
type SortDir = 'asc' | 'desc'

function bandClasses(band: string | null | undefined): string {
  if (!band) return 'bg-gray-100 text-gray-600'
  if (band.startsWith('GREEN')) return 'bg-green-100 text-green-700'
  if (band.startsWith('YELLOW')) return 'bg-amber-100 text-amber-700'
  return 'bg-red-100 text-red-700'
}

// The score the runs listing already carries (cached at run end), or null.
function cachedScoreView(run: AdminRun): ScoreView | null {
  if (run.quality_score == null) return null
  return {
    score: run.quality_score,
    band: run.quality_band ?? '',
    dataComplete: run.quality_data_complete,
    missingEvidence: run.quality_missing_evidence,
  }
}

interface ScoreView {
  score: number
  band: string
  // false when the score was computed with a scored file missing or unreadable (#745)
  dataComplete: boolean | null
  missingEvidence: string[]
}

// Advisory run-quality score (provisional — bands not yet calibrated). Shows the
// cached score as a colored badge, or a "Score" button to compute/backfill. A
// score computed with a file missing or unreadable is marked "incomplete" with
// a dashed border, and its tooltip lists what was missing (#745).
function QualityCell({
  run,
  view,
  onScored,
}: {
  run: AdminRun
  view: ScoreView | null
  onScored: (s: ScoreView) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (view) {
    const incomplete = view.dataComplete === false
    const title = incomplete
      ? `${view.band} — score computed without: ${view.missingEvidence.join('; ') || 'unknown files'}`
      : `${view.band} — provisional/diagnostic quality score`
    return (
      <span
        title={title}
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${bandClasses(view.band)}${
          incomplete ? ' border border-dashed border-current' : ''
        }`}
      >
        {view.score}
        {incomplete && <span className="font-normal">incomplete</span>}
      </span>
    )
  }
  if (run.status !== 'complete') return <span className="text-gray-400 text-xs">--</span>
  return (
    <button
      type="button"
      disabled={busy}
      title={error ?? undefined}
      onClick={async (e) => {
        e.stopPropagation()
        setBusy(true)
        setError(null)
        try {
          const r = await computeRunScore(run.run_id)
          onScored({
            score: r.totalScore,
            band: r.band,
            dataComplete: r.data_complete,
            missingEvidence: r.missing_evidence,
          })
        } catch (err) {
          console.error(`Failed to score run ${run.run_id}:`, err)
          setError(err instanceof Error ? err.message : 'Scoring failed')
        } finally {
          setBusy(false)
        }
      }}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium disabled:opacity-50 ${
        error
          ? 'bg-red-100 text-red-700 hover:bg-red-200'
          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
      }`}
    >
      {busy ? (
        <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" />
      ) : error ? (
        <>
          <AlertCircle className="w-3 h-3" aria-hidden="true" />
          Retry
        </>
      ) : (
        'Score'
      )}
    </button>
  )
}

export default function AdminSubmissions() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState<AdminRun[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [filterUser, setFilterUser] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [sortField, setSortField] = useState<SortField>('started_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  // On-demand scores computed this session, keyed by run_id (overlays fetched data).
  const [computedScores, setComputedScores] = useState<Record<string, ScoreView>>({})

  const fetchRuns = useCallback(async (offset: number, append: boolean) => {
    const params = new URLSearchParams({
      offset: offset.toString(),
      limit: '20',
    })
    if (filterUser) params.set('user', filterUser)
    if (filterStatus) params.set('status', filterStatus)

    try {
      const data = await getAdminRuns(params.toString())
      if (append) {
        setRuns((prev) => [...prev, ...data.runs])
      } else {
        setRuns(data.runs)
      }
      setTotal(data.total)
      setHasMore(data.has_more)
    } catch (err) {
      console.error('Failed to fetch admin runs:', err)
    }
  }, [filterUser, filterStatus])

  useEffect(() => {
    setLoading(true)
    fetchRuns(0, false).finally(() => setLoading(false))
  }, [fetchRuns])

  const handleShowMore = async () => {
    setLoadingMore(true)
    await fetchRuns(runs.length, true)
    setLoadingMore(false)
  }

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDir('desc')
    }
  }

  // Client-side sort (the API returns server-sorted by date, we re-sort locally for other columns)
  const sortedRuns = [...runs].sort((a, b) => {
    let cmp = 0
    switch (sortField) {
      case 'started_at':
        cmp = (a.started_at ?? '').localeCompare(b.started_at ?? '')
        break
      case 'total_cost':
        cmp = a.total_cost - b.total_cost
        break
      case 'duration_seconds':
        cmp = (a.duration_seconds ?? 0) - (b.duration_seconds ?? 0)
        break
      case 'filename':
        cmp = a.filename.localeCompare(b.filename)
        break
      case 'status':
        cmp = a.status.localeCompare(b.status)
        break
    }
    return sortDir === 'asc' ? cmp : -cmp
  })

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null
    return sortDir === 'asc' ? (
      <ChevronUp className="w-3 h-3 inline ml-0.5" aria-hidden="true" />
    ) : (
      <ChevronDown className="w-3 h-3 inline ml-0.5" aria-hidden="true" />
    )
  }

  return (
    <div>
      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" aria-hidden="true" />
          <label className="sr-only" htmlFor="filter-user">Filter by user email</label>
          <input
            id="filter-user"
            type="text"
            placeholder="Filter by user email..."
            value={filterUser}
            onChange={(e) => setFilterUser(e.target.value)}
            className="pl-9 pr-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-1 focus:ring-primary-500 focus:border-primary-500 focus:outline-none w-64"
          />
        </div>
        <label className="sr-only" htmlFor="filter-status">Filter by status</label>
        <select
          id="filter-status"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="text-sm border border-gray-300 rounded-lg px-3 py-2 focus:ring-1 focus:ring-primary-500 focus:border-primary-500 focus:outline-none bg-white"
        >
          {/* Default ("") hides never-started "created" runs (abandoned/declined
              uploads). "all" opts back in to every status; "created" inspects
              just the never-started ones. */}
          <option value="">All except not-started</option>
          <option value="running">Running</option>
          <option value="paused">Paused</option>
          <option value="complete">Complete</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
          <option value="created">Not started</option>
          <option value="all">All (incl. not-started)</option>
        </select>
        <span className="text-sm text-gray-500 self-center ml-auto">
          {total} total runs
        </span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow-sm border border-sand-300 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-sand-200">
              <thead className="bg-sand-50">
                <tr>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Run ID
                  </th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    User
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700"
                    onClick={() => handleSort('filename')}
                  >
                    Filename <SortIcon field="filename" />
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700"
                    onClick={() => handleSort('status')}
                  >
                    Status <SortIcon field="status" />
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700"
                    onClick={() => handleSort('duration_seconds')}
                  >
                    Duration <SortIcon field="duration_seconds" />
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700"
                    onClick={() => handleSort('total_cost')}
                  >
                    Cost <SortIcon field="total_cost" />
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700"
                    onClick={() => handleSort('started_at')}
                  >
                    Date <SortIcon field="started_at" />
                  </th>
                  <th
                    scope="col"
                    title="Advisory run-quality score (0-100). Provisional — bands not yet calibrated."
                    className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider"
                  >
                    Quality
                  </th>
                  <th scope="col" className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Feedback
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-sand-200">
                {sortedRuns.map((run) => (
                  <tr
                    key={run.run_id}
                    onClick={() => navigate(`/run/${run.run_id}`)}
                    className="hover:bg-gray-50 cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-3 whitespace-nowrap text-sm font-mono text-primary-600">
                      {run.run_id}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <div className="text-sm text-gray-900">{run.user_display_name ?? '--'}</div>
                      <div className="text-xs text-gray-500">{run.user_email ?? '--'}</div>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 max-w-[200px] truncate">
                      <span className="inline-flex items-center gap-1">
                        <FileText className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" aria-hidden="true" />
                        {run.filename}
                      </span>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1.5 text-sm">
                        <StatusIcon status={run.status} />
                        <span className="capitalize text-gray-700">{run.status}</span>
                      </span>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-right text-sm text-gray-500">
                      <span className="inline-flex items-center gap-1">
                        <Clock className="w-3 h-3" aria-hidden="true" />
                        {formatDuration(run.duration_seconds)}
                      </span>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-right text-sm text-gray-700">
                      <span className="inline-flex items-center gap-1">
                        {formatCost(run.total_cost, 3)}
                      </span>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                      {formatDate(run.started_at)}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-center">
                      <QualityCell
                        run={run}
                        view={cachedScoreView(run) ?? computedScores[run.run_id] ?? null}
                        onScored={(s) => setComputedScores((prev) => ({ ...prev, [run.run_id]: s }))}
                      />
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-center">
                      {run.status === 'complete' ? (
                        run.has_feedback ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-green-100 text-green-700">
                            <MessageSquare className="w-3 h-3" aria-hidden="true" />
                            Given
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-100 text-amber-700">
                            <MessageSquare className="w-3 h-3" aria-hidden="true" />
                            Pending
                          </span>
                        )
                      ) : (
                        <span className="text-gray-400 text-xs">--</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {runs.length === 0 && (
            <div className="text-center py-12 text-gray-500 text-sm">
              No runs found.
            </div>
          )}

          {hasMore && (
            <div className="border-t border-sand-200 px-4 py-3">
              <button
                onClick={handleShowMore}
                disabled={loadingMore}
                className="w-full py-2 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-50 rounded-lg transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none flex items-center justify-center gap-1.5"
              >
                {loadingMore ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                    Loading...
                  </>
                ) : (
                  <>
                    <ChevronDown className="w-3.5 h-3.5" aria-hidden="true" />
                    Show more ({total - runs.length} remaining)
                  </>
                )}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
