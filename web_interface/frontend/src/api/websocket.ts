export function getWebSocketUrl(path: string): string {
  const apiUrl = import.meta.env.VITE_API_URL
  if (apiUrl) {
    // Convert http(s) to ws(s)
    const wsUrl = apiUrl.replace(/^http/, 'ws')
    return `${wsUrl}${path}`
  }
  // Fallback: derive from current page location (works with Vite proxy in dev)
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}
