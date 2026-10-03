import { useState, useEffect, useRef, useCallback } from 'react'
import { MapPin, XCircle, AlertCircle, LifeBuoy } from 'lucide-react'
import { getRunDataJson, cancelRun, restartRun, retryStep, startRun } from '../api/runs'
import { runRoutes } from '../api/routes'
import { formatCost, formatScannedPages, runningStepCost } from '../utils'
import { usePipelineRun } from '../hooks/usePipelineRun'

import PipelineHeader from './PipelineHeader'
import StepSidebar, { StepStatusIcon } from './StepSidebar'
import LogViewer from './LogViewer'
import PromptLogViewer from './PromptLogViewer'
import OutputFiles, { DocxDownloadCard, visibleOutputFiles } from './OutputFiles'
import JsonViewerModal from './JsonViewerModal'
import CancelConfirmModal from './CancelConfirmModal'
import ErrorBanner from './ErrorBanner'
import FeedbackForm from './FeedbackForm'
import { RunQualitySections, ReviewNote } from './RunQualityPanel'
import { canActOnRun, useAuth, useCanSeeCost, useCanViewAllRuns } from '../contexts/AuthContext'

interface PipelineViewerProps {
  runId: string
  onBack: () => void
  onNavigateToRun?: (runId: string) => void
}

const STAGE_DESCRIPTIONS: Record<string, string> = {
  '1a': 'Uses GPT to analyze the CV structure and identify all section headers (Education, Publications, Grants, etc.) and their hierarchical relationships.',
  '1b': 'Creates mappings between extracted headers and Word document paragraph indices without using LLM.',
  '2': 'Identifies individual entries within each section (publications, positions, grants) achieving complete document coverage.',
  '3a': 'Uses LLM to map CV section headers to WCM taxonomy codes (e.g., S1 for Peer-Reviewed Articles, B1 for Education).',
  '3b': 'Classifies individual entries to taxonomy codes using header context and entry content, with post-classification correction.',
  '4': 'Extracts structured fields from entries: authors, titles, journals, DOIs, grant numbers, institutions, dates. Also infers CV owner location from employment/education history for geographic scope classification.',
  '4.5': 'Generates a biosketch-style M1 research summary statement analyzing your CV content.',
  '5': 'Enriches publications with PubMed metadata: full author lists, MeSH terms, publication types, abstracts, PMCIDs.',
  '5b': 'Adds institution location data (city, state, country) via batched LLM lookups, using CV owner context for disambiguation. Applies to education (B1, B2), training (C, C1, C2, C3), and position (D1, D2, D3) entries.',
  '5c': 'Reformats teaching entries (K-codes) into a consistent, readable format.',
  '5d': 'Reformats non-enriched citations to Vancouver bibliographic format.',
  '6': 'Generates the final WCM-formatted Word document with all sections filled by taxonomy code. Routes presentations and service activities to Regional/National/International tables based on inferred CV owner location.',
}

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
  const showCost = useCanSeeCost()
  const { user } = useAuth()
  // Admin or staff: Run by, run quality, stage JSON on any run (read-only).
  const canViewAllRuns = useCanViewAllRuns()
  // Extract clean state and background engine processing mechanisms out of the custom hook
  const {
    runStatus,
    currentStep,
    setCurrentStep,
    logs,
    stepProgress,
    showPromptLogs,
    setShowPromptLogs,
    promptLogs,
    promptLogsMessage,
    selectedPromptLog,
    setSelectedPromptLog,
    localElapsedSeconds,
    stepStartTimes,
    stepStartCosts,
    connectionLost,
    maybeStuck,
    fetchPromptLogsContext,
    setRetryInFlight,
  } = usePipelineRun(runId)

  const [displayProgress, setDisplayProgress] = useState(0)
  const [apiError, setApiError] = useState<string | null>(null)
  const [jsonViewerOpen, setJsonViewerOpen] = useState(false)
  const [jsonContent, setJsonContent] = useState<any>(null)
  const [jsonFilename, setJsonFilename] = useState('')
  const [showCancelConfirm, setShowCancelConfirm] = useState(false)
  const [isCancelling, setIsCancelling] = useState(false)
  const [isRestarting, setIsRestarting] = useState(false)
  const [isRetrying, setIsRetrying] = useState(false)
  const [isStarting, setIsStarting] = useState(false)
  // null = automatic: step details are open while a run is in flight and collapsed once it is complete.
  const [detailsOverride, setDetailsOverride] = useState<boolean | null>(null)
  // null = automatic: Logs while running, Summary once there is something to summarise.
  const [tab, setTab] = useState<'summary' | 'logs' | null>(null)
  
  const cvInsightsAttemptedRef = useRef<string | null>(null)
  const logsEndRef = useRef<HTMLDivElement>(null)
  const [cvInsights, setCvInsights] = useState<any>(null)

  // Automatic target window scroll tracker targeting live user context feedback triggers
  useEffect(() => {
    if (window.location.hash === '#feedback' && runStatus?.status === 'complete') {
      const timer = setTimeout(() => {
        const el = document.getElementById('feedback-section')
        if (el) el.scrollIntoView({ behavior: 'smooth' })
      }, 500)
      return () => clearTimeout(timer)
    }
  }, [runStatus?.status])

  
  // Pure data derived state execution calculation — maps linear updates instantly on changes
  const calculateProgress = useCallback(() => {
    if (!runStatus?.steps) return 0
    let completedWeight = 0, runningWeight = 0, runningProgress = 0

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
          runningProgress = stepStartTime ? Math.min(0.95, (Date.now() - stepStartTime) / 1000 / stepInfo.estimated_seconds) : 0.05
        }
      }
    })
    return Math.round(Math.min(100, Math.max(0, ((completedWeight + runningWeight * runningProgress) / TOTAL_WEIGHT) * 100)))
  }, [runStatus?.steps, stepProgress, stepStartTimes])

  // Animation layout frame interpolator for progress bar visualization adjustments
  useEffect(() => {
    if (runStatus?.status !== 'running') {
      setDisplayProgress(calculateProgress())
      return
    }
    const interval = setInterval(() => {
      const targetProgress = calculateProgress()
      setDisplayProgress((prev) => (prev < targetProgress ? Math.min(prev + 1, targetProgress) : targetProgress))
    }, 1000)
    return () => clearInterval(interval)
  }, [runStatus?.status, calculateProgress])

  // Load extracted summary details following complete validation matches on stage 4 transitions
  useEffect(() => {
    const loadCvInsights = async () => {
      const stage4Step = runStatus?.steps.find((s) => s.stage_id === '4')
      if (!stage4Step || stage4Step.status !== 'complete' || cvInsights || cvInsightsAttemptedRef.current === runId) return

      let outputFiles: string[] = []
      try {
        outputFiles = stage4Step.output_files ? JSON.parse(stage4Step.output_files) : []
      } catch { return }

      const fieldsJson = outputFiles.find((f) => f.includes('_fields.json'))
      if (!fieldsJson) return

      cvInsightsAttemptedRef.current = runId
      try {
        const data = await getRunDataJson(runId, fieldsJson)
        if (data.content) {
          setCvInsights({ cv_owner: data.content.cv_owner, cv_owner_location: data.content.cv_owner_location })
        }
      } catch (err) {
        console.error('Error loading CV insights:', err)
      }
    }
    loadCvInsights()
  }, [runStatus?.steps, runId, cvInsights])

  const handleCancel = () => { if (!isCancelling) setShowCancelConfirm(true) }

  const confirmCancel = async () => {
    if (isCancelling) return
    setIsCancelling(true)
    try {
      await cancelRun(runId)
      setShowCancelConfirm(false)
    } catch (err: any) {
      setApiError(`Failed to cancel: ${err.message || 'Unknown error'}`)
    } finally { setIsCancelling(false) }
  }

  const handleRestart = async () => {
    if (isRestarting) return
    setIsRestarting(true)
    try {
      const data = await restartRun(runId)
      if (!data.run_id) throw new Error('Restart did not return a new run id')
      // Restart only *creates* the new run; start it too so "Restart with file"
      // yields a running job instead of a stuck "created" one (mirrors the
      // upload flow's beginRun). Navigate once the start request is accepted.
      await startRun(data.run_id)
      if (onNavigateToRun) onNavigateToRun(data.run_id)
    } catch (err: any) {
      setApiError(`Failed to restart: ${err.message || 'Unknown error'}`)
    } finally { setIsRestarting(false) }
  }

  // A "created" run already exists with its steps — it just never started.
  // Start the existing run rather than spawning a new one (the restart path),
  // so we don't pile up orphaned "created" rows.
  const handleStart = async () => {
    if (isStarting) return
    setIsStarting(true)
    try {
      await startRun(runId)
    } catch (err: any) {
      setApiError(`Failed to start: ${err.message || 'Unknown error'}`)
    } finally { setIsStarting(false) }
  }

  const handleRetry = async () => {
    if (isRetrying) return
    const failedStep = runStatus?.steps?.find((s) => s.status === 'error')
    if (!failedStep) return
    setIsRetrying(true)
    // Open the carve-out so the poll reducer accepts the run flipping from a
    // terminal status back to "running"; cleared once running is observed (in
    // the hook) or here if the retry request itself fails.
    setRetryInFlight(true)
    try {
      await retryStep(runId, failedStep.step_number)
    } catch (err: any) {
      setApiError(`Failed to retry: ${err.message || 'Unknown error'}`)
      setRetryInFlight(false)
    } finally { setIsRetrying(false) }
  }

  const openJsonViewer = async (filename: string) => {
    try {
      const data = await getRunDataJson(runId, filename)
      setJsonContent(data.content)
      setJsonFilename(filename)
      setJsonViewerOpen(true)
    } catch (err) {
      setApiError('Failed to load JSON file.')
    }
  }

  const currentStepData = runStatus?.steps[currentStep - 1]

  const finalDocxName = (() => {
    if (runStatus?.status !== 'complete' || !runStatus.steps) return null
    for (let i = runStatus.steps.length - 1; i >= 0; i--) {
      const raw = runStatus.steps[i].output_files
      if (!raw) continue
      try {
        const files: string[] = JSON.parse(raw)
        const docx = files.find((f) => f.endsWith('.docx'))
        if (docx) return docx.split('/').pop() || docx
      } catch { /* parse error safe ignore */ }
    }
    return null
  })()

  const supportHref = (reason: string) =>
    `mailto:paa2013@med.cornell.edu?subject=${encodeURIComponent(`CViche: ${reason} (run ${runId})`)}&body=${encodeURIComponent(`Run ID: ${runId}\nFile: ${runStatus?.filename}\n\nPlease describe what happened:\n`)}`

  if (!runStatus || !runStatus.steps) {
  return (
    <main className="flex items-center justify-center min-h-screen">
      <div className="text-gray-600 font-medium" role="status" aria-live="polite">
        Loading pipeline data...
      </div>
    </main>
  )
}

  const isComplete = runStatus.status === 'complete'
  // Run write controls (start/cancel/restart/retry, feedback) only for the
  // owner or an admin: staff read any run, and the API 403s their writes.
  const canAct = canActOnRun(user, runStatus.run_by?.id)
  const showDetails = detailsOverride ?? !isComplete

  // Count only what the Summary tab will list for this user: stage JSON is
  // admin/staff-only, and on a finished run the final .docx sits in the download card.
  const currentOutputCount = currentStepData
    ? visibleOutputFiles(currentStepData, canViewAllRuns).filter(
        f => !(isComplete && currentStepData.stage_id === '6' && f.endsWith('.docx')),
      ).length
    : 0
  const showInsights = !!(cvInsights && cvInsights.cv_owner_location?.inference_success)
  // Summary is the default view of every step, as in the mockup; the full log
  // stream sits one tab over.
  const activeTab: 'summary' | 'logs' | 'prompts' = showPromptLogs ? 'prompts' : (tab ?? 'summary')
  const tabs: { key: 'summary' | 'logs' | 'prompts'; label: string; onSelect: () => void }[] = [
    { key: 'summary' as const, label: 'Summary', onSelect: () => { setTab('summary'); setShowPromptLogs(false) } },
    { key: 'logs' as const, label: 'Logs', onSelect: () => { setTab('logs'); setShowPromptLogs(false) } },
    { key: 'prompts' as const, label: 'Prompts', onSelect: () => { fetchPromptLogsContext() } },
  ]

  const stepStatusLabel: Record<string, { label: string; cls: string }> = {
    complete: { label: 'Complete', cls: 'text-success-700' },
    running: { label: 'Running', cls: 'text-primary-700' },
    error: { label: 'Failed', cls: 'text-error-700' },
  }
  const stepStatus = stepStatusLabel[currentStepData?.status ?? ''] ?? { label: 'Pending', cls: 'text-gray-500' }

  const stepDuration = currentStepData
    ? currentStepData.status === 'running' && stepStartTimes[currentStep]
      ? `${Math.floor((Date.now() - stepStartTimes[currentStep]) / 1000)}s`
      : currentStepData.duration_seconds ? `${currentStepData.duration_seconds}s` : '—'
    : '—'
  const currentProgress = stepProgress[currentStep]
  const metrics: { label: string; value: string }[] = currentStepData ? [
    { label: 'Duration', value: stepDuration },
    ...(showCost ? [{
      label: 'Cost',
      value: currentStepData.status === 'running'
        ? formatCost(runningStepCost(runStatus.total_cost, stepStartCosts[currentStep]), 3)
        : formatCost(currentStepData.cost, 3),
    }] : []),
    ...(currentOutputCount > 0 ? [{ label: 'Output files', value: String(currentOutputCount) }] : []),
  ] : []

  // Short key facts for the Summary tab: where the step is, and the last thing it logged.
  const stepLogs = logs[currentStep] || []
  const latestLog = stepLogs.length > 0 ? stepLogs[stepLogs.length - 1].replace(/^\[[^\]]*\]\s?/, '') : null
  const summaryRows: { label: string; value: string }[] = [
    ...(currentStepData?.status === 'running' && currentProgress && currentProgress.total > 0
      ? [{ label: currentProgress.message || 'Progress', value: `${currentProgress.current} of ${currentProgress.total}` }] : []),
    ...(latestLog ? [{ label: 'Latest', value: latestLog }] : []),
  ]

  const bannerBase = 'rounded-xl border px-4 py-3'

  return (
    <div className="min-h-screen">
      {apiError && <ErrorBanner message={apiError} onDismiss={() => setApiError(null)} />}

      <div className={`mx-auto w-full flex flex-col gap-[18px] px-4 sm:px-7 pt-6 pb-16 ${isComplete ? 'max-w-[1000px]' : 'max-w-[1240px]'}`}>
        {connectionLost && (
          <div className={`${bannerBase} bg-orange-50 border-orange-300`} role="status" aria-live="polite">
            <div className="flex items-center gap-3 max-w-full">
              <AlertCircle className="h-5 w-5 text-orange-600 flex-shrink-0" aria-hidden="true" />
              <p className="text-sm font-medium text-orange-800">Lost connection to the server — showing cached data. Reconnecting…</p>
            </div>
          </div>
        )}

        {runStatus.status === 'running' && maybeStuck && (
          <div className={`${bannerBase} bg-orange-50 border-orange-300`} role="alert">
            <div className="flex flex-wrap items-center justify-between gap-3 max-w-full">
              <div className="flex items-center gap-3">
                <AlertCircle className="h-5 w-5 text-orange-600 flex-shrink-0" aria-hidden="true" />
                <p className="text-sm font-medium text-orange-800">This run is taking much longer than expected and may be stuck.</p>
              </div>
              <div className="flex flex-wrap shrink-0 gap-2">
                {canAct && (
                  <>
                    <button onClick={handleCancel} disabled={isCancelling} className="rounded-lg px-4 py-1.5 text-sm font-medium bg-orange-600 text-white hover:bg-orange-700 disabled:opacity-55">{isCancelling ? 'Cancelling...' : 'Cancel'}</button>
                    <button onClick={handleRestart} disabled={isRestarting} className="rounded-lg px-4 py-1.5 text-sm font-medium bg-orange-100 text-orange-800 hover:bg-orange-200 disabled:opacity-55">Restart with file</button>
                  </>
                )}
                <a href={supportHref('a run appears stuck')} className="inline-flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-sm font-medium text-orange-800 hover:bg-orange-100"><LifeBuoy className="h-4 w-4" />Contact support</a>
              </div>
            </div>
          </div>
        )}

        {runStatus.status === 'failed' && (
          <div className={`${bannerBase} bg-red-50 border-red-300`} role="alert">
            <div className="flex flex-wrap items-center justify-between gap-3 max-w-full">
              <div className="flex min-w-0 items-center gap-3">
                <XCircle className="h-5 w-5 text-red-600 flex-shrink-0" />
                <p className="min-w-0 text-sm font-medium text-red-800 [overflow-wrap:anywhere]">Pipeline failed {runStatus.error_message && ` — ${runStatus.error_message}`}</p>
              </div>
              <div className="flex flex-wrap shrink-0 gap-2">
                {canAct && runStatus.steps?.some((s) => s.status === 'error') && (
                  <button onClick={handleRetry} disabled={isRetrying || isRestarting} className="rounded-lg px-4 py-1.5 text-sm font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-55">{isRetrying ? 'Retrying...' : 'Retry failed step'}</button>
                )}
                {canAct && (
                  <button onClick={handleRestart} disabled={isRestarting || isRetrying} className="rounded-lg px-4 py-1.5 text-sm font-medium bg-red-100 text-red-800 hover:bg-red-200 disabled:opacity-55">Restart with file</button>
                )}
                <a href={supportHref('a run failed')} className="inline-flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-sm font-medium text-red-800 hover:bg-red-100"><LifeBuoy className="h-4 w-4" />Contact support</a>
              </div>
            </div>
          </div>
        )}

        {runStatus.status === 'cancelled' && (
          <div className={`${bannerBase} bg-orange-50 border-orange-300`} role="status">
            <div className="flex flex-wrap items-center justify-between gap-3 max-w-full">
              <div className="flex min-w-0 items-center gap-3">
                <AlertCircle className="h-5 w-5 text-orange-600 flex-shrink-0" />
                <p className="min-w-0 text-sm font-medium text-orange-800 [overflow-wrap:anywhere]">Pipeline was cancelled {runStatus.error_message && ` — ${runStatus.error_message}`}</p>
              </div>
              {canAct && (
                <button onClick={handleRestart} disabled={isRestarting} className="shrink-0 rounded-lg px-4 py-1.5 text-sm font-medium bg-orange-100 text-orange-800 hover:bg-orange-200 disabled:opacity-55">Restart with file</button>
              )}
            </div>
          </div>
        )}

        {!!runStatus.scanned_pages?.length && (
          <div className={`${bannerBase} bg-amber-50 border-amber-300`} role="status">
            <div className="flex items-center gap-3 max-w-full">
              <AlertCircle className="h-5 w-5 text-amber-600 flex-shrink-0" aria-hidden="true" />
              <p className="min-w-0 text-sm font-medium text-amber-800">{formatScannedPages(runStatus.scanned_pages)}</p>
            </div>
          </div>
        )}

        {runStatus.status === 'created' && (
          <div className={`${bannerBase} bg-white border-sand-300`} role="status">
            <div className="flex flex-wrap items-center justify-between gap-3 max-w-full">
              <p className="text-sm font-medium text-gray-700">Pipeline has not been started yet</p>
              {canAct && (
                <button onClick={handleStart} disabled={isStarting} className="shrink-0 rounded-lg px-4 py-1.5 text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-55">{isStarting ? 'Starting...' : 'Start pipeline'}</button>
              )}
            </div>
          </div>
        )}

        <PipelineHeader
          runId={runId}
          filename={runStatus.filename}
          title={isComplete ? runStatus.cv_owner_name?.trim() || runStatus.filename : undefined}
          runDate={runStatus.started_at}
          runByName={canViewAllRuns ? runStatus.run_by?.display_name : null}
          status={runStatus.status}
          steps={runStatus.steps}
          stepProgress={stepProgress}
          displayProgress={displayProgress}
          totalCost={runStatus.total_cost}
          inputTokens={runStatus.input_tokens}
          outputTokens={runStatus.output_tokens}
          elapsedSeconds={localElapsedSeconds}
          estimatedSeconds={runStatus.estimated_duration_seconds}
          isCancelling={isCancelling}
          onCancel={canAct ? handleCancel : undefined}
          onBack={onBack}
          detailsOpen={isComplete ? showDetails : undefined}
          onToggleDetails={isComplete ? () => setDetailsOverride(!showDetails) : undefined}
        >
          {/* Always mounted so screen readers announce the change when the run finishes. */}
          <p role="status" aria-live="polite" className="sr-only">
            {isComplete ? 'Your CV is ready to download.' : ''}
          </p>
          {isComplete && finalDocxName && <DocxDownloadCard runId={runId} filename={finalDocxName} />}
          {isComplete && !canViewAllRuns && <ReviewNote runId={runId} />}
        </PipelineHeader>

        {showDetails && (
          <div className="grid grid-cols-1 gap-[18px] items-start md:grid-cols-[minmax(260px,340px)_minmax(0,1fr)]">
            <StepSidebar steps={runStatus.steps} currentStep={currentStep} onSelectStep={setCurrentStep} stepStartTimes={stepStartTimes} />

            <main className="min-w-0" aria-label="Step details">
              {currentStepData && (
                <section className="flex flex-col gap-4 min-w-0 bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] px-4 py-5 sm:px-6">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-xs text-gray-500 font-mono">Stage {currentStepData.stage_id || currentStep}</div>
                      <h2 className="mt-0.5 text-xl font-semibold text-gray-900">{currentStepData.step_name}</h2>
                    </div>
                    <span className={`inline-flex items-center gap-1.5 text-[13px] font-medium ${stepStatus.cls}`}>
                      <StepStatusIcon status={currentStepData.status} className="w-4 h-4" />
                      {stepStatus.label}
                    </span>
                  </div>

                  {currentStepData.stage_id && STAGE_DESCRIPTIONS[currentStepData.stage_id] && (
                    <p className="m-0 text-gray-700">{STAGE_DESCRIPTIONS[currentStepData.stage_id]}</p>
                  )}

                  <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-px overflow-hidden rounded-[10px] border border-sand-200 bg-sand-200">
                    {metrics.map((m) => (
                      <div key={m.label} className="bg-sand-50 px-3.5 py-2.5">
                        <div className="text-xs text-gray-500">{m.label}</div>
                        <div className="font-semibold text-gray-900 tabular-nums">{m.value}</div>
                      </div>
                    ))}
                  </div>


                  <div className="flex gap-[18px] border-b border-sand-200" role="tablist">
                    {tabs.map((t) => {
                      const selected = activeTab === t.key
                      return (
                        <button
                          key={t.key}
                          role="tab"
                          aria-selected={selected}
                          onClick={t.onSelect}
                          className={`-mb-px border-b-2 px-0.5 py-2 font-medium focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none ${
                            selected ? 'border-ink text-gray-900' : 'border-transparent text-gray-500 hover:text-gray-900'
                          }`}
                        >
                          {t.label}
                        </button>
                      )
                    })}
                  </div>

                  {activeTab === 'summary' && (
                    <div id="summary-panel" className="flex flex-col gap-6">
                      {currentStepData.status === 'pending' ? (
                        <p className="text-sm text-gray-500">This step has not started yet.</p>
                      ) : summaryRows.length === 0 && currentOutputCount === 0 && !showInsights ? (
                        <p className="text-sm text-gray-500">Nothing to summarize yet. The Logs tab shows everything this step has reported.</p>
                      ) : summaryRows.length > 0 && (
                        <dl className="text-sm">
                          {summaryRows.map((r) => (
                            <div key={r.label} className="flex items-baseline justify-between gap-6 border-b border-sand-200 py-2.5">
                              <dt className="text-gray-600 shrink-0 max-w-[40%] [overflow-wrap:anywhere]">{r.label}</dt>
                              <dd className="min-w-0 text-right font-semibold text-gray-900 [overflow-wrap:anywhere]">{r.value}</dd>
                            </div>
                          ))}
                        </dl>
                      )}
                      {showInsights && (
                        <section>
                          <h3 className="text-sm font-semibold text-gray-700 mb-2">CV Insights</h3>
                          <div className="bg-gradient-to-r from-purple-50 to-indigo-50 border border-purple-200 rounded-lg p-4">
                            <div className="flex items-start gap-4">
                              <MapPin className="h-6 w-6 text-purple-600 flex-shrink-0 mt-0.5" />
                              <div className="flex-1 min-w-0 [overflow-wrap:anywhere]">
                                <div className="font-medium text-gray-900 mb-1">Inferred CV Owner Location</div>
                                {cvInsights.cv_owner?.full_name && <div className="text-sm text-gray-600 mb-2"><span className="font-medium">CV Owner:</span> {cvInsights.cv_owner.full_name}</div>}
                                <div className="text-sm text-gray-700">
                                  {cvInsights.cv_owner_location?.primary_location && (
                                    <span><span className="font-medium">Primary Location:</span> {cvInsights.cv_owner_location.primary_location.institution && `${cvInsights.cv_owner_location.primary_location.institution}, `}{cvInsights.cv_owner_location.primary_location.city}, {cvInsights.cv_owner_location.primary_location.state}</span>
                                  )}
                                </div>
                              </div>
                            </div>
                          </div>
                        </section>
                      )}
                      <OutputFiles runId={runId} step={currentStepData} onOpenJson={openJsonViewer} showFinalOutput={!isComplete} />
                    </div>
                  )}
                  {activeTab === 'logs' && <div id="log-panel"><LogViewer logs={logs[currentStep] || []} logsEndRef={logsEndRef} /></div>}
                  {activeTab === 'prompts' && <div id="prompt-log-panel"><PromptLogViewer promptLogs={promptLogs} promptLogsMessage={promptLogsMessage} selectedPromptLog={selectedPromptLog} onSelectPromptLog={setSelectedPromptLog} /></div>}
                </section>
              )}
            </main>
          </div>
        )}

        {/* Below the step details, so "Pipeline details" opens them under the header, not past the score. */}
        {isComplete && canViewAllRuns && <RunQualitySections runId={runId} />}

        {isComplete && canAct && (
          <section id="feedback-section">
            <FeedbackForm runId={runId} />
          </section>
        )}
      </div>

      <JsonViewerModal isOpen={jsonViewerOpen} onClose={() => setJsonViewerOpen(false)} content={jsonContent} filename={jsonFilename} downloadUrl={runRoutes.dataFile(runId, jsonFilename)} />
      <CancelConfirmModal isOpen={showCancelConfirm} isCancelling={isCancelling} onConfirm={confirmCancel} onClose={() => setShowCancelConfirm(false)} />
    </div>
  )
}
