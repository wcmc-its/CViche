/**
 * Format date+time for run tables (RunHistory, AdminSubmissions).
 * Conditionally includes year when date is not current year (D-05).
 * Output examples: "Mar 26, 2:30 PM" (current year), "Mar 26, 2025, 2:30 PM" (previous year)
 */
export function formatDate(dateStr: string | null): string {
  if (!dateStr) return '--'
  const d = new Date(dateStr)
  const now = new Date()
  const includeYear = d.getFullYear() !== now.getFullYear()
  const dateOpts: Intl.DateTimeFormatOptions = includeYear
    ? { month: 'short', day: 'numeric', year: 'numeric' }
    : { month: 'short', day: 'numeric' }
  return (
    d.toLocaleDateString(undefined, dateOpts) +
    ', ' +
    d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  )
}

/**
 * Format date-only for admin user lists (AdminUsers).
 * Output example: "Mar 26, 2026"
 */
export function formatDateShort(dateStr: string | null): string {
  if (!dateStr) return '--'
  const d = new Date(dateStr)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

/**
 * Format duration in seconds to human-readable string.
 * null => em dash, <60 => "45s", >=60 => "2m 5s"
 */
export function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return '\u2014'
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  if (mins > 0) return `${mins}m ${secs}s`
  return `${secs}s`
}

const MINUTES_PER_HOUR = 60
const SECONDS_PER_MINUTE = 60

/**
 * Format a wait or processing time in minutes, rounded: "25 min", "2 h", "2 h 5 m".
 */
export function formatMinutes(minutes: number): string {
  const total = Math.round(minutes)
  const hours = Math.floor(total / MINUTES_PER_HOUR)
  const rest = total % MINUTES_PER_HOUR
  if (!hours) return `${rest} min`
  return rest ? `${hours} h ${rest} m` : `${hours} h`
}

/**
 * "about 3 min left" for a running run: the estimate minus the time already run, rounded up to a
 * minute. null when the estimate is unknown or already used up, so the caller hides it rather than
 * showing a negative or stale figure.
 */
export function formatTimeLeft(estimatedSeconds: number | null | undefined, elapsedSeconds: number): string | null {
  if (!estimatedSeconds || estimatedSeconds <= 0) return null
  const remaining = estimatedSeconds - elapsedSeconds
  if (remaining <= 0) return null
  return `about ${formatMinutes(Math.ceil(remaining / SECONDS_PER_MINUTE))} left`
}

/**
 * Format cost with dollar sign. Default precision 2 for summary views,
 * pass 3 for detail views (PipelineViewer, StepSidebar, AdminSubmissions).
 * null/undefined => em dash. Older runs can store a null cost; without this
 * guard `cost.toFixed` throws and, with no error boundary, blanks the page.
 */
/**
 * Cost so far of a running step: live run total minus the total when the step
 * started. Clamped at 0 because the two operands come at different precisions
 * -- the step-start total is the orchestrator's float64, while a polled run
 * total is read back from a MySQL FLOAT (~6 significant digits), so before the
 * step's first COST_UPDATE the difference can be about -0.000005, shown as
 * "$-0.000".
 */
export function runningStepCost(totalCost: number | null | undefined, stepStartCost: number | undefined): number {
  return Math.max(0, (totalCost || 0) - (stepStartCost || 0))
}

export function formatCost(cost: number | null | undefined, precision: 2 | 3 = 2): string {
  if (cost === null || cost === undefined) return '—'
  return `$${cost.toFixed(precision)}`
}

/**
 * Full date+time for tooltip display.
 * Output example: "Mar 27, 2026, 2:30 PM"
 */
export function formatFullDateTime(d: Date): string {
  return (
    d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) +
    ', ' +
    d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  )
}

/**
 * Format date with relative time for recent runs.
 * - < 60 seconds: "just now"
 * - < 60 minutes: "N minutes ago" (singular: "1 minute ago")
 * - < 24 hours: "N hours ago" (singular: "1 hour ago")
 * - >= 24 hours: absolute date via formatDate()
 *
 * Returns { display, tooltip } where tooltip is the full date+time
 * (only set for relative-time values, per D-04).
 */
export function formatRelativeDate(dateStr: string | null): { display: string; tooltip?: string } {
  if (!dateStr) return { display: '--' }

  const d = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffSec = Math.floor(diffMs / 1000)

  // Future dates (clock skew): fall back to absolute
  if (diffSec < 0) {
    return { display: formatDate(dateStr) }
  }

  const fullDateTime = formatFullDateTime(d)

  if (diffSec < 60) {
    return { display: 'just now', tooltip: fullDateTime }
  }

  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) {
    const label = diffMin === 1 ? '1 minute ago' : `${diffMin} minutes ago`
    return { display: label, tooltip: fullDateTime }
  }

  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) {
    const label = diffHr === 1 ? '1 hour ago' : `${diffHr} hours ago`
    return { display: label, tooltip: fullDateTime }
  }

  // >= 24 hours: absolute date, no tooltip
  return { display: formatDate(dateStr) }
}

/** The run page's warning for a PDF's scanned pages (#1282). */
export function formatScannedPages(pages: number[]): string {
  const one = pages.length === 1
  return `${one ? 'Page' : 'Pages'} ${pages.join(', ')} of this PDF ${one ? 'is a scanned image' : 'are scanned images'}, `
    + `so ${one ? 'its' : 'their'} text couldn't be read and is missing from the output.`
}
