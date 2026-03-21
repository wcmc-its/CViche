import { createContext, useContext, useState, useEffect, ReactNode } from 'react'

interface User {
  user_id: number
  email: string
  display_name: string
  role: string
  consent_version: string | null
  default_submission_type: string | null
}

interface ConsentStatus {
  text: string
  version: string
  user_has_consented: boolean
  current_hash: string
}

interface AuthContextType {
  user: User | null
  loading: boolean
  consentStatus: ConsentStatus | null
  consentLoading: boolean
  needsConsent: boolean
  login: (email: string, displayName: string) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
  refreshConsent: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [consentStatus, setConsentStatus] = useState<ConsentStatus | null>(null)
  const [consentLoading, setConsentLoading] = useState(false)

  const refreshUser = async () => {
    try {
      const res = await fetch('/api/auth/me')
      if (res.ok) {
        setUser(await res.json())
      } else {
        setUser(null)
      }
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }

  const refreshConsent = async () => {
    setConsentLoading(true)
    try {
      const res = await fetch('/api/consent')
      if (res.ok) {
        setConsentStatus(await res.json())
      }
    } catch {
      // If consent check fails, don't block -- user will see consent page on next load
    } finally {
      setConsentLoading(false)
    }
  }

  useEffect(() => {
    refreshUser()
  }, [])

  // Fetch consent status whenever user changes
  useEffect(() => {
    if (user) {
      refreshConsent()
    } else {
      setConsentStatus(null)
    }
  }, [user])

  const login = async (email: string, displayName: string) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, display_name: displayName }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail?.message || err.detail || 'Login failed')
    }
    await refreshUser()
  }

  const logout = async () => {
    await fetch('/api/auth/logout', { method: 'POST' })
    setUser(null)
    setConsentStatus(null)
  }

  const needsConsent = !!(
    user &&
    consentStatus &&
    !consentStatus.user_has_consented
  )

  return (
    <AuthContext.Provider value={{
      user,
      loading,
      consentStatus,
      consentLoading,
      needsConsent,
      login,
      logout,
      refreshUser,
      refreshConsent,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
