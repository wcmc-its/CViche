/** Step log lines are "[HH:MM:SS] message". The WebSocket and the polling fallback both feed them,
 *  so both must produce the same clock format for the same entry to be recognised as one line. */

const CLOCK_TIME = /T(\d{2}:\d{2}:\d{2})/

/** HH:MM:SS from an ISO timestamp (the server's clock, as the polled logs use); '' when it has none. */
export function clockTime(timestamp: string | undefined): string {
  return CLOCK_TIME.exec(timestamp ?? '')?.[1] ?? ''
}

/** "[HH:MM:SS] message", or the bare message when the time is unknown. */
export function formatLogLine(time: string, message: string): string {
  return time ? `[${time}] ${message}` : message
}

/** `existing` plus the `incoming` lines it doesn't hold yet (a line is identified by time + message). */
export function mergeLogLines(existing: string[], incoming: string[]): string[] {
  const seen = new Set(existing)
  const added = incoming.filter((line) => {
    if (seen.has(line)) return false
    seen.add(line)
    return true
  })
  return added.length ? [...existing, ...added] : existing
}
