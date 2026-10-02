import { useState, useEffect, useRef, useCallback } from 'react'
import type { RunStatus } from '../types'
import { getRunStatus, getRunStep, getPromptLogs } from '../api/runs'
import { getWebSocketUrl } from '../api/websocket'
import { wsRoutes } from '../api/routes'
import { clockTime, formatLogLine, mergeLogLines } from '../utils/logLines'

// Tuning Constants matching your design requirements
// Fallback only — runs now carry a per-document estimated_duration_seconds; this
// fixed value is used solely for older runs created before that column existed.
const EXPECTED_TOTAL_SECONDS = 475 // Weighted estimates sum (~8 min)
const STALL_NO_PROGRESS_MS = 5 * 60 * 1000
// "Taking longer than expected" fires past this multiple of the run's OWN
// input-scaled estimate, so a legitimately large CV no longer trips it just for
// exceeding a one-size-fits-all constant. The genuine-stall signal is the
// no-progress timer above, which is independent of run size.
const STALL_PAD_MULTIPLIER = 2
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
  //
  // Mirrored as state (retryInFlight) so flipping it can re-run the poll effect:
  // the ref alone is read synchronously inside fetchStatus, but a ref mutation
  // never re-runs an effect, so a retry at terminal status would not restart the
  // stopped 2s poll without a reactive signal.
  const retryInFlightRef = useRef(false)
  const [retryInFlight, setRetryInFlightState] = useState(false)

  // New run (mount or restart navigation): clear the retry carve-out so it can't
  // leak across runs. Keyed only on runId so a retry within the same run isn't
  // wiped (the poll/elapsed effects close the carve-out once running is observed).
  useEffect(() => {
    retryInFlightRef.current = false
    setRetryInFlightState(false)
  }, [runId])

  // Authoritative status polling loop (Every 2 seconds)
  useEffect(() => {
    let isMounted = true
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

    // A terminal run never changes again on its own, so the ~3-DB-query/tick
    // poll must stop once we observe a terminal status — except while a retry is
    // in flight, the one case that legitimately flips terminal->running. There is
    // no "run running" WebSocket event, so a resumed poll is the only thing that
    // can pick up that transition (see reconcileStatus). Take one final snapshot
    // to capture the authoritative terminal state, then leave the interval off.
    if (runStatus && TERMINAL_RUN_STATUSES.includes(runStatus.status) && !retryInFlight) {
      fetchStatus()
      return () => {
        isMounted = false
      }
    }

    fetchStatus()
    const interval = setInterval(fetchStatus, 2000)
    return () => {
      isMounted = false
      clearInterval(interval)
    }
  }, [runId, runStatus?.status, retryInFlight])

  // Polling fallback mechanism for individual active step log buffers
  useEffect(() => {
    let isMounted = true
    const fetchStepLogs = async () => {
      try {
        const data = await getRunStep(runId, currentStep)
        if (!isMounted || !data.logs || data.logs.length === 0) return

        const formattedLogs = data.logs.map((log: any) => formatLogLine(log.time, log.message))
        setLogs((prev) => {
          const merged = mergeLogLines(prev[currentStep] || [], formattedLogs)
          return merged === prev[currentStep] ? prev : { ...prev, [currentStep]: merged }
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
    const wsUrl = getWebSocketUrl(wsRoutes.runStream(runId))
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
          {
            const line = formatLogLine(clockTime(data.timestamp), data.message)
            setLogs((prev) => ({ ...prev, [data.step]: mergeLogLines(prev[data.step] || [], [line]) }))
          }
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
      // (Status is now non-terminal, so the poll keeps ticking regardless.)
      retryInFlightRef.current = false
      setRetryInFlightState(false)
      if (!startTimeRef.current) {
        startTimeRef.current = runStatus.total_duration_seconds
          ? Date.now() - runStatus.total_duration_seconds * 1000
          : Date.now()
        setLocalElapsedSeconds(runStatus.total_duration_seconds || 0)
      }

      // Scale the "longer than expected" threshold to THIS run's input-scaled
      // estimate; fall back to the fixed constant for runs predating it.
      const expectedSeconds = runStatus.estimated_duration_seconds || EXPECTED_TOTAL_SECONDS

      const timer = setInterval(() => {
        if (!startTimeRef.current) return
        const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000)
        setLocalElapsedSeconds(elapsed)

        const noProgressMs = Date.now() - lastProgressRef.current.at
        setMaybeStuck(
          noProgressMs > STALL_NO_PROGRESS_MS || elapsed > expectedSeconds * STALL_PAD_MULTIPLIER
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
  }, [runStatus?.status, runStatus?.total_duration_seconds, runStatus?.estimated_duration_seconds])

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
  //
  // Writes both the ref (read synchronously by fetchStatus's reconcile) and the
  // state (a reactive signal that re-runs the poll effect). The state write is
  // what restarts the stopped 2s poll when a retry is triggered at terminal
  // status — there's no "run running" WebSocket event, so the resumed poll is
  // the only path that observes the terminal->running transition.
  const setRetryInFlight = useCallback((value: boolean) => {
    retryInFlightRef.current = value
    setRetryInFlightState(value)
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