/**
 * Human-readable status label.
 */
export function statusLabel(status: string): string {
  switch (status) {
    case 'complete':
      return 'Complete'
    case 'running':
      return 'Running'
    case 'failed':
      return 'Failed'
    case 'cancelled':
      return 'Cancelled'
    default:
      return 'Pending'
  }
}

/**
 * Tailwind color class for status text.
 * Returns classes like "text-green-600", NOT hex values.
 */
export function statusLabelColor(status: string): string {
  switch (status) {
    case 'complete':
      return 'text-green-600'
    case 'running':
      return 'text-blue-600'
    case 'failed':
      return 'text-red-600'
    case 'cancelled':
      return 'text-orange-600'
    default:
      return 'text-gray-600'
  }
}
