import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import type { User, ConsentStatus, AuthConfig } from '../types'
import * as authApi from '../api/auth'
import { getConsentStatus as fetchConsentStatus } from '../api/consent'
import { useIdleLogout } from '../hooks/useIdleLogout'

interface AuthContextType {
  user: User | null
  loading: boolean
  authConfig: AuthConfig | null
  consentStatus: ConsentStatus | null
  consentLoading: boolean
  needsConsent: boolean
  login: (email: string, displayName: string) => Promise<void>
  logout: () => Promise<void>
  clearAuth: () => void
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
    // finally, not a plain sequence: logout answers 503 when the server-side
    // session could not be revoked (see auth_routes.logout). The device must
    // still end up signed out in that case -- the error is re-thrown so the
    // caller can surface the failed revocation, but never at the cost of
    // leaving stale auth state behind.
    try {
      await authApi.logout()
    } finally {
      setUser(null)
      setConsentStatus(null)
    }
  }

  // Clear in-memory auth state without calling the server. Used when the
  // session has already expired server-side (a 401 on a protected request),
  // so RequireAuth re-gates and the user is sent back to /login.
  const clearAuth = () => {
    setUser(null)
    setConsentStatus(null)
  }

  // Idle auto-logout: sign out after a period of no interaction. Armed only
  // while signed in. Sessions are stateless long-TTL cookies with no server
  // idle window, so this is enforced client-side. Fall back to clearAuth if the
  // server logout call fails, so the user is signed out locally regardless.
  useIdleLogout(!!user, () => {
    logout().catch(() => clearAuth())
  })

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
      clearAuth,
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

/** Processing cost is admin-only (#1111); the API sends null to everyone else. */
export function useCanSeeCost(): boolean {
  return useAuth().user?.role === 'admin'
}
