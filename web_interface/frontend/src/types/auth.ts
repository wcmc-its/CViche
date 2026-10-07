export interface User {
  user_id: number
  /** WCM CWID (SSO identity); null for non-SSO accounts. Keys the directory headshot. */
  cwid?: string | null
  email: string
  display_name: string
  role: string
  consent_version: string | null
  default_submission_type: string | null
  /** Run quota from GET /api/auth/me; null limits mean unlimited. */
  quota?: QuotaInfo | null
  /** Where CVs can be emailed (#1298); null while email intake is off. */
  intake_address?: string | null
}

export interface QuotaInfo {
  daily_limit: number | null
  daily_used: number
  daily_remaining: number | null
  monthly_limit: number | null
  monthly_used: number
  monthly_remaining: number | null
  is_admin: boolean
}

export interface ConsentStatus {
  text: string
  version: string
  user_has_consented: boolean
  current_hash: string
}

export interface AuthConfig {
  mode: 'simple' | 'saml'
  discovery_url?: string
  /** The backend's per-file upload cap in MB (#109). Absent only in the offline fallback config. */
  max_upload_mb?: number
}
