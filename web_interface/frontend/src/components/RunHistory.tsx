import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, DollarSign, FileText, CheckCircle2, Loader2, XCircle, AlertCircle, ChevronDown, MessageSquare } from 'lucide-react'

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

export default function RunHistory({ onSelectRun }: RunHistoryProps) {
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [total, setTotal] = useState(0)
  const [feedbackMap, setFeedbackMap] = useState<Record<string, boolean>>({})

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

  const fetchRuns = async (offset: number, append: boolean) => {
    try {
      const res = await fetch(`/api/runs?offset=${offset}&limit=20`)
      if (res.ok) {
        const data = await res.json()
        // Handle both paginated response { runs, total, has_more } and legacy array response
        const runsList = Array.isArray(data) ? data : (data.runs || [])
        if (append) {
          setRuns(prev => [...prev, ...runsList])
        } else {
          setRuns(runsList)
        }
        setHasMore(data.has_more || false)
        setTotal(data.total || runsList.length)
      }
    } catch (err) {
      console.error('Error fetching runs:', err)
    }
  }

  useEffect(() => {
    const load = async () => {
      await Promise.all([fetchRuns(0, false), fetchFeedbackStatus()])
      setLoading(false)
    }
    load()
  }, [])

  const handleShowMore = async () => {
    setLoadingMore(true)
    await fetchRuns(runs.length, true)
    setLoadingMore(false)
  }

  if (loading) {
    return (
      <div className="text-center py-4 text-sm text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin inline mr-2" aria-hidden="true" />
        Loading history...
      </div>
    )
  }

  if (runs.length === 0) {
    return null
  }

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  }

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return '—'
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    if (mins > 0) return `${mins}m ${secs}s`
    return `${secs}s`
  }

  return (
    <section aria-label="Previous runs" className="mt-6 bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Previous Runs</h2>
      <div className="space-y-2">
        {runs.map((run) => (
          <button
            key={run.run_id}
            onClick={() => onSelectRun(run.run_id)}
            className="w-full text-left bg-white border border-gray-200 rounded-lg px-4 py-3 hover:bg-gray-50 transition-colors cursor-pointer focus:ring-2 focus:ring-blue-500 focus:outline-none"
            style={{ touchAction: 'manipulation' }}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <StatusIcon status={run.status} />
                <div className="min-w-0">
                  <div className="text-sm font-medium text-gray-900 truncate flex items-center gap-1.5">
                    <FileText className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" aria-hidden="true" />
                    {run.filename}
                  </div>
                  <div className="text-xs text-gray-500">
                    {formatDate(run.started_at)}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-3 text-xs text-gray-500 shrink-0">
                <span className="flex items-center gap-1">
                  <Clock className="w-3 h-3" aria-hidden="true" />
                  {formatDuration(run.total_duration_seconds)}
                </span>
                <span className="flex items-center gap-1">
                  <DollarSign className="w-3 h-3" aria-hidden="true" />
                  {run.total_cost.toFixed(3)}
                </span>
                {run.status === 'complete' && feedbackMap[run.run_id] !== undefined && (
                  feedbackMap[run.run_id] ? (
                    <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-[11px] font-medium">
                      <MessageSquare className="w-3 h-3" aria-hidden="true" />
                      Feedback given
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        navigate(`/run/${run.run_id}#feedback`)
                      }}
                      className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 text-[11px] font-medium hover:bg-amber-200 transition-colors cursor-pointer"
                      title="Go to feedback form"
                    >
                      <MessageSquare className="w-3 h-3" aria-hidden="true" />
                      Needs feedback
                    </button>
                  )
                )}
              </div>
            </div>
          </button>
        ))}
      </div>

      {hasMore && (
        <button
          onClick={handleShowMore}
          disabled={loadingMore}
          className="w-full mt-3 py-2 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-50 rounded-lg transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none flex items-center justify-center gap-1.5"
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
      )}
    </section>
  )
}
