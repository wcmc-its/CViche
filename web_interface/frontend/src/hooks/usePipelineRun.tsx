import { useState, useEffect, useRef, useCallback } from 'react'
import type { RunStatus } from '../types'
import { getRunStatus, getRunStep, getPromptLogs } from '../api/runs'
import { getWebSocketUrl } from '../api/websocket'
import { wsRoutes } from '../api/routes'

// Tuning Constants matching your design requirements
const EXPECTED_TOTAL_SECONDS = 475 // Weighted estimates sum (~8 min)
const STALL_NO_PROGRESS_MS = 5 * 60 * 1000
const STALL_ELAPSED_MULTIPLIER = 3
const POLL_FAILURE_THRESHOLD = 3

// Status-poll cadence. The poll is a *fallback* to the WebSocket push below:
// while the socket is actively delivering we read the DB only as an occasional
// reconcile; when it goes silent we fall back to the fast cadence (old behavior).
const POLL_FAST_MS = 2000        // socket silent (disconnected / cross-pod) — full-rate fallback
const POLL_RECONCILE_MS = 20000  // socket healthy — only a periodic safety reconcile against the DB
const WS_FRESH_MS = 10000        // a WS message within this window == the socket is "delivering"
// WebSocket keepalive + reconnect.
const WS_HEARTBEAT_MS = 25000      // client->server keepalive ping (defeats idle-proxy drops)
const WS_RECONNECT_BASE_MS = 1000  // exponential-backoff floor for reconnect
const WS_RECONNECT_MAX_MS = 30000  // exponential-backoff ceiling for reconnect

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
  // WS-aware polling bookkeeping (ms epoch): when the most recent WebSocket
  // message arrived for this run, and when the status poll last read the DB. The
  // poll skips its DB call while the socket is freshly delivering and we
  // reconciled recently (see the poll effect).
  const lastWsMessageAtRef = useRef(0)
  const lastPollAtRef = useRef(0)
  // Latest run status mirrored to a ref so the WebSocket onclose handler can
  // decide whether to reconnect (a terminal run emits nothing more) without
  // re-subscribing the socket on every status change.
  const statusRef = useRef<string | undefined>(undefined)
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
    // Don't let the previous run's socket freshness suppress the new run's poll.
    lastWsMessageAtRef.current = 0
  }, [runId])

  // Status poll — a WS-aware *fallback*, not the primary feed.
  //
  // The WebSocket below pushes every change (steps, cost, terminal status), so
  // while it is actively delivering for this run there's no need to read the DB
  // at the old 2s cadence. Each tick we therefore SKIP the GET /status call when
  // a WS message arrived recently (WS_FRESH_MS) AND we reconciled within
  // POLL_RECONCILE_MS — collapsing a healthy session from ~30 DB reads/min to ~3.
  // When the socket is silent (disconnected, or connected to a different replica
  // than the one running the pipeline while the Redis broker is off — #80, #4),
  // freshness goes stale and the poll reverts to its fast 2s cadence, so
  // correctness is never traded for the saving.
  useEffect(() => {
    let isMounted = true
    const fetchStatus = async () => {
      lastPollAtRef.current = Date.now()
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

    // Per-tick gate. A still-OPEN socket is NOT proof of delivery — under
    // horizontal scaling with the broker off it can be open yet silent — so we
    // key on WS *message recency*, not readyState. The periodic reconcile still
    // reads the DB every POLL_RECONCILE_MS to catch anything a healthy socket
    // somehow dropped (and to keep cost/step counters honest).
    const maybePoll = () => {
      const now = Date.now()
      const wsFresh = now - lastWsMessageAtRef.current < WS_FRESH_MS
      const reconciledRecently = now - lastPollAtRef.current < POLL_RECONCILE_MS
      if (wsFresh && reconciledRecently) return
      fetchStatus()
    }

    // A terminal run never changes again on its own, so the poll stops once we
    // observe a terminal status — except while a retry is in flight, the one case
    // that legitimately flips terminal->running. There is no "run running"
    // WebSocket event, so a resumed poll is the only thing that can pick up that
    // transition (see reconcileStatus). Take one final snapshot to capture the
    // authoritative terminal state, then leave the interval off.
    if (runStatus && TERMINAL_RUN_STATUSES.includes(runStatus.status) && !retryInFlight) {
      fetchStatus()
      return () => {
        isMounted = false
      }
    }

    fetchStatus()
    const interval = setInterval(maybePoll, POLL_FAST_MS)
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

  // Mirror the latest status into a ref for the WebSocket onclose handler.
  useEffect(() => {
    statusRef.current = runStatus?.status
  }, [runStatus?.status])

  // Real-time push (WebSocket) — the primary update channel, with a keepalive
  // heartbeat and auto-reconnect so it stays primary across long runs and
  // recovers dropped sockets (restoring the low-DB-load regime of the poll above).
  useEffect(() => {
    let cancelled = false
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let heartbeatTimer: ReturnType<typeof setInterval> | null = null
    let attempt = 0

    const clearHeartbeat = () => {
      if (heartbeatTimer) {
        clearInterval(heartbeatTimer)
        heartbeatTimer = null
      }
    }

    // Apply a pushed event to local state. lastWsMessageAt marks the socket as
    // delivering (this is what gates the poll above). statusRef is updated
    // SYNCHRONOUSLY on terminal events so the onclose reconnect guard can't miss
    // a run that just finished (the post-render mirror effect would lag it).
    const handleMessage = (event: MessageEvent) => {
      lastWsMessageAtRef.current = Date.now()
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
          statusRef.current = 'failed'
          setRunStatus((prev) =>
            prev ? { ...prev, status: 'failed', error_message: data.error ?? prev.error_message } : prev
          )
          break
        case 'RUN_CANCELLED':
          statusRef.current = 'cancelled'
          setRunStatus((prev) => (prev ? { ...prev, status: 'cancelled' } : prev))
          break
        case 'RUN_COMPLETE':
          statusRef.current = 'complete'
          setRunStatus((prev) => (prev ? { ...prev, status: 'complete' } : prev))
          break
      }
    }

    const connect = () => {
      if (cancelled) return
      clearHeartbeat() // defensive: never let two heartbeat intervals coexist
      const ws = new WebSocket(getWebSocketUrl(wsRoutes.runStream(runId)))
      wsRef.current = ws

      ws.onopen = () => {
        // Keepalive only: some load balancers/proxies drop an idle socket. The
        // server reads and discards these — they keep the pipe open; they are NOT
        // a liveness probe (the poll's freshness gate handles silent sockets).
        // Backoff is intentionally NOT reset here — see onmessage.
        heartbeatTimer = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            try {
              ws.send('ping')
            } catch {
              /* ignore */
            }
          }
        }, WS_HEARTBEAT_MS)
      }

      ws.onmessage = (event) => {
        // Reset backoff on actual DELIVERY, not on open: a socket can open
        // successfully yet never deliver (a different replica than the one
        // running the pipeline, with the Redis broker off). Resetting on open
        // would let such a socket reconnect at the 1s floor forever instead of
        // backing off; only a real message proves the connection is useful.
        attempt = 0
        handleMessage(event)
      }

      ws.onclose = () => {
        clearHeartbeat()
        if (cancelled) return
        // A finished run emits nothing more — don't reconnect it.
        if (statusRef.current && TERMINAL_RUN_STATUSES.includes(statusRef.current)) return
        // Reconnect with capped exponential backoff. While the socket is down (or
        // open-but-silent), lastWsMessageAt goes stale, so the poll above reverts
        // to its fast cadence and the user keeps getting updates until push is back.
        attempt += 1
        const delay = Math.min(WS_RECONNECT_BASE_MS * 2 ** (attempt - 1), WS_RECONNECT_MAX_MS)
        reconnectTimer = setTimeout(connect, delay)
      }

      ws.onerror = () => {
        // onclose fires next and owns the reconnect; nothing to do here.
      }
    }

    connect()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      clearHeartbeat()
      const ws = wsRef.current
      wsRef.current = null
      if (ws) {
        ws.onclose = null // prevent the teardown close from scheduling a reconnect
        ws.close()
      }
    }
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