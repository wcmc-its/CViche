import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import type { User, ConsentStatus, AuthConfig } from '../types'
import * as authApi from '../api/auth'
import { getConsentStatus as fetchConsentStatus } from '../api/consent'

interface AuthContextType {
  user: User | null
  loading: boolean
  authConfig: AuthConfig | null
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
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null)

  const fetchConfig = async () => {
    try {
      setAuthConfig(await authApi.getAuthConfig())
    } catch {
      setAuthConfig({ mode: 'simple' })
    }
  }

  const refreshUser = async () => {
    try {
      setUser(await authApi.getCurrentUser())
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }

  const refreshConsent = async () => {
    setConsentLoading(true)
    try {
      setConsentStatus(await fetchConsentStatus())
    } catch {
      // If consent check fails, don't block -- user will see consent page on next load
    } finally {
      setConsentLoading(false)
    }
  }

  useEffect(() => {
    fetchConfig()
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
    try {
      await authApi.login(email, displayName)
    } catch (err: any) {
      throw new Error(err.message || 'Login failed')
    }
    await refreshUser()
  }

  const logout = async () => {
    await authApi.logout()
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
      authConfig,
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
