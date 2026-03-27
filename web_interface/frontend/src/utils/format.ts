/**
 * Format date+time for run tables (RunHistory, AdminSubmissions).
 * Output example: "Mar 26, 2:30 PM"
 */
export function formatDate(dateStr: string | null): string {
  if (!dateStr) return '--'
  const d = new Date(dateStr)
  return (
    d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
    ' ' +
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
