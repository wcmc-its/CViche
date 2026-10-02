import { authRoutes } from './routes'

const API_BASE = import.meta.env.VITE_API_URL || ''

export interface ApiError {
  status: number
  message: string
  /** The backend's error code (detail.error), e.g. 'duplicate_file'. */
  code?: string
}

// Paths that manage auth state themselves. A 401 from these is expected
// (e.g. /api/auth/me during bootstrap means "not signed in") and must NOT
// trigger the global redirect, or the login page would loop.
const AUTH_BOOTSTRAP_PREFIX = authRoutes.bootstrapPrefix

// Registered by the app (see AuthErrorHandler in App.tsx). Invoked on any 401
// from a protected endpoint so the app can clear auth state and bounce to /login.
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler
}

function maybeHandleUnauthorized(path: string, status: number) {
  if (status === 401 && !path.startsWith(AUTH_BOOTSTRAP_PREFIX)) {
    onUnauthorized?.()
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, options)
  if (!res.ok) {
    maybeHandleUnauthorized(path, res.status)
    const body = await res.json().catch(() => null)
    const message = body?.detail?.message || body?.detail || `Request failed: ${res.status}`
    throw { status: res.status, message, code: body?.detail?.error } as ApiError
  }
  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      headers: body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  delete: async (path: string): Promise<void> => {
    // 204 No Content responses have no body to parse, so we don't go through
    // request<T> (which always calls res.json()).
    const res = await fetch(`${API_BASE}${path}`, { method: 'DELETE' })
    if (!res.ok) {
      maybeHandleUnauthorized(path, res.status)
      const body = await res.json().catch(() => null)
      const message = body?.detail?.message || body?.detail || `Request failed: ${res.status}`
      throw { status: res.status, message } as ApiError
    }
  },
  postRaw: async (path: string, body?: unknown): Promise<Response> => {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    })
    maybeHandleUnauthorized(path, res.status)
    return res
  },
  getRaw: async (path: string): Promise<Response> => {
    const res = await fetch(`${API_BASE}${path}`)
    maybeHandleUnauthorized(path, res.status)
    return res
  },
}
