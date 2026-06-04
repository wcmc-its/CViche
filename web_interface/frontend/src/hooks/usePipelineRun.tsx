import { useState, useEffect, useRef, useCallback } from 'react'
import type { RunStatus } from '../types'
import { getRunStatus, getRunStep, getPromptLogs } from '../api/runs'
import { getWebSocketUrl } from '../api/websocket'

// Tuning Constants matching your design requirements
const EXPECTED_TOTAL_SECONDS = 475 // Weighted estimates sum (~8 min)
const STALL_NO_PROGRESS_MS = 5 * 60 * 1000
const STALL_ELAPSED_MULTIPLIER = 3
const POLL_FAILURE_THRESHOLD = 3

// Run/step statuses that are terminal — once observed, a stale poll snapshot must
// not be allowed to move away from them (see reconcileStatus).
const TERMINAL_RUN_STATUSES = ['complete', 'failed', 'cancelled']
const TERMINAL_STEP_STATUSES = ['complete', 'error']

// Merge an authoritative-but-possibly-stale 2s poll snapshot into the current
// state without letting it override fresher, monotonic information.
//
// runStatus has two writers: this poll (a full snapshot) and the WebSocket
// (optimistic partial updates below). The poll's DB read can predate a terminal
// commit while a WebSocket terminal event (RUN_COMPLETE/FAILED/CANCELLED) has
// already advanced the UI. A blind replace would then let that stale 'running'
// snapshot resurrect a finished run for ~2s (banner/timer flap), regress live
// cost/token counters, and rewind per-step progress. Reconcile instead: terminal
// run status and terminal per-step status are sticky, and counters are monotonic.
//
// A different run (restart navigation) or a user-triggered retry — the only
// legitimate terminal->running transition — bypasses the stickiness.
function reconcileStatus(
  prev: RunStatus | null,
  next: RunStatus,
  retryInFlight: boolean
): RunStatus {
  if (!prev || prev.run_id !== next.run_id || retryInFlight) return next

  // A stale snapshot must never resurrect a finished run.
  if (
    TERMINAL_RUN_STATUSES.includes(prev.status) &&
    !TERMINAL_RUN_STATUSES.includes(next.status)
  ) {
    return prev
  }

  // Keep per-step terminal states the snapshot would rewind (e.g. an optimistic
  // STEP_ERROR, or an already-complete step momentarily shown as still running).
  const steps = next.steps.map((step) => {
    const prevStep = prev.steps.find((s) => s.step_number === step.step_number)
    return prevStep &&
      TERMINAL_STEP_STATUSES.includes(prevStep.status) &&
      !TERMINAL_STEP_STATUSES.includes(step.status)
      ? prevStep
      : step
  })

  // Cost/token counters only ever increase; never let a stale read tick them back.
  return {
    ...next,
    steps,
    total_cost: Math.max(prev.total_cost ?? 0, next.total_cost ?? 0),
    total_tokens: Math.max(prev.total_tokens ?? 0, next.total_tokens ?? 0),
    input_tokens: Math.max(prev.input_tokens ?? 0, next.input_tokens ?? 0),
    output_tokens: Math.max(prev.output_tokens ?? 0, next.output_tokens ?? 0),
  }
}

export function usePipelineRun(runId: string) {
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null)
  const [currentStep, setCurrentStep] = useState(1)
  const [logs, setLogs] = useState<Record<number, string[]>>({})
  const [stepProgress, setStepProgress] = useState<Record<number, { current: number; total: number; message: string }>>({})
  
  const [showPromptLogs, setShowPromptLogs] = useState(false)
  const [promptLogs, setPromptLogs] = useState<{ filename: string; content: string }[]>([])
  const [promptLogsMessage, setPromptLogsMessage] = useState<string | null>(null)
  const [selectedPromptLog, setSelectedPromptLog] = useState<string | null>(null)

  const [localElapsedSeconds, setLocalElapsedSeconds] = useState(0)
  const [stepStartTimes, setStepStartTimes] = useState<Record<number, number>>({})
  const [stepStartCosts, setStepStartCosts] = useState<Record<number, number>>({})
  
  const [connectionLost, setConnectionLost] = useState(false)
  const [maybeStuck, setMaybeStuck] = useState(false)

  const wsRef = useRef<WebSocket | null>(null)
  const startTimeRef = useRef<number | null>(null)
  const pollFailuresRef = useRef(0)
  const lastProgressRef = useRef<{ fingerprint: string; at: number }>({ fingerprint: '', at: Date.now() })
  // True from when the user triggers a per-step retry until the run is observed
  // running again. Lets the otherwise-monotonic poll reducer accept the one
  // legitimate terminal->running transition a retry causes (see reconcileStatus).
  const retryInFlightRef = useRef(false)

  // Authoritative status polling loop (Every 2 seconds)
  useEffect(() => {
    let isMounted = true
    // New run (mount or restart navigation): clear the retry carve-out so it
    // can't leak across runs.
    retryInFlightRef.current = false
    const fetchStatus = async () => {
      try {
        const data = await getRunStatus(runId)
        if (!isMounted) return
        pollFailuresRef.current = 0
        setConnectionLost(false)
        // Reconcile rather than blind-replace: a stale poll snapshot must not
        // demote a WebSocket-applied terminal status, rewind per-step progress,
        // or regress cost/token counters. See reconcileStatus.
        setRunStatus((prev) => reconcileStatus(prev, data, retryInFlightRef.current))
      } catch {
        if (!isMounted) return
        pollFailuresRef.current += 1
        if (pollFailuresRef.current >= POLL_FAILURE_THRESHOLD) {
          setConnectionLost(true)
        }
      }
    }

    fetchStatus()
    const interval = setInterval(fetchStatus, 2000)
    return () => {
      isMounted = false
      clearInterval(interval)
    }
  }, [runId])

  // Polling fallback mechanism for individual active step log buffers
  useEffect(() => {
    let isMounted = true
    const fetchStepLogs = async () => {
      try {
        const data = await getRunStep(runId, currentStep)
        if (!isMounted || !data.logs || data.logs.length === 0) return

        const formattedLogs = data.logs.map((log: any) => `[${log.time}] ${log.message}`)
        setLogs((prev) => {
          const currentStepLogs = prev[currentStep] || []
          const uniqueNewLogs = formattedLogs.filter((log: string) => !currentStepLogs.includes(log))
          if (uniqueNewLogs.length === 0) return prev
          return { ...prev, [currentStep]: [...currentStepLogs, ...uniqueNewLogs] }
        })
      } catch (err) {
        console.error('Error fetching step logs:', err)
      }
    }

    fetchStepLogs()

    const currentStepInfo = runStatus?.steps?.find((s) => s.step_number === currentStep)
    const isRunning = runStatus?.status === 'running' && 
      (currentStepInfo?.status === 'running' || currentStepInfo?.status === 'pending')

    let interval: ReturnType<typeof setInterval> | null = null
    if (isRunning) {
      interval = setInterval(fetchStepLogs, 1000)
    }

    return () => {
      isMounted = false
      if (interval) clearInterval(interval)
    }
  }, [runId, currentStep, runStatus?.status, runStatus?.steps])

  // Real-time asynchronous push infrastructure (WebSockets)
  useEffect(() => {
    const wsUrl = getWebSocketUrl(`/ws/run/${runId}/stream`)
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)

      switch (data.event) {
        case 'STEP_START':
          setCurrentStep(data.step)
          setStepStartTimes((prev) => ({ ...prev, [data.step]: Date.now() }))
          setStepStartCosts((prev) => ({ ...prev, [data.step]: data.total_cost || 0 }))
          setStepProgress((prev) => ({ ...prev, [data.step]: { current: 0, total: 0, message: '' } }))
          break
        case 'LOG':
          const logMessage = data.timestamp
            ? `[${new Date(data.timestamp).toLocaleTimeString()}] ${data.message}`
            : data.message
          setLogs((prev) => ({
            ...prev,
            [data.step]: [...(prev[data.step] || []), logMessage],
          }))
          break
        case 'PROGRESS':
          setStepProgress((prev) => ({
            ...prev,
            [data.step]: { current: data.current, total: data.total, message: data.message || '' },
          }))
          break
        case 'COST_UPDATE':
          setRunStatus((prev) => {
            if (!prev) return prev
            return {
              ...prev,
              total_cost: data.total_cost,
              total_tokens: data.total_tokens,
              input_tokens: data.input_tokens || prev.input_tokens,
              output_tokens: data.output_tokens || prev.output_tokens,
            }
          })
          break
        case 'STEP_ERROR':
          setRunStatus((prev) =>
            prev
              ? {
                  ...prev,
                  steps: prev.steps.map((s) =>
                    s.step_number === data.step ? { ...s, status: 'error' } : s
                  ),
                }
              : prev
          )
          break
        case 'RUN_FAILED':
          setRunStatus((prev) =>
            prev ? { ...prev, status: 'failed', error_message: data.error ?? prev.error_message } : prev
          )
          break
        case 'RUN_CANCELLED':
          setRunStatus((prev) => (prev ? { ...prev, status: 'cancelled' } : prev))
          break
        case 'RUN_COMPLETE':
          setRunStatus((prev) => (prev ? { ...prev, status: 'complete' } : prev))
          break
      }
    }

    return () => ws.close()
  }, [runId])

  // Explicit elapsed timing context loop and system stall guard
  useEffect(() => {
    if (runStatus?.status === 'running') {
      // Running observed: the retry carve-out has served its purpose, so close
      // it before a later stale 'running' poll could be mistaken for a retry.
      retryInFlightRef.current = false
      if (!startTimeRef.current) {
        startTimeRef.current = runStatus.total_duration_seconds
          ? Date.now() - runStatus.total_duration_seconds * 1000
          : Date.now()
        setLocalElapsedSeconds(runStatus.total_duration_seconds || 0)
      }

      const timer = setInterval(() => {
        if (!startTimeRef.current) return
        const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000)
        setLocalElapsedSeconds(elapsed)

        const noProgressMs = Date.now() - lastProgressRef.current.at
        setMaybeStuck(
          noProgressMs > STALL_NO_PROGRESS_MS || elapsed > EXPECTED_TOTAL_SECONDS * STALL_ELAPSED_MULTIPLIER
        )
      }, 1000)

      return () => clearInterval(timer)
    } else {
      if (typeof runStatus?.total_duration_seconds === 'number') {
        setLocalElapsedSeconds(runStatus.total_duration_seconds)
      }
      startTimeRef.current = null
      setMaybeStuck(false)
    }
  }, [runStatus?.status, runStatus?.total_duration_seconds])

  // Monitors backend progress signature (Resets stall alert upon data advancement)
  useEffect(() => {
    if (!runStatus) return
    const completeCount = runStatus.steps.filter((s) => s.status === 'complete').length
    const runningStep = runStatus.steps.find((s) => s.status === 'running')?.step_number ?? -1
    const fingerprint = `${completeCount}|${runningStep}|${runStatus.total_cost}|${runStatus.total_tokens}`
    
    if (fingerprint !== lastProgressRef.current.fingerprint) {
      lastProgressRef.current = { fingerprint, at: Date.now() }
      setMaybeStuck(false)
    }
  }, [runStatus])

  // Automatically anchors viewing viewport onto final workspace steps upon complete transitions
  useEffect(() => {
    if (runStatus?.status === 'complete' && runStatus.steps.length > 0) {
      setCurrentStep(runStatus.steps[runStatus.steps.length - 1].step_number)
    }
  }, [runStatus?.status, runStatus?.steps.length])

  // Explicit isolated retrieval invocation for detailed model prompting traces
  const fetchPromptLogsContext = useCallback(async () => {
    try {
      const data = await getPromptLogs(runId, currentStep)
      setPromptLogs(data.logs || [])
      setPromptLogsMessage(data.message || null)
      setShowPromptLogs(true)
      setSelectedPromptLog(data.logs && data.logs.length > 0 ? data.logs[0].filename : null)
    } catch (err) {
      console.error('Error fetching prompt logs:', err)
    }
  }, [runId, currentStep])

  // Keeps the isolated prompt log viewport updated on user tracking adjustments
  useEffect(() => {
    if (showPromptLogs) {
      fetchPromptLogsContext()
    }
  }, [currentStep, showPromptLogs, fetchPromptLogsContext])

  // Opened by the component's retry handler so the poll reducer accepts the run
  // flipping from a terminal status back to "running"; auto-closed once running
  // is observed (above) and on runId change.
  const setRetryInFlight = useCallback((value: boolean) => {
    retryInFlightRef.current = value
  }, [])

  return {
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
    setRetryInFlight,
  }
}