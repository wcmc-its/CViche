import { useState, useEffect, useRef, useCallback } from 'react'
import { MapPin, XCircle, AlertCircle, Clock, CheckCircle2, Download, LifeBuoy } from 'lucide-react'
import type { RunStatus } from '../types'
import { getRunDataJson, cancelRun, restartRun, retryStep } from '../api/runs'
import { formatCost } from '../utils'
import { usePipelineRun } from '../hooks/usePipelineRun'

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
  // Extract clean state and background engine processing mechanisms out of the custom hook
  const {
    runStatus,
    setRunStatus,
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
    setMaybeStuck,
    fetchPromptLogsContext,
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
  
  const cvInsightsAttemptedRef = useRef<string | null>(null)
  const logsEndRef = useRef<HTMLDivElement>(null)
  const [cvInsights, setCvInsights] = useState<any>(null)

  // Explicit user page exit protection alert interceptor
  useEffect(() => {
    if (runStatus?.status !== 'running') return
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [runStatus?.status])

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
      if (onNavigateToRun) onNavigateToRun(data.new_run_id)
    } catch (err: any) {
      setApiError(`Failed to restart: ${err.message || 'Unknown error'}`)
    } finally { setIsRestarting(false) }
  }

  const handleRetry = async () => {
    if (isRetrying) return
    const failedStep = runStatus?.steps?.find((s) => s.status === 'error')
    if (!failedStep) return
    setIsRetrying(true)
    try {
      await retryStep(runId, failedStep.step_number)
    } catch (err: any) {
      setApiError(`Failed to retry: ${err.message || 'Unknown error'}`)
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
    <main className="flex items-center justify-center min-h-screen bg-surface-muted">
      <div className="text-gray-600 font-medium" role="status" aria-live="polite">
        Loading pipeline data...
      </div>
    </main>
  )
}

  return (
    <div className="min-h-screen bg-surface-muted">
      {apiError && <ErrorBanner message={apiError} onDismiss={() => setApiError(null)} />}

      {connectionLost && (
        <div className="bg-orange-50 border-b border-orange-300 px-6 py-3" role="status" aria-live="polite">
          <div className="flex items-center gap-3 max-w-full">
            <AlertCircle className="h-5 w-5 text-orange-600 flex-shrink-0" aria-hidden="true" />
            <p className="text-sm font-medium text-orange-800">Lost connection to the server — showing cached data. Reconnecting…</p>
          </div>
        </div>
      )}

      {runStatus.status === 'running' && maybeStuck && (
        <div className="bg-orange-50 border-b border-orange-300 px-6 py-3" role="alert">
          <div className="flex items-center justify-between max-w-full">
            <div className="flex items-center gap-3">
              <AlertCircle className="h-5 w-5 text-orange-600 flex-shrink-0" aria-hidden="true" />
              <p className="text-sm font-medium text-orange-800">This run is taking much longer than expected and may be stuck.</p>
            </div>
            <div className="ml-4 flex shrink-0 gap-2">
              <button onClick={handleCancel} disabled={isCancelling} className="rounded-lg px-4 py-1.5 text-sm font-medium bg-orange-600 text-white hover:bg-orange-700 disabled:opacity-55">{isCancelling ? 'Cancelling...' : 'Cancel'}</button>
              <button onClick={handleRestart} disabled={isRestarting} className="rounded-lg px-4 py-1.5 text-sm font-medium bg-orange-100 text-orange-800 hover:bg-orange-200 disabled:opacity-55">Restart with file</button>
              <a href={supportHref('a run appears stuck')} className="inline-flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-sm font-medium text-orange-800 hover:bg-orange-100"><LifeBuoy className="h-4 w-4" />Contact support</a>
            </div>
          </div>
        </div>
      )}

      {runStatus.status === 'complete' && (
        <div className="bg-success-50 border-b-2 border-success-600 px-6 py-4" role="status" aria-live="polite">
          <div className="flex items-center justify-between gap-4 max-w-full">
            <div className="flex items-center gap-3">
              <CheckCircle2 className="h-6 w-6 text-success-600 flex-shrink-0" />
              <div>
                <p className="text-base font-semibold text-success-800">Your CV is ready to download</p>
                <p className="text-sm text-success-700">{runStatus.filename}</p>
              </div>
            </div>
            {finalDocxName && (
              <a href={`/api/run/${runId}/data/${finalDocxName}`} download className="shrink-0 inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-semibold bg-success-600 text-white shadow-md hover:bg-success-700"><Download className="h-5 w-5" /><span>Download Word document</span></a>
            )}
          </div>
        </div>
      )}

      {runStatus.status === 'failed' && (
        <div className="bg-red-50 border-b border-red-300 px-6 py-3" role="alert">
          <div className="flex items-center justify-between max-w-full">
            <div className="flex items-center gap-3">
              <XCircle className="h-5 w-5 text-red-600 flex-shrink-0" />
              <p className="text-sm font-medium text-red-800">Pipeline failed {runStatus.error_message && ` — ${runStatus.error_message}`}</p>
            </div>
            <div className="ml-4 flex shrink-0 gap-2">
              {runStatus.steps?.some((s) => s.status === 'error') && (
                <button onClick={handleRetry} disabled={isRetrying || isRestarting} className="rounded-lg px-4 py-1.5 text-sm font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-55">{isRetrying ? 'Retrying...' : 'Retry failed step'}</button>
              )}
              <button onClick={handleRestart} disabled={isRestarting || isRetrying} className="rounded-lg px-4 py-1.5 text-sm font-medium bg-red-100 text-red-800 hover:bg-red-200 disabled:opacity-55">Restart with file</button>
              <a href={supportHref('a run failed')} className="inline-flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-sm font-medium text-red-800 hover:bg-red-100"><LifeBuoy className="h-4 w-4" />Contact support</a>
            </div>
          </div>
        </div>
      )}

      {runStatus.status === 'cancelled' && (
        <div className="bg-orange-50 border-b border-orange-300 px-6 py-3" role="status">
          <div className="flex items-center justify-between max-w-full">
            <div className="flex items-center gap-3">
              <AlertCircle className="h-5 w-5 text-orange-600 flex-shrink-0" />
              <p className="text-sm font-medium text-orange-800">Pipeline was cancelled {runStatus.error_message && ` — ${runStatus.error_message}`}</p>
            </div>
            <button onClick={handleRestart} disabled={isRestarting} className="ml-4 shrink-0 rounded-lg px-4 py-1.5 text-sm font-medium bg-orange-100 text-orange-800 hover:bg-orange-200 disabled:opacity-55">Restart with file</button>
          </div>
        </div>
      )}

      {runStatus.status === 'created' && (
        <div className="bg-gray-50 border-b border-gray-300 px-6 py-3" role="status">
          <div className="flex items-center justify-between max-w-full">
            <p className="text-sm font-medium text-gray-700">Pipeline has not been started yet</p>
            <button onClick={handleRestart} disabled={isRestarting} className="ml-4 shrink-0 rounded-lg px-4 py-1.5 text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-55">Restart with file</button>
          </div>
        </div>
      )}

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

      <div className={`bg-white border-b border-gray-200 px-6 py-3 transition-opacity ${runStatus.status === 'running' ? 'opacity-100' : 'opacity-0 h-0 py-0 overflow-hidden'}`} role="progressbar" aria-valuenow={displayProgress} aria-valuemin={0} aria-valuemax={100}>
        <div className="flex items-center gap-3 max-w-full">
          <span className="text-sm font-medium text-gray-700 min-w-[80px]">Progress:</span>
          <div className="flex-1 bg-gray-200 rounded-full h-6 overflow-hidden">
            <div className="h-full bg-gradient-to-r from-primary-500 to-primary-600 transition-all duration-100 ease-linear relative overflow-hidden" style={{ width: `${displayProgress}%` }} />
          </div>
          <span className="text-sm font-semibold text-gray-900 min-w-[50px] text-right">{displayProgress}%</span>
        </div>
      </div>

      <div className="flex flex-col md:flex-row max-w-full">
        <StepSidebar steps={runStatus.steps} currentStep={currentStep} onSelectStep={setCurrentStep} stepStartTimes={stepStartTimes} stepStartCosts={stepStartCosts} totalCost={runStatus.total_cost} />

        <main className="flex-1 p-4 md:p-8" aria-label="Step details">
          {currentStepData && (
            <section className="bg-white rounded-lg shadow p-6">
              <div className="mb-6">
                <h2 className="text-xl md:text-2xl font-bold text-gray-900 mb-2">Stage {currentStepData.stage_id || currentStep}: {currentStepData.step_name}</h2>
                {currentStepData.stage_id && STAGE_DESCRIPTIONS[currentStepData.stage_id] && (
                  <p className="text-sm text-gray-600 mb-3 bg-primary-50 p-3 rounded-lg border border-primary-100">{STAGE_DESCRIPTIONS[currentStepData.stage_id]}</p>
                )}
                <div className="flex flex-wrap items-center gap-2 md:gap-4 text-sm text-gray-600">
                  <span>Status: <strong>{currentStepData.status}</strong></span>
                  <span className="hidden md:inline">·</span>
                  <span>Duration: <strong>{currentStepData.status === 'running' && stepStartTimes[currentStep] ? `${Math.floor((Date.now() - stepStartTimes[currentStep]) / 1000)}s` : currentStepData.duration_seconds ? `${currentStepData.duration_seconds}s` : '—'}</strong></span>
                  <span className="hidden md:inline">·</span>
                  <span>Cost: <strong>{currentStepData.status === 'running' ? formatCost((runStatus.total_cost || 0) - (stepStartCosts[currentStep] || 0), 3) : formatCost(currentStepData.cost, 3)}</strong></span>
                </div>

                {currentStepData.status === 'running' && stepProgress[currentStep] && stepProgress[currentStep].total > 0 && (
                  <div className="mt-4 p-3 bg-primary-50 border border-primary-200 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-blue-900">{stepProgress[currentStep].message || 'Processing...'}</span>
                      <span className="text-sm font-semibold text-blue-900">{stepProgress[currentStep].current} / {stepProgress[currentStep].total}</span>
                    </div>
                    <div className="bg-primary-200 rounded-full h-2 overflow-hidden" role="progressbar" aria-valuenow={stepProgress[currentStep].current} aria-valuemax={stepProgress[currentStep].total}>
                      <div className="h-full bg-primary-600 transition-all duration-300" style={{ width: `${(stepProgress[currentStep].current / stepProgress[currentStep].total) * 100}%` }} />
                    </div>
                  </div>
                )}
              </div>

              <div className="mb-4 flex gap-2" role="tablist">
                <button onClick={() => setShowPromptLogs(false)} className={`px-4 py-2 rounded-lg font-medium ${!showPromptLogs ? 'bg-primary-500 text-white' : 'bg-gray-200 text-gray-700'}`}>Logs</button>
                <button onClick={() => fetchPromptLogsContext()} className={`px-4 py-2 rounded-lg font-medium ${showPromptLogs ? 'bg-primary-500 text-white' : 'bg-gray-200 text-gray-700'}`}>Prompt Logs</button>
              </div>

              {!showPromptLogs && <div id="log-panel"><LogViewer logs={logs[currentStep] || []} logsEndRef={logsEndRef} /></div>}
              {showPromptLogs && <div id="prompt-log-panel"><PromptLogViewer promptLogs={promptLogs} promptLogsMessage={promptLogsMessage} selectedPromptLog={selectedPromptLog} onSelectPromptLog={setSelectedPromptLog} /></div>}

              {cvInsights && cvInsights.cv_owner_location?.inference_success && (
                <section className="mt-6">
                  <h3 className="text-sm font-semibold text-gray-700 mb-2">CV Insights</h3>
                  <div className="bg-gradient-to-r from-purple-50 to-indigo-50 border border-purple-200 rounded-lg p-4">
                    <div className="flex items-start gap-4">
                      <MapPin className="h-6 w-6 text-purple-600 flex-shrink-0 mt-0.5" />
                      <div className="flex-1">
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

              {currentStepData.status === 'complete' && <OutputFiles runId={runId} step={currentStepData} onOpenJson={openJsonViewer} />}
              {runStatus?.status === 'complete' && <section id="feedback-section" className="mt-6"><FeedbackForm runId={runId} /></section>}
            </section>
          )}
        </main>
      </div>

      <JsonViewerModal isOpen={jsonViewerOpen} onClose={() => setJsonViewerOpen(false)} content={jsonContent} filename={jsonFilename} downloadUrl={`/api/run/${runId}/data/${jsonFilename}`} />
      <CancelConfirmModal isOpen={showCancelConfirm} isCancelling={isCancelling} onConfirm={confirmCancel} onClose={() => setShowCancelConfirm(false)} />
    </div>
  )
}