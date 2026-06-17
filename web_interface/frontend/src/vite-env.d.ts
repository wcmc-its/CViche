/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_API_URL: string
  // Minutes of inactivity before the client auto-logs-out (see useIdleLogout).
  // Optional; defaults to 20 when unset or invalid.
  readonly VITE_IDLE_TIMEOUT_MINUTES?: string
}
interface ImportMeta {
  readonly env: ImportMetaEnv
}
