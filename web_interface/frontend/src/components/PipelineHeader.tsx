import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { ArrowLeft, Ban, CheckCircle2, Download, Loader2, XCircle } from 'lucide-react'
import { runRoutes } from '../api/routes'
import { getInputFileUrl } from '../api/runs'
import type { ApiError } from '../api/client'
import { formatCost, formatDate, formatDuration, formatTimeLeft } from '../utils'
import { useCanSeeCost } from '../contexts/AuthContext'
import type { RunState } from '../types'
import { groupStepsIntoPhases } from './StepSidebar'

interface HeaderStep {
  step_number: number
  stage_id?: string
  status: string
}

interface PipelineHeaderProps {
  runId: string
  filename: string
  /** Heading text; defaults to the filename. A finished run passes the faculty name. */
  title?: string
  /** When the run was created (ISO); shown once the run is no longer live. */
  runDate?: string | null
  /** Admin only: who ran it. */
  runByName?: string | null
  status: RunState
  steps: HeaderStep[]
  stepProgress: Record<number, { current: number; total: number; message: string }>
  displayProgress: number
  totalCost: number | null
  inputTokens: number
  outputTokens: number
  elapsedSeconds: number
  /** The run's own estimate (estimated_duration_seconds); null/absent hides the time left. */
  estimatedSeconds?: number | null
  isCancelling: boolean
  /** Omitted for a viewer who may not act on the run (staff on another user's
   *  run): the Cancel button is not rendered. */
  onCancel?: () => void
  onBack: () => void
  /** Shows an error message the way the page shows its others (a refused
   *  "Original file" download, e.g. still being scanned for malware). */
  onError: (message: string) => void
  /** Complete runs: shows the "Pipeline details" toggle. */
  detailsOpen?: boolean
  onToggleDetails?: () => void
  /** Rendered at the bottom of the card (the download panel for a finished run). */
  children?: ReactNode
}

const formatTotalTime = (seconds: number) => {
  const hours = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours > 0) return `${hours}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

const PILL_BASE = 'inline-flex items-center gap-1.5 rounded-full px-2.5 py-[3px] text-[13px] font-medium'

/** The plain gray pill for a state with no icon of its own. */
function NeutralPill({ label }: { label: string }) {
  return <span className={`${PILL_BASE} bg-gray-100 text-gray-700 capitalize`}>{label}</span>
}

/** One case per RunState: adding a state to the union without a case here is
 *  a compile error (the `never` check), not a silently gray pill (#298). */
function StatusPill({ status }: { status: RunState }) {
  switch (status) {
    case 'running':
      return (
        <span className={`${PILL_BASE} bg-primary-50 text-primary-700`}>
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Running
        </span>
      )
    case 'complete':
      return (
        <span className={`${PILL_BASE} bg-[#ECFDF3] text-success-700`}>
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
          Complete
        </span>
      )
    case 'failed':
      return (
        <span className={`${PILL_BASE} bg-error-50 text-error-700`}>
          <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
          Failed
        </span>
      )
    case 'cancelled':
      return (
        <span className={`${PILL_BASE} bg-orange-50 text-orange-800`}>
          <Ban className="h-3.5 w-3.5" aria-hidden="true" />
          Cancelled
        </span>
      )
    case 'created':
    case 'queued':
    case 'paused':
      return <NeutralPill label={status} />
    default: {
      // The API is not validated at runtime: a value outside the union still
      // renders, as the gray pill, instead of crashing the run page.
      const unhandled: never = status
      return <NeutralPill label={String(unhandled)} />
    }
  }
}

export default function PipelineHeader({
  runId,
  filename,
  title,
  runDate,
  runByName,
  status,
  steps,
  stepProgress,
  displayProgress,
  totalCost,
  inputTokens,
  outputTokens,
  elapsedSeconds,
  estimatedSeconds,
  isCancelling,
  onCancel,
  onBack,
  onError,
  detailsOpen,
  onToggleDetails,
  children,
}: PipelineHeaderProps) {
  const showCost = useCanSeeCost()
  const isRunning = status === 'running'
  const timeLeft = isRunning ? formatTimeLeft(estimatedSeconds, elapsedSeconds) : null

  // The API gives elapsed time but no start timestamp, so the start is derived
  // once from the first elapsed reading of a live run.
  const [startedAt, setStartedAt] = useState<number | null>(null)
  useEffect(() => {
    setStartedAt(null)
  }, [runId])
  useEffect(() => {
    if (isRunning && startedAt === null && elapsedSeconds > 0) {
      setStartedAt(Date.now() - elapsedSeconds * 1000)
    }
  }, [isRunning, startedAt, elapsedSeconds])

  // A plain <a download> turned a refusal (409 still being scanned, 403 flagged)
  // into a silent failed download, so ask for the URL first (#1333). The S3
  // bucket has no CORS, so the browser navigates to the URL rather than fetching it.
  const [isFetchingOriginal, setIsFetchingOriginal] = useState(false)
  const downloadOriginal = async () => {
    if (isFetchingOriginal) return
    setIsFetchingOriginal(true)
    try {
      const url = await getInputFileUrl(runId)
      window.location.href = url ?? runRoutes.inputFile(runId)
    } catch (err) {
      onError(`Failed to download the original file: ${(err as ApiError).message || 'Unknown error'}`)
    } finally { setIsFetchingOriginal(false) }
  }

  const phases = groupStepsIntoPhases(steps)
  const bars = phases.map((phase) => {
    const total = phase.steps.length
    const done = phase.steps.filter((s) => s.status === 'complete').length
    const running = phase.steps.find((s) => s.status === 'running')
    const failed = phase.steps.some((s) => s.status === 'error')
    let runningFrac = 0
    if (running) {
      const prog = stepProgress[running.step_number]
      runningFrac = prog && prog.total > 0 ? prog.current / prog.total : 0.5
    }
    const fill = failed ? 1 : Math.min(1, (done + runningFrac) / total)
    return {
      name: phase.name,
      total,
      state: failed ? 'error' : done === total ? 'done' : running ? 'active' : 'pending',
      fill,
    }
  })

  const tokenStat = (label: string, value: number) => (
    <div className="hidden lg:flex flex-col">
      <span>{label}</span>
      <span className="text-gray-900 font-semibold text-[15px] tabular-nums">{value.toLocaleString()}</span>
    </div>
  )

  return (
    <>
      <button
        onClick={onBack}
        aria-label="Back to runs"
        className="self-start inline-flex items-center gap-1.5 rounded text-[13px] text-gray-600 hover:text-gray-900 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
        Runs
      </button>

      <section className="flex flex-col gap-[18px] bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] px-4 py-5 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="m-0 min-w-0 [overflow-wrap:anywhere] text-[22px] font-semibold text-gray-900">{title ?? filename}</h1>
              <StatusPill status={status} />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[13px] text-gray-500">
              <button
                type="button"
                onClick={downloadOriginal}
                disabled={isFetchingOriginal}
                title="Download the original uploaded CV"
                className="group inline-flex items-center gap-1.5 text-gray-700 hover:text-primary-600 hover:underline"
              >
                Original file
                <Download className="h-3.5 w-3.5 shrink-0 opacity-60 group-hover:opacity-100" aria-hidden="true" />
              </button>
              {title && title !== filename && <span className="min-w-0 [overflow-wrap:anywhere]">{filename}</span>}
              <span>Run {runId}</span>
              {isRunning && startedAt !== null && (
                <span>Started {new Date(startedAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span>
              )}
              {!isRunning && runDate && <span>{formatDate(runDate)}</span>}
              {!isRunning && elapsedSeconds > 0 && <span>{formatDuration(elapsedSeconds)}</span>}
              {runByName && <span className="min-w-0 [overflow-wrap:anywhere]">Run by {runByName}</span>}
              {!isRunning && showCost && totalCost !== null && <span>{formatCost(totalCost, 2)}</span>}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-x-5 gap-y-3 text-[13px] text-gray-500">
            {isRunning && (
              <>
                <div className="flex flex-col">
                  <span>Elapsed</span>
                  <span className="text-gray-900 font-semibold text-[15px] tabular-nums">{formatTotalTime(elapsedSeconds)}</span>
                </div>
                {timeLeft && (
                  <div className="flex flex-col">
                    <span>Estimate</span>
                    <span className="text-gray-900 font-semibold text-[15px] tabular-nums">{timeLeft}</span>
                  </div>
                )}
                {showCost && (
                  <div className="flex flex-col">
                    <span>Cost so far</span>
                    <span className="text-gray-900 font-semibold text-[15px] tabular-nums">{formatCost(totalCost, 3)}</span>
                  </div>
                )}
              </>
            )}
            {tokenStat('Tokens in', inputTokens)}
            {tokenStat('Tokens out', outputTokens)}
            {onCancel && (isRunning || status === 'queued') && (
              <button
                onClick={onCancel}
                disabled={isCancelling}
                aria-label="Cancel pipeline run"
                className={`rounded-lg border px-3.5 py-2 text-[13px] font-medium transition-colors focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none ${
                  isCancelling
                    ? 'border-gray-200 bg-gray-100 text-gray-500 cursor-not-allowed'
                    : 'border-red-300 text-red-700 hover:bg-red-50'
                }`}
              >
                {isCancelling ? 'Cancelling...' : 'Cancel run'}
              </button>
            )}
            {onToggleDetails && (
              <button
                onClick={onToggleDetails}
                aria-expanded={detailsOpen}
                className="rounded-lg border border-sand-400 px-3.5 py-2 text-[13px] font-medium text-gray-900 hover:bg-sand-50 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none"
              >
                Pipeline details
              </button>
            )}
          </div>
        </div>

        {bars.length > 0 && (status === 'running' || status === 'failed' || status === 'cancelled') && (
          <div className="flex flex-col gap-2">
            <div
              className="flex gap-1 h-2.5"
              role="progressbar"
              aria-label="Pipeline progress"
              aria-valuenow={displayProgress}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              {bars.map((bar) => (
                <div
                  key={bar.name}
                  style={{ flex: bar.total }}
                  className="relative overflow-hidden rounded-[3px] bg-[#E8E1D2]"
                >
                  <div
                    className={`absolute inset-y-0 left-0 transition-all duration-300 ${
                      bar.state === 'error'
                        ? 'bg-red-500'
                        : bar.state === 'active'
                          ? 'bg-primary-600 bg-stripe-animation'
                          : 'bg-primary-600'
                    }`}
                    style={{ width: `${bar.fill * 100}%` }}
                  />
                </div>
              ))}
            </div>
            <div className="flex gap-1">
              {bars.map((bar) => (
                <div
                  key={bar.name}
                  style={{ flex: bar.total }}
                  className={`min-w-0 truncate text-xs ${
                    bar.state === 'active' ? 'font-semibold text-primary-600' : 'text-gray-500'
                  }`}
                >
                  {bar.name}
                </div>
              ))}
            </div>
          </div>
        )}
        {isRunning && (
          <p className="m-0 text-[13px] text-gray-500">
            You can close this page. Processing continues, and the result will appear in Runs.
          </p>
        )}

        {children}
      </section>
    </>
  )
}
