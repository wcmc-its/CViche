import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, FileText, CheckCircle2, Loader2, XCircle, AlertCircle, ChevronDown, ChevronUp, ChevronLeft, ChevronRight, MessageSquare } from 'lucide-react'
import { formatRelativeDate } from '../utils'
import ErrorBanner from './ErrorBanner'

interface RunSummary {
  run_id: string
  filename: string
  status: string
  started_at: string
  completed_at: string | null
  total_cost: number
  total_duration_seconds: number | null
}

interface FeedbackStatus {
  run_id: string
  has_feedback: boolean
}

interface RunHistoryProps {
  onSelectRun: (runId: string) => void
}

type SortField = 'status' | 'filename' | 'started_at' | 'total_duration_seconds' | 'total_cost' | 'feedback'
type SortDir = 'asc' | 'desc'

const PAGE_SIZE = 100

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'complete':
      return <CheckCircle2 className="w-4 h-4 text-green-600" aria-hidden="true" />
    case 'running':
      return <Loader2 className="w-4 h-4 text-blue-600 animate-spin" aria-hidden="true" />
    case 'failed':
      return <XCircle className="w-4 h-4 text-red-600" aria-hidden="true" />
    case 'cancelled':
      return <AlertCircle className="w-4 h-4 text-orange-600" aria-hidden="true" />
    default:
      return <Clock className="w-4 h-4 text-gray-400" aria-hidden="true" />
  }
}

function statusLabelColor(status: string): string {
  switch (status) {
    case 'complete':
      return 'text-green-600'
    case 'running':
      return 'text-blue-600'
    case 'failed':
      return 'text-red-600'
    case 'cancelled':
      return 'text-orange-600'
    default:
      return 'text-gray-500'
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case 'complete':
      return 'Complete'
    case 'running':
      return 'Running'
    case 'failed':
      return 'Failed'
    case 'cancelled':
      return 'Cancelled'
    default:
      return 'Pending'
  }
}

function getPageNumbers(currentPage: number, totalPages: number): (number | 'ellipsis')[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i)
  }

  const pages: (number | 'ellipsis')[] = []
  const first = 0
  const last = totalPages - 1

  pages.push(first)

  if (currentPage > 2) {
    pages.push('ellipsis')
  }

  for (let i = Math.max(1, currentPage - 1); i <= Math.min(last - 1, currentPage + 1); i++) {
    if (!pages.includes(i)) {
      pages.push(i)
    }
  }

  if (currentPage < last - 2) {
    pages.push('ellipsis')
  }

  if (!pages.includes(last)) {
    pages.push(last)
  }

  return pages
}

export default function RunHistory({ onSelectRun }: RunHistoryProps) {
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [feedbackMap, setFeedbackMap] = useState<Record<string, boolean>>({})
  const [currentPage, setCurrentPage] = useState(0)
  const [sortField, setSortField] = useState<SortField>('started_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [error, setError] = useState<string | null>(null)
  const containerRef = useRef<HTMLElement>(null)

  const fetchFeedbackStatus = async () => {
    try {
      const res = await fetch('/api/runs/feedback-status')
      if (res.ok) {
        const data: FeedbackStatus[] = await res.json()
        const map: Record<string, boolean> = {}
        data.forEach((item) => { map[item.run_id] = item.has_feedback })
        setFeedbackMap(map)
      }
    } catch (err) {
      console.error('Error fetching feedback status:', err)
    }
  }

  const fetchRuns = useCallback(async (offset: number) => {
    try {
      setError(null)
      const res = await fetch(`/api/runs?offset=${offset}&limit=${PAGE_SIZE}`)
      if (res.ok) {
        const data = await res.json()
        // Handle both paginated response { runs, total, has_more } and legacy array response
        const runsList = Array.isArray(data) ? data : (data.runs || [])
        setRuns(runsList)
        setTotal(data.total || runsList.length)
      } else {
        setError('Unable to load run history. Please refresh the page to try again.')
      }
    } catch (err) {
      console.error('Error fetching runs:', err)
      setError('Unable to load run history. Please refresh the page to try again.')
    }
  }, [])

  useEffect(() => {
    const load = async () => {
      await Promise.all([fetchRuns(0), fetchFeedbackStatus()])
      setLoading(false)
    }
    load()
  }, [fetchRuns])

  useEffect(() => {
    if (currentPage === 0) return
    fetchRuns(currentPage * PAGE_SIZE).then(() => {
      containerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [currentPage, fetchRuns])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDir(field === 'started_at' ? 'desc' : 'asc')
    }
    setCurrentPage(0)
  }

  const sortedRuns = [...runs].sort((a, b) => {
    let cmp = 0
    switch (sortField) {
      case 'status':
        cmp = a.status.localeCompare(b.status)
        break
      case 'filename':
        cmp = a.filename.localeCompare(b.filename)
        break
      case 'started_at':
        cmp = (a.started_at ?? '').localeCompare(b.started_at ?? '')
        break
      case 'total_duration_seconds':
        cmp = (a.total_duration_seconds ?? 0) - (b.total_duration_seconds ?? 0)
        break
      case 'total_cost':
        cmp = a.total_cost - b.total_cost
        break
      case 'feedback': {
        const valA = feedbackMap[a.run_id] === true ? 2 : (feedbackMap[a.run_id] === false ? 1 : 0)
        const valB = feedbackMap[b.run_id] === true ? 2 : (feedbackMap[b.run_id] === false ? 1 : 0)
        cmp = valA - valB
        break
      }
    }
    return sortDir === 'asc' ? cmp : -cmp
  })

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const startIndex = currentPage * PAGE_SIZE
  const endIndex = Math.min(startIndex + PAGE_SIZE, total)

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return '\u2014'
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    if (mins > 0) return `${mins}m ${secs}s`
    return `${secs}s`
  }

  const getAriaSortValue = (field: SortField): 'ascending' | 'descending' | 'none' => {
    if (sortField !== field) return 'none'
    return sortDir === 'asc' ? 'ascending' : 'descending'
  }

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField === field) {
      return sortDir === 'asc'
        ? <ChevronUp className="w-3 h-3 text-primary-600" aria-hidden="true" />
        : <ChevronDown className="w-3 h-3 text-primary-600" aria-hidden="true" />
    }
    return <ChevronDown className="w-3 h-3 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden="true" />
  }

  if (loading) {
    return (
      <div className="text-center py-4 text-sm text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin inline mr-2" aria-hidden="true" />
        Loading history...
      </div>
    )
  }

  if (runs.length === 0 && !error) {
    return (
      <section ref={containerRef} aria-label="Previous runs" className="mt-6 bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">Previous Runs</h2>
        <div className="flex flex-col items-center justify-center py-12">
          <FileText className="w-12 h-12 text-gray-300 mb-3" aria-hidden="true" />
          <p className="text-sm font-semibold text-gray-900">No runs yet</p>
          <p className="text-sm text-gray-500 text-center max-w-[280px] mt-1">Upload a CV above to start your first pipeline run.</p>
        </div>
      </section>
    )
  }

  return (
    <section ref={containerRef} aria-label="Previous runs" className="mt-6 bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Previous Runs</h2>

      {error && (
        <div className="mb-4">
          <ErrorBanner message={error} onDismiss={() => { setError(null); fetchRuns(currentPage * PAGE_SIZE) }} />
        </div>
      )}

      {runs.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="min-w-[640px] w-full">
              <thead className="sticky top-0 z-header bg-white border-b-2 border-gray-200">
                <tr>
                  <th className="px-3 py-3 text-left min-w-[120px]" aria-sort={getAriaSortValue('status')}>
                    <button
                      type="button"
                      onClick={() => handleSort('status')}
                      className="group flex items-center gap-1 text-xs font-normal text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700 w-full"
                    >
                      Status
                      <SortIcon field="status" />
                    </button>
                  </th>
                  <th className="px-3 py-3 text-left" aria-sort={getAriaSortValue('filename')}>
                    <button
                      type="button"
                      onClick={() => handleSort('filename')}
                      className="group flex items-center gap-1 text-xs font-normal text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700 w-full"
                    >
                      File
                      <SortIcon field="filename" />
                    </button>
                  </th>
                  <th className="px-3 py-3 text-left min-w-[170px]" aria-sort={getAriaSortValue('started_at')}>
                    <button
                      type="button"
                      onClick={() => handleSort('started_at')}
                      className="group flex items-center gap-1 text-xs font-normal text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700 w-full"
                    >
                      Date
                      <SortIcon field="started_at" />
                    </button>
                  </th>
                  <th className="px-3 py-3 text-right min-w-[88px]" aria-sort={getAriaSortValue('total_duration_seconds')}>
                    <button
                      type="button"
                      onClick={() => handleSort('total_duration_seconds')}
                      className="group flex items-center gap-1 justify-end text-xs font-normal text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700 w-full"
                    >
                      Duration
                      <SortIcon field="total_duration_seconds" />
                    </button>
                  </th>
                  <th className="px-3 py-3 text-right min-w-[72px]" aria-sort={getAriaSortValue('total_cost')}>
                    <button
                      type="button"
                      onClick={() => handleSort('total_cost')}
                      className="group flex items-center gap-1 justify-end text-xs font-normal text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700 w-full"
                    >
                      Cost
                      <SortIcon field="total_cost" />
                    </button>
                  </th>
                  <th className="px-3 py-3 text-center min-w-[140px]" aria-sort={getAriaSortValue('feedback')}>
                    <button
                      type="button"
                      onClick={() => handleSort('feedback')}
                      className="group flex items-center gap-1 justify-center text-xs font-normal text-gray-500 uppercase tracking-wider cursor-pointer select-none hover:text-gray-700 w-full"
                    >
                      Feedback
                      <SortIcon field="feedback" />
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedRuns.map((run, index) => (
                  <tr
                    key={run.run_id}
                    onClick={() => onSelectRun(run.run_id)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectRun(run.run_id) } }}
                    tabIndex={0}
                    role="link"
                    className={`
                      cursor-pointer transition-colors border-b border-gray-100
                      ${run.status === 'running' ? 'bg-primary-50' : index % 2 === 0 ? 'bg-white' : 'bg-gray-50'}
                      hover:bg-gray-100
                    `}
                  >
                    <td className="px-3 py-3 text-left">
                      <span className="inline-flex items-center gap-1">
                        <StatusIcon status={run.status} />
                        <span className={`text-sm ${statusLabelColor(run.status)}`}>{statusLabel(run.status)}</span>
                      </span>
                    </td>
                    <td className="px-3 py-3 text-left text-sm text-gray-900">
                      {run.filename}
                    </td>
                    <td className="px-3 py-3 text-left text-sm text-gray-500">
                      {(() => {
                        const { display, tooltip } = formatRelativeDate(run.started_at)
                        return <span title={tooltip}>{display}</span>
                      })()}
                    </td>
                    <td className="px-3 py-3 text-right text-sm text-gray-500">
                      {formatDuration(run.total_duration_seconds)}
                    </td>
                    <td className="px-3 py-3 text-right text-sm text-gray-700">
                      {run.total_cost > 0 ? `$${run.total_cost.toFixed(2)}` : '\u2014'}
                    </td>
                    <td className="px-3 py-3 text-center">
                      {run.status === 'complete' && feedbackMap[run.run_id] === true && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-[11px] font-medium">
                          <MessageSquare className="w-3 h-3" aria-hidden="true" />
                          Feedback given
                        </span>
                      )}
                      {run.status === 'complete' && feedbackMap[run.run_id] === false && (
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); navigate(`/run/${run.run_id}#feedback`) }}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 text-[11px] font-medium hover:bg-amber-200 transition-colors cursor-pointer"
                          title="Go to feedback form"
                        >
                          <MessageSquare className="w-3 h-3" aria-hidden="true" />
                          Needs feedback
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-4 pt-4 border-t border-gray-100">
              <span className="text-sm text-gray-500">
                Showing {startIndex + 1}{'\u2013'}{endIndex} of {total} runs
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setCurrentPage(p => Math.max(0, p - 1))}
                  disabled={currentPage === 0}
                  aria-label="Previous page"
                  className={`p-1 rounded-md ${currentPage === 0 ? 'text-gray-300 cursor-not-allowed' : 'text-gray-600 hover:bg-gray-100'}`}
                >
                  <ChevronLeft className="w-4 h-4" aria-hidden="true" />
                </button>
                {getPageNumbers(currentPage, totalPages).map((page, i) =>
                  page === 'ellipsis' ? (
                    <span key={`ellipsis-${i}`} className="px-2 text-sm text-gray-400">&hellip;</span>
                  ) : (
                    <button
                      key={page}
                      onClick={() => setCurrentPage(page)}
                      className={`min-w-[32px] h-8 px-3 py-1 rounded-md text-sm ${
                        page === currentPage
                          ? 'bg-primary-600 text-white'
                          : 'text-gray-600 hover:bg-gray-100'
                      }`}
                    >
                      {page + 1}
                    </button>
                  )
                )}
                <button
                  onClick={() => setCurrentPage(p => Math.min(totalPages - 1, p + 1))}
                  disabled={currentPage === totalPages - 1}
                  aria-label="Next page"
                  className={`p-1 rounded-md ${currentPage === totalPages - 1 ? 'text-gray-300 cursor-not-allowed' : 'text-gray-600 hover:bg-gray-100'}`}
                >
                  <ChevronRight className="w-4 h-4" aria-hidden="true" />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}
