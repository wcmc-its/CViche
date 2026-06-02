import { useState, useEffect, useRef, useCallback } from 'react'
import { MapPin, XCircle, AlertCircle, Clock } from 'lucide-react'
import type { RunStatus } from '../types'
import { getRunStatus, getRunStep, getPromptLogs, getRunDataJson, cancelRun, restartRun, retryStep } from '../api/runs'
import { getWebSocketUrl } from '../api/websocket'
import { formatCost } from '../utils'
import PipelineHeader from './PipelineHeader'
import StepSidebar from './StepSidebar'
import LogViewer from './LogViewer'
import PromptLogViewer from './PromptLogViewer'
import OutputFiles from './OutputFiles'
import JsonViewerModal from './JsonViewerModal'
import CancelConfirmModal from './CancelConfirmModal'
import ErrorBanner from './ErrorBanner'
import FeedbackForm from './FeedbackForm'

interface PipelineViewerProps {
  runId: string
  onBack: () => void
  onNavigateToRun?: (runId: string) => void
}

// Stage descriptions for the UI
const STAGE_DESCRIPTIONS: Record<string, string> = {
  '1a': 'Uses GPT to analyze the CV structure and identify all section headers (Education, Publications, Grants, etc.) and their hierarchical relationships.',
  '1b': 'Creates mappings between extracted headers and Word document paragraph indices without using LLM.',
  '2': 'Identifies individual entries within each section (publications, positions, grants) achieving complete document coverage.',
  '3a': 'Uses LLM to map CV section headers to WCM taxonomy codes (e.g., S1 for Peer-Reviewed Articles, B1 for Education).',
  '3b': 'Classifies individual entries to taxonomy codes using header context and entry content, with post-classification correction.',
  '4': 'Extracts structured fields from entries: authors, titles, journals, DOIs, grant numbers, institutions, dates. Also infers CV owner location from employment/education history for geographic scope classification.',
  '4.5': 'Generates a biosketch-style M1 research summary statement analyzing your CV content.',
  '5': 'Enriches publications with PubMed metadata: full author lists, MeSH terms, publication types, abstracts, PMCIDs.',
  '5b': 'Adds institution location data (city, state, country) via ROR API for education, training, and position entries.',
  '5c': 'Reformats teaching entries (K-codes) into a consistent, readable format.',
  '5d': 'Reformats non-enriched citations to Vancouver bibliographic format.',
  '6': 'Generates the final WCM-formatted Word document with all sections filled by taxonomy code. Routes presentations and service activities to Regional/National/International tables based on inferred CV owner location.',
}

// Step weights for progress calculation (must match step_registry.py)
const STEP_WEIGHTS: Record<string, { weight: number; estimated_seconds: number }> = {
  '1a': { weight: 15, estimated_seconds: 70 },
  '1b': { weight: 2, estimated_seconds: 10 },
  '2': { weight: 3, estimated_seconds: 15 },
  '3a': { weight: 10, estimated_seconds: 50 },
  '3b': { weight: 12, estimated_seconds: 55 },
  '4': { weight: 20, estimated_seconds: 100 },
  '4.5': { weight: 8, estimated_seconds: 40 },
  '5': { weight: 10, estimated_seconds: 50 },
  '5b': { weight: 5, estimated_seconds: 25 },
  '5c': { weight: 5, estimated_seconds: 25 },
  '5d': { weight: 7, estimated_seconds: 35 },
  '6': { weight: 3, estimated_seconds: 15 },
}
const TOTAL_WEIGHT = Object.values(STEP_WEIGHTS).reduce((sum, s) => sum + s.weight, 0)

export default function PipelineViewer({ runId, onBack, onNavigateToRun }: PipelineViewerProps) {
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null)
  const [currentStep, setCurrentStep] = useState(1)
  const [logs, setLogs] = useState<Record<number, string[]>>({})
  const [jsonViewerOpen, setJsonViewerOpen] = useState(false)
  const [jsonContent, setJsonContent] = useState<any>(null)
  const [jsonFilename, setJsonFilename] = useState('')
  const [stepProgress, setStepProgress] = useState<Record<number, { current: number; total: number; message: string }>>({})
  const [showPromptLogs, setShowPromptLogs] = useState(false)
  const [promptLogs, setPromptLogs] = useState<{filename: string, content: string}[]>([])
  const [promptLogsMessage, setPromptLogsMessage] = useState<string | null>(null)
  const [selectedPromptLog, setSelectedPromptLog] = useState<string | null>(null)
  const [localElapsedSeconds, setLocalElapsedSeconds] = useState(0)
  const [displayProgress, setDisplayProgress] = useState(0)
  const [stepStartTimes, setStepStartTimes] = useState<Record<number, number>>({})
  const [stepStartCosts, setStepStartCosts] = useState<Record<number, number>>({})
  const [isCancelling, setIsCancelling] = useState(false)
  const [showCancelConfirm, setShowCancelConfirm] = useState(false)
  const [isRestarting, setIsRestarting] = useState(false)
  const [isRetrying, setIsRetrying] = useState(false)

  // Auto-scroll to feedback section when URL has #feedback hash
  useEffect(() => {
    if (window.location.hash === '#feedback' && runStatus?.status === 'complete') {
      // Wait for FeedbackForm to mount and render
      const timer = setTimeout(() => {
        const el = document.getElementById('feedback-section')
        if (el) {
          el.scrollIntoView({ behavior: 'smooth' })
        }
      }, 500)
      return () => clearTimeout(timer)
    }
  }, [runStatus?.status])
  const [apiError, setApiError] = useState<string | null>(null)
  // Track which run we've already attempted a CV-insights load for, so the 2s
  // status poll (a fresh steps array each tick) can't re-fire the request on
  // every poll — especially after a failure, where cvInsights stays null.
  const cvInsightsAttemptedRef = useRef<string | null>(null)
  const [cvInsights, setCvInsights] = useState<{
    cv_owner?: { full_name?: string; last_name?: string };
    cv_owner_location?: {
      metro_area?: string;
      primary_location?: { city?: string; state?: string; institution?: string };
      inference_success?: boolean;
    };
  } | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const startTimeRef = useRef<number | null>(null)
  const logsEndRef = useRef<HTMLDivElement>(null)

  // Open the in-app confirmation modal that spells out the implications of
  // cancelling (partial output, sunk cost, delayed stop, no undo) before any
  // request is sent. The actual cancellation happens in confirmCancel.
  const handleCancel = () => {
    if (isCancelling) return
    setShowCancelConfirm(true)
  }

  // Proceed with cancellation after the user confirms in the modal. Keeps the
  // isCancelling busy state so both the modal's primary button and the header
  // Cancel button reflect the in-flight request.
  const confirmCancel = async () => {
    if (isCancelling) return

    setIsCancelling(true)
    try {
      await cancelRun(runId)
      setShowCancelConfirm(false)
    } catch (err: any) {
      console.error('Error cancelling run:', err)
      setApiError(`Failed to cancel: ${err.message || 'Unknown error'}`)
      // Leave the modal open on failure so the user can retry or dismiss.
    } finally {
      setIsCancelling(false)
    }
  }

  // Restart the pipeline with the same file
  const handleRestart = async () => {
    if (isRestarting) return
    setIsRestarting(true)
    try {
      const data = await restartRun(runId)
      if (onNavigateToRun) {
        onNavigateToRun(data.new_run_id)
      }
    } catch (err: any) {
      console.error('Error restarting run:', err)
      setApiError(`Failed to restart: ${err.message || 'Unknown error'}`)
    } finally {
      setIsRestarting(false)
    }
  }

  // Resume this run from the failed step (re-runs the failed stage onward,
  // reusing earlier stages' outputs). Cheaper than a full restart.
  const handleRetry = async () => {
    if (isRetrying) return
    const failedStep = runStatus?.steps?.find((s) => s.status === 'error')
    if (!failedStep) return
    setIsRetrying(true)
    try {
      await retryStep(runId, failedStep.step_number)
      // The 2s status poll + WebSocket pick up the run flipping back to
      // "running", which swaps the failed banner for the live progress view.
    } catch (err: any) {
      console.error('Error retrying step:', err)
      setApiError(`Failed to retry: ${err.message || 'Unknown error'}`)
    } finally {
      setIsRetrying(false)
    }
  }

  // Calculate overall progress with weighted steps and time-based interpolation
  const calculateProgress = useCallback(() => {
    if (!runStatus?.steps) return 0

    let completedWeight = 0
    let runningWeight = 0
    let runningProgress = 0

    runStatus.steps.forEach((step) => {
      const stageId = step.stage_id || String(step.step_number)
      const stepInfo = STEP_WEIGHTS[stageId] || { weight: 1, estimated_seconds: 30 }

      if (step.status === 'complete') {
        completedWeight += stepInfo.weight
      } else if (step.status === 'running') {
        runningWeight = stepInfo.weight

        const stepProg = stepProgress[step.step_number]
        if (stepProg && stepProg.total > 0) {
          runningProgress = stepProg.current / stepProg.total
        } else {
          const stepStartTime = stepStartTimes[step.step_number]
          if (stepStartTime) {
            const elapsed = (Date.now() - stepStartTime) / 1000
            runningProgress = Math.min(0.95, elapsed / stepInfo.estimated_seconds)
          } else {
            runningProgress = 0.05
          }
        }
      }
    })

    const totalProgress = (completedWeight + runningWeight * runningProgress) / TOTAL_WEIGHT * 100
    return Math.round(Math.min(100, Math.max(0, totalProgress)))
  }, [runStatus?.steps, stepProgress, stepStartTimes])

  // Fetch initial status
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const data = await getRunStatus(runId)
        setRunStatus(data)
      } catch {
        // Transient failure -- polling will retry
      }
    }
    fetchStatus().catch(() => {
      setApiError('Failed to fetch pipeline status. The server may be unavailable.')
    })
    const interval = setInterval(fetchStatus, 2000)
    return () => clearInterval(interval)
  }, [runId])

  // Fetch logs for current step from API
  useEffect(() => {
    const fetchStepLogs = async () => {
      try {
        const data = await getRunStep(runId, currentStep)
        if (data.logs && data.logs.length > 0) {
          setLogs((prev) => ({
            ...prev,
            [currentStep]: data.logs.map((log: any) => `[${log.time}] ${log.message}`)
          }))
        }
      } catch (err) {
        console.error('Error fetching step logs:', err)
      }
    }

    fetchStepLogs()

    const currentStepInfo = runStatus?.steps?.find(s => s.step_number === currentStep)
    const isRunning = runStatus?.status === 'running' &&
      (currentStepInfo?.status === 'running' || currentStepInfo?.status === 'pending')

    if (isRunning) {
      const interval = setInterval(fetchStepLogs, 1000)
      return () => clearInterval(interval)
    }
  }, [runId, currentStep, runStatus?.status, runStatus?.steps])

  // Fetch prompt logs for current step
  const fetchPromptLogs = useCallback(async () => {
    try {
      const data = await getPromptLogs(runId, currentStep)
      setPromptLogs(data.logs || [])
      setPromptLogsMessage(data.message || null)
      setShowPromptLogs(true)
      if (data.logs && data.logs.length > 0) {
        setSelectedPromptLog(data.logs[0].filename)
      } else {
        setSelectedPromptLog(null)
      }
    } catch (err) {
      console.error('Error fetching prompt logs:', err)
    }
  }, [runId, currentStep])

  // Re-fetch prompt logs when step changes and panel is visible
  useEffect(() => {
    if (showPromptLogs) {
      fetchPromptLogs()
    }
  }, [currentStep, fetchPromptLogs, showPromptLogs])

  // Smooth timer that ticks every second
  useEffect(() => {
    if (runStatus?.status === 'running') {
      if (!startTimeRef.current) {
        if (runStatus.total_duration_seconds) {
          startTimeRef.current = Date.now() - (runStatus.total_duration_seconds * 1000)
        } else {
          startTimeRef.current = Date.now()
        }
        setLocalElapsedSeconds(runStatus.total_duration_seconds || 0)
      }

      const timer = setInterval(() => {
        if (startTimeRef.current) {
          setLocalElapsedSeconds(Math.floor((Date.now() - startTimeRef.current) / 1000))
        }
      }, 1000)
      return () => clearInterval(timer)
    } else if (runStatus?.status === 'complete' || runStatus?.status === 'failed') {
      setLocalElapsedSeconds(runStatus.total_duration_seconds || 0)
      startTimeRef.current = null
    }
  }, [runStatus?.status, runStatus?.total_duration_seconds])

  // Smooth progress animation
  useEffect(() => {
    if (runStatus?.status !== 'running') {
      const progress = calculateProgress()
      if (displayProgress !== progress) {
        setDisplayProgress(progress)
      }
      return
    }

    const interval = setInterval(() => {
      const targetProgress = calculateProgress()
      setDisplayProgress(prev => {
        if (prev < targetProgress) {
          return Math.min(prev + 1, targetProgress)
        } else if (prev > targetProgress) {
          return targetProgress
        }
        return prev
      })
    }, 1000)

    return () => clearInterval(interval)
  }, [runStatus?.status, calculateProgress, displayProgress])

  // Load CV insights when Stage 4 completes
  useEffect(() => {
    const loadCvInsights = async () => {
      const stage4Step = runStatus?.steps.find(s => s.stage_id === '4')
      if (!stage4Step || stage4Step.status !== 'complete') return
      if (cvInsights) return
      // Only attempt once per run, regardless of success/failure.
      if (cvInsightsAttemptedRef.current === runId) return

      let outputFiles: string[] = []
      try {
        outputFiles = stage4Step.output_files ? JSON.parse(stage4Step.output_files) : []
      } catch {
        return
      }

      const fieldsJson = outputFiles.find(f => f.includes('_fields.json'))
      if (!fieldsJson) return

      cvInsightsAttemptedRef.current = runId
      try {
        const data = await getRunDataJson(runId, fieldsJson)
        if (data.content) {
          setCvInsights({
            cv_owner: data.content.cv_owner,
            cv_owner_location: data.content.cv_owner_location
          })
        }
      } catch (err) {
        console.error('Error loading CV insights:', err)
      }
    }

    loadCvInsights()
  }, [runStatus?.steps, runId, cvInsights])

  // Open JSON viewer
  const openJsonViewer = async (filename: string) => {
    try {
      const data = await getRunDataJson(runId, filename)
      setJsonContent(data.content)
      setJsonFilename(filename)
      setJsonViewerOpen(true)
    } catch (err) {
      console.error('Error loading JSON:', err)
      setApiError('Failed to load JSON file.')
    }
  }

  // WebSocket connection
  useEffect(() => {
    const wsUrl = getWebSocketUrl(`/ws/run/${runId}/stream`)
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)

      if (data.event === 'STEP_START') {
        setCurrentStep(data.step)
        setStepStartTimes((prev) => ({ ...prev, [data.step]: Date.now() }))
        setStepStartCosts((prev) => ({ ...prev, [data.step]: data.total_cost || 0 }))
        setStepProgress((prev) => ({ ...prev, [data.step]: { current: 0, total: 0, message: '' } }))
      } else if (data.event === 'LOG') {
        const logMessage = data.timestamp
          ? `[${new Date(data.timestamp).toLocaleTimeString()}] ${data.message}`
          : data.message
        setLogs((prev) => ({
          ...prev,
          [data.step]: [...(prev[data.step] || []), logMessage],
        }))
      } else if (data.event === 'PROGRESS') {
        setStepProgress((prev) => ({
          ...prev,
          [data.step]: { current: data.current, total: data.total, message: data.message || '' }
        }))
      } else if (data.event === 'COST_UPDATE') {
        setRunStatus((prev) => {
          if (!prev) return prev
          return {
            ...prev,
            total_cost: data.total_cost,
            total_tokens: data.total_tokens,
            input_tokens: data.input_tokens || prev.input_tokens,
            output_tokens: data.output_tokens || prev.output_tokens
          }
        })
      }
    }

    ws.onerror = () => {
      console.error('WebSocket connection error')
    }

    return () => {
      ws.close()
    }
  }, [runId])

  // Warn before the tab is closed or navigated away while a run is in progress.
  // The run continues server-side regardless, but the browser prompt reminds the
  // user that leaving won't stop it (and that they can return later). Modern
  // browsers ignore custom text and show their own generic message, so we only
  // need to call preventDefault / set returnValue to trigger the prompt.
  useEffect(() => {
    if (runStatus?.status !== 'running') return

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }

    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [runStatus?.status])

  if (!runStatus) {
    return (
      <main className="flex items-center justify-center min-h-screen">
        <div className="text-gray-600" role="status" aria-live="polite">Loading pipeline data...</div>
      </main>
    )
  }

  const currentStepData = runStatus.steps[currentStep - 1]

  return (
    <div className="min-h-screen bg-surface-muted">
      {/* API Error Banner */}
      {apiError && (
        <ErrorBanner message={apiError} onDismiss={() => setApiError(null)} />
      )}

      {/* Failed Banner */}
      {runStatus.status === 'failed' && (
        <div className="bg-red-50 border-b border-red-300 px-6 py-3" role="alert">
          <div className="flex items-center justify-between max-w-full">
            <div className="flex items-center gap-3">
              <XCircle className="h-5 w-5 text-red-600 flex-shrink-0" aria-hidden="true" />
              <div>
                <p className="text-sm font-medium text-red-800">
                  Pipeline failed
                  {runStatus.error_message && ` — ${runStatus.error_message}`}
                </p>
              </div>
            </div>
            <div className="ml-4 flex shrink-0 gap-2">
              {runStatus.steps?.some((s) => s.status === 'error') && (
                <button
                  onClick={handleRetry}
                  disabled={isRetrying || isRestarting}
                  className="rounded-lg px-4 py-1.5 text-sm font-medium bg-red-600 text-white hover:bg-red-700 transition-colors focus:ring-2 focus:ring-red-500 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isRetrying ? 'Retrying...' : 'Retry failed step'}
                </button>
              )}
              <button
                onClick={handleRestart}
                disabled={isRestarting || isRetrying}
                className="rounded-lg px-4 py-1.5 text-sm font-medium bg-red-100 text-red-800 hover:bg-red-200 transition-colors focus:ring-2 focus:ring-red-500 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isRestarting ? 'Restarting...' : 'Restart with this file'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Cancelled Banner */}
      {runStatus.status === 'cancelled' && (
        <div className="bg-orange-50 border-b border-orange-300 px-6 py-3" role="status">
          <div className="flex items-center justify-between max-w-full">
            <div className="flex items-center gap-3">
              <AlertCircle className="h-5 w-5 text-orange-600 flex-shrink-0" aria-hidden="true" />
              <p className="text-sm font-medium text-orange-800">
                Pipeline was cancelled
                {runStatus.error_message && ` — ${runStatus.error_message}`}
              </p>
            </div>
            <button
              onClick={handleRestart}
              disabled={isRestarting}
              className="ml-4 shrink-0 rounded-lg px-4 py-1.5 text-sm font-medium bg-orange-100 text-orange-800 hover:bg-orange-200 transition-colors focus:ring-2 focus:ring-orange-500 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isRestarting ? 'Restarting...' : 'Restart with this file'}
            </button>
          </div>
        </div>
      )}

      {/* Created (not yet started) Banner */}
      {runStatus.status === 'created' && (
        <div className="bg-gray-50 border-b border-gray-300 px-6 py-3" role="status">
          <div className="flex items-center justify-between max-w-full">
            <div className="flex items-center gap-3">
              <Clock className="h-5 w-5 text-gray-500 flex-shrink-0" aria-hidden="true" />
              <p className="text-sm font-medium text-gray-700">
                Pipeline has not been started yet
              </p>
            </div>
            <button
              onClick={handleRestart}
              disabled={isRestarting}
              className="ml-4 shrink-0 rounded-lg px-4 py-1.5 text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors focus:ring-2 focus:ring-gray-500 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isRestarting ? 'Restarting...' : 'Restart with this file'}
            </button>
          </div>
        </div>
      )}

      {/* Header */}
      <PipelineHeader
        runId={runId}
        filename={runStatus.filename}
        status={runStatus.status}
        totalCost={runStatus.total_cost}
        inputTokens={runStatus.input_tokens}
        outputTokens={runStatus.output_tokens}
        elapsedSeconds={localElapsedSeconds}
        isCancelling={isCancelling}
        onCancel={handleCancel}
        onBack={onBack}
      />

      {/* Progress Bar — always rendered to prevent layout shift, visibility toggled */}
      <div
        className={`bg-white border-b border-gray-200 px-6 py-3 transition-opacity ${
          runStatus.status === 'running' ? 'opacity-100' : 'opacity-0 h-0 py-0 overflow-hidden'
        }`}
        role="progressbar"
        aria-valuenow={displayProgress}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Pipeline progress: ${displayProgress}%`}
      >
        <div className="flex items-center gap-3 max-w-full">
          <span className="text-sm font-medium text-gray-700 min-w-[80px]">Progress:</span>
          <div className="flex-1 bg-gray-200 rounded-full h-6 overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-primary-500 to-primary-600 transition-all duration-100 ease-linear relative overflow-hidden"
              style={{ width: `${displayProgress}%` }}
            >
              <div className="absolute inset-0 bg-stripe-animation" aria-hidden="true"></div>
            </div>
          </div>
          <span className="text-sm font-semibold text-gray-900 min-w-[50px] text-right">{displayProgress}%</span>
        </div>
      </div>

      {/* Run-continues-on-server note — only while running. Reassures the user
          that the run keeps going server-side whether or not this window stays
          open, mirroring the beforeunload prompt above. */}
      {runStatus.status === 'running' && (
        <div className="bg-blue-50 border-b border-blue-200 px-6 py-2" role="note">
          <p className="text-xs text-blue-800 max-w-full">
            This run continues on the server whether or not this window stays open. You can safely
            close the tab and return to the app when it completes.
          </p>
        </div>
      )}

      {/* Main Content */}
      <div className="flex flex-col md:flex-row max-w-full">
        {/* Sidebar */}
        <StepSidebar
          steps={runStatus.steps}
          currentStep={currentStep}
          onSelectStep={setCurrentStep}
          stepStartTimes={stepStartTimes}
          stepStartCosts={stepStartCosts}
          totalCost={runStatus.total_cost}
        />

        {/* Step Detail Panel */}
        <main className="flex-1 p-4 md:p-8" aria-label="Step details">
          {currentStepData && (
            <section className="bg-white rounded-lg shadow p-6">
              <div className="mb-6">
                <h2 className="text-xl md:text-2xl font-bold text-gray-900 mb-2">
                  Stage {currentStepData.stage_id || currentStep}: {currentStepData.step_name}
                </h2>
                {currentStepData.stage_id && STAGE_DESCRIPTIONS[currentStepData.stage_id] && (
                  <p className="text-sm text-gray-600 mb-3 bg-primary-50 p-3 rounded-lg border border-primary-100">
                    {STAGE_DESCRIPTIONS[currentStepData.stage_id]}
                  </p>
                )}
                <div className="flex flex-wrap items-center gap-2 md:gap-4 text-sm text-gray-600">
                  <span>Status: <strong>{currentStepData.status}</strong></span>
                  <span className="hidden md:inline" aria-hidden="true">·</span>
                  <span>Duration: <strong>{
                    currentStepData.status === 'running' && stepStartTimes[currentStep]
                      ? `${Math.floor((Date.now() - stepStartTimes[currentStep]) / 1000)}s`
                      : currentStepData.duration_seconds
                        ? `${currentStepData.duration_seconds}s`
                        : '—'
                  }</strong></span>
                  <span className="hidden md:inline" aria-hidden="true">·</span>
                  <span>Cost: <strong>{
                    currentStepData.status === 'running'
                      ? formatCost((runStatus.total_cost || 0) - (stepStartCosts[currentStep] || 0), 3)
                      : formatCost(currentStepData.cost, 3)
                  }</strong></span>
                </div>

                {/* Within-Step Progress */}
                {currentStepData.status === 'running' && stepProgress[currentStep] && stepProgress[currentStep].total > 0 && (
                  <div className="mt-4 p-3 bg-primary-50 border border-primary-200 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-blue-900">
                        {stepProgress[currentStep].message || 'Processing...'}
                      </span>
                      <span className="text-sm font-semibold text-blue-900">
                        {stepProgress[currentStep].current} / {stepProgress[currentStep].total}
                      </span>
                    </div>
                    <div
                      className="bg-primary-200 rounded-full h-2 overflow-hidden"
                      role="progressbar"
                      aria-valuenow={stepProgress[currentStep].current}
                      aria-valuemax={stepProgress[currentStep].total}
                      aria-label="Step progress"
                    >
                      <div
                        className="h-full bg-primary-600 transition-all duration-300"
                        style={{
                          width: `${(stepProgress[currentStep].current / stepProgress[currentStep].total) * 100}%`
                        }}
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Log/Prompt Tabs */}
              <div className="mb-4 flex gap-2" role="tablist" aria-label="Log view options">
                <button
                  onClick={() => setShowPromptLogs(false)}
                  role="tab"
                  aria-selected={!showPromptLogs}
                  aria-controls="log-panel"
                  className={`px-4 py-2 rounded-lg font-medium transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none ${
                    !showPromptLogs
                      ? 'bg-primary-500 text-white'
                      : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
                  }`}
                >
                  Logs
                </button>
                <button
                  onClick={() => fetchPromptLogs()}
                  role="tab"
                  aria-selected={showPromptLogs}
                  aria-controls="prompt-log-panel"
                  className={`px-4 py-2 rounded-lg font-medium transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none ${
                    showPromptLogs
                      ? 'bg-primary-500 text-white'
                      : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
                  }`}
                >
                  Prompt Logs
                </button>
              </div>

              {/* Log Panels */}
              {!showPromptLogs && (
                <div id="log-panel" role="tabpanel">
                  <LogViewer
                    logs={logs[currentStep] || []}
                    logsEndRef={logsEndRef}
                  />
                </div>
              )}
              {showPromptLogs && (
                <div id="prompt-log-panel" role="tabpanel">
                  <PromptLogViewer
                    promptLogs={promptLogs}
                    promptLogsMessage={promptLogsMessage}
                    selectedPromptLog={selectedPromptLog}
                    onSelectPromptLog={setSelectedPromptLog}
                  />
                </div>
              )}

              {/* CV Insights */}
              {cvInsights && cvInsights.cv_owner_location?.inference_success && (
                <section className="mt-6" aria-label="CV Insights">
                  <h3 className="text-sm font-semibold text-gray-700 mb-2">CV Insights</h3>
                  <div className="bg-gradient-to-r from-purple-50 to-indigo-50 border border-purple-200 rounded-lg p-4">
                    <div className="flex items-start gap-4">
                      <MapPin className="h-6 w-6 text-purple-600 flex-shrink-0 mt-0.5" aria-hidden="true" />
                      <div className="flex-1">
                        <div className="font-medium text-gray-900 mb-1">Inferred CV Owner Location</div>
                        {cvInsights.cv_owner?.full_name && (
                          <div className="text-sm text-gray-600 mb-2">
                            <span className="font-medium">CV Owner:</span> {cvInsights.cv_owner.full_name}
                          </div>
                        )}
                        <div className="text-sm text-gray-700">
                          {cvInsights.cv_owner_location?.primary_location && (
                            <span>
                              <span className="font-medium">Primary Location:</span>{' '}
                              {cvInsights.cv_owner_location.primary_location.institution &&
                                `${cvInsights.cv_owner_location.primary_location.institution}, `}
                              {cvInsights.cv_owner_location.primary_location.city}
                              {cvInsights.cv_owner_location.primary_location.state &&
                                `, ${cvInsights.cv_owner_location.primary_location.state}`}
                            </span>
                          )}
                        </div>
                        {cvInsights.cv_owner_location?.metro_area && (
                          <div className="text-sm text-gray-600 mt-1">
                            <span className="font-medium">Metro Area:</span> {cvInsights.cv_owner_location.metro_area}
                          </div>
                        )}
                        <div className="text-xs text-gray-500 mt-2">
                          Used for classifying presentations & service activities as Regional/National/International
                        </div>
                      </div>
                    </div>
                  </div>
                </section>
              )}

              {/* Output Files */}
              {currentStepData.status === 'complete' && (
                <OutputFiles
                  runId={runId}
                  step={currentStepData}
                  onOpenJson={openJsonViewer}
                />
              )}

              {/* Feedback Form -- only for completed runs */}
              {runStatus?.status === 'complete' && (
                <section id="feedback-section" className="mt-6">
                  <FeedbackForm runId={runId} />
                </section>
              )}
            </section>
          )}
        </main>
      </div>

      {/* JSON Viewer Modal */}
      <JsonViewerModal
        isOpen={jsonViewerOpen}
        onClose={() => setJsonViewerOpen(false)}
        content={jsonContent}
        filename={jsonFilename}
        downloadUrl={`/api/run/${runId}/data/${jsonFilename}`}
      />

      {/* Cancel Confirmation Modal */}
      <CancelConfirmModal
        isOpen={showCancelConfirm}
        isCancelling={isCancelling}
        onConfirm={confirmCancel}
        onClose={() => setShowCancelConfirm(false)}
      />
    </div>
  )
}
