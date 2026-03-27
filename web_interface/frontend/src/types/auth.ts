export interface User {
  user_id: number
  email: string
  display_name: string
  role: string
  consent_version: string | null
  default_submission_type: string | null
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
}
