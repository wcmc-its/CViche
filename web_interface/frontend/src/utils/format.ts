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

/**
 * Format cost with dollar sign. Default precision 2 for summary views,
 * pass 3 for detail views (PipelineViewer, StepSidebar, AdminSubmissions).
 */
export function formatCost(cost: number, precision: 2 | 3 = 2): string {
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
