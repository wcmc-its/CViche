/**
 * routes.ts — single source of truth for all CViche API + WebSocket paths (issue #105).
 *
 * Every frontend HTTP/WS path literal lives here so route changes happen in one
 * place and call sites stay typed. Builders return the existing literal spelling
 * verbatim — no endpoint paths were invented or renamed.
 *
 * Convention:
 *   - Builders that take params return a template string typed `as const`.
 *   - Static paths are `as const` string literals (returned from zero-arg builders,
 *     or exposed directly where a non-builder string const is required).
 *   - Query-string builders accept already-stringified params where the call site
 *     builds them (admin runs), or typed primitives where it does not.
 */

export const runRoutes = {
  /** GET /api/run/:id/status */
  status: (id: string) => `/api/run/${id}/status` as const,
  /** GET /api/run/:id/step/:step */
  step: (id: string, step: number) => `/api/run/${id}/step/${step}` as const,
  /** GET /api/run/:id/prompt-logs?step=:step */
  promptLogs: (id: string, step: number) =>
    `/api/run/${id}/prompt-logs?step=${step}` as const,
  /** GET /api/run/:id/json/:file  (JSON-parsed data file) */
  dataJson: (id: string, file: string) =>
    `/api/run/${id}/json/${file}` as const,
  /** GET/href /api/run/:id/data/:file  (raw data file download / open) */
  dataFile: (id: string, file: string) =>
    `/api/run/${id}/data/${file}` as const,
  /** GET/href /api/run/:id/input  (original uploaded CV download) */
  inputFile: (id: string) => `/api/run/${id}/input` as const,
  /** POST /api/run/:id/cancel */
  cancel: (id: string) => `/api/run/${id}/cancel` as const,
  /** POST /api/run/:id/restart */
  restart: (id: string) => `/api/run/${id}/restart` as const,
  /** POST /api/run/:id/retry/:step */
  retryStep: (id: string, step: number) =>
    `/api/run/${id}/retry/${step}` as const,
  /** POST /api/run/:id/start */
  start: (id: string) => `/api/run/${id}/start` as const,
  /** GET /api/runs?offset=:offset&limit=:limit[&:extra]  (extra = pre-built
   *  admin scope/filter query string, see buildRunListQuery) */
  list: (offset: number, limit: number, extra = '') =>
    `/api/runs?offset=${offset}&limit=${limit}${extra ? `&${extra}` : ''}`,
  /** GET /api/runs/filter-options?:params  (admin; caller passes a pre-built query string) */
  filterOptions: (params: string) => `/api/runs/filter-options?${params}` as const,
  /** GET /api/runs/feedback-status */
  feedbackStatus: () => `/api/runs/feedback-status` as const,
  /** GET /api/capacity  (read-only run-admission snapshot) */
  capacity: () => `/api/capacity` as const,
} as const

export const feedbackRoutes = {
  /** GET /api/run/:id/feedback  (existing feedback + run context) */
  get: (id: string) => `/api/run/${id}/feedback` as const,
  /** POST /api/run/:id/feedback  (submit feedback) */
  submit: (id: string) => `/api/run/${id}/feedback` as const,
} as const

export const adminRoutes = {
  /** GET /api/admin/stats */
  stats: () => `/api/admin/stats` as const,
  /** GET /api/admin/users */
  users: () => `/api/admin/users` as const,
  /** PUT /api/admin/users/:userId */
  user: (userId: number) => `/api/admin/users/${userId}` as const,
  /** GET /api/admin/runs?:params  (caller passes a pre-built query string) */
  runs: (params: string) => `/api/admin/runs?${params}` as const,
  /** POST /api/admin/run/:runId/score */
  runScore: (runId: string) => `/api/admin/run/${runId}/score` as const,
  /** GET/PUT /api/admin/config */
  config: () => `/api/admin/config` as const,
  /** GET/href /api/admin/export/:type  (csv export; type ∈ runs|users|consent|feedback) */
  export: (type: string) => `/api/admin/export/${type}` as const,
  /** Convenience for the single hard-coded feedback export (== export('feedback')) */
  exportFeedback: () => `/api/admin/export/feedback` as const,
  /** DELETE /api/admin/feedback/:feedbackId */
  feedback: (feedbackId: number) => `/api/admin/feedback/${feedbackId}` as const,
} as const

export const authRoutes = {
  /**
   * Prefix used by the API client to skip the global 401 redirect on
   * auth-bootstrap calls (e.g. /api/auth/me during startup). This is a plain
   * string const — not a builder — because it is consumed via path.startsWith().
   */
  bootstrapPrefix: '/api/auth/' as const,
  /** GET /api/auth/config */
  config: () => `/api/auth/config` as const,
  /** GET /api/auth/me */
  me: () => `/api/auth/me` as const,
  /** POST /api/auth/login */
  login: () => `/api/auth/login` as const,
  /** POST /api/auth/logout */
  logout: () => `/api/auth/logout` as const,
  /** href /api/saml/login  (full-page redirect to SAML SSO) */
  samlLogin: () => `/api/saml/login` as const,
} as const

export const consentRoutes = {
  /** GET/POST /api/consent */
  consent: () => `/api/consent` as const,
} as const

export const uploadRoutes = {
  /** POST /api/estimate  (cost/time estimate, multipart) */
  estimate: () => `/api/estimate` as const,
  /** POST /api/upload  (file upload, multipart) */
  upload: () => `/api/upload` as const,
} as const

export const wsRoutes = {
  /** WS /ws/run/:id/stream  (live pipeline event stream; passed to getWebSocketUrl) */
  runStream: (id: string) => `/ws/run/${id}/stream` as const,
} as const

/** Aggregate of every route group, for ergonomic single-import access. */
export const routes = {
  run: runRoutes,
  feedback: feedbackRoutes,
  admin: adminRoutes,
  auth: authRoutes,
  consent: consentRoutes,
  upload: uploadRoutes,
  ws: wsRoutes,
} as const

export type Routes = typeof routes

export default routes
