const API_BASE = import.meta.env.VITE_API_URL || ''

export interface ApiError {
  status: number
  message: string
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, options)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const message = body?.detail?.message || body?.detail || `Request failed: ${res.status}`
    throw { status: res.status, message } as ApiError
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
  postRaw: async (path: string, body?: unknown): Promise<Response> => {
    return fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    })
  },
  getRaw: async (path: string): Promise<Response> => {
    return fetch(`${API_BASE}${path}`)
  },
}
