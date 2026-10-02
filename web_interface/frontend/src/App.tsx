import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useParams, useLocation } from 'react-router-dom'
import { useAuth } from './contexts/AuthContext'
import { setUnauthorizedHandler } from './api/client'
import UploadPage from './components/UploadPage'
import PipelineViewer from './components/PipelineViewer'
import LoginPage from './components/LoginPage'
import ConsentPage from './components/ConsentPage'
import AppHeader from './components/AppHeader'
import RunsPage from './components/RunsPage'
import AdminDashboard from './components/AdminDashboard'
import HelpPage from './components/HelpPage'
import TermsPage from './components/TermsPage'
import { InboxProvider } from './contexts/InboxContext'
import ErrorBoundary from './components/ErrorBoundary'
import { Loader2 } from 'lucide-react'

/**
 * Wraps the routed views in an error boundary, keyed by pathname so navigating
 * away from a crashed view resets it (a remount on key change clears the error
 * state). Without this, one uncaught render error blanks the entire app.
 */
function RoutedErrorBoundary({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  // BrowserRouter keeps the window's scroll offset across navigations, so a
  // run opened from low in the Runs list landed mid-page. Hash links
  // (#feedback, help anchors) do their own scrolling.
  useEffect(() => {
    if (!location.hash) window.scrollTo(0, 0)
  }, [location.pathname, location.hash])
  return <ErrorBoundary key={location.pathname}>{children}</ErrorBoundary>
}

/**
 * Gate: redirects to /login if not authenticated.
 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (!user) return <Navigate to="/login" replace />

  return <>{children}</>
}

/**
 * Gate: redirects to /consent if user hasn't consented to the current version.
 * Must be nested inside RequireAuth (assumes user is present).
 */
function RequireConsent({ children }: { children: React.ReactNode }) {
  const { needsConsent, consentLoading } = useAuth()

  if (consentLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (needsConsent) {
    return <Navigate to="/consent" replace />
  }

  return <>{children}</>
}

/**
 * Gate: redirects to / if user is not an admin.
 * Must be nested inside RequireAuth (assumes user is present).
 */
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (!user || user.role !== 'admin') {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

/** A signed-in page under the shared top bar. */
function WithHeader({ children }: { children: React.ReactNode }) {
  return (
    <>
      <AppHeader />
      {children}
    </>
  )
}

function UploadRoute() {
  const navigate = useNavigate()
  return (
    <UploadPage onUploadSuccess={(runId) => navigate(`/run/${runId}`)} />
  )
}

function PipelineRoute() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()

  if (!runId) return <Navigate to="/" replace />

  return (
    <PipelineViewer
      runId={runId}
      onBack={() => navigate('/runs')}
      onNavigateToRun={(newRunId) => navigate(`/run/${newRunId}`)}
    />
  )
}

function RedirectIfAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (user) return <Navigate to="/" replace />

  return <>{children}</>
}

/**
 * Consent page route: requires auth but NOT consent (otherwise infinite redirect).
 * If user has already consented, redirect to upload page.
 */
function ConsentRoute() {
  const { needsConsent, consentLoading } = useAuth()

  if (consentLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  // If user already consented, send them to the upload page
  if (!needsConsent) {
    return <Navigate to="/" replace />
  }

  return <ConsentPage />
}

/**
 * Registers a global 401 handler with the API client. When a protected request
 * comes back 401 (session expired mid-use), clear auth state and bounce to
 * /login — which in SAML mode presents the WCM SSO button. Lives inside the
 * router so it can navigate; auth-bootstrap calls are excluded in client.ts.
 */
function AuthErrorHandler() {
  const { clearAuth } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearAuth()
      navigate('/login', { replace: true })
    })
    return () => setUnauthorizedHandler(null)
  }, [clearAuth, navigate])

  return null
}

function App() {
  return (
    <BrowserRouter>
      <AuthErrorHandler />
      <InboxProvider>
      <div className="min-h-screen">
        <RoutedErrorBoundary>
        <Routes>
          <Route
            path="/login"
            element={
              <RedirectIfAuth>
                <LoginPage />
              </RedirectIfAuth>
            }
          />
          <Route
            path="/consent"
            element={
              <RequireAuth>
                <ConsentRoute />
              </RequireAuth>
            }
          />
          <Route
            path="/"
            element={
              <RequireAuth>
                <RequireConsent>
                  <WithHeader>
                    <UploadRoute />
                  </WithHeader>
                </RequireConsent>
              </RequireAuth>
            }
          />
          <Route
            path="/runs"
            element={
              <RequireAuth>
                <RequireConsent>
                  <WithHeader>
                    <RunsPage />
                  </WithHeader>
                </RequireConsent>
              </RequireAuth>
            }
          />
          <Route
            path="/run/:runId"
            element={
              <RequireAuth>
                <RequireConsent>
                  <WithHeader>
                    <PipelineRoute />
                  </WithHeader>
                </RequireConsent>
              </RequireAuth>
            }
          />
          <Route
            path="/help"
            element={
              <RequireAuth>
                <RequireConsent>
                  <WithHeader>
                    <HelpPage />
                  </WithHeader>
                </RequireConsent>
              </RequireAuth>
            }
          />
          <Route
            path="/terms"
            element={
              <RequireAuth>
                <RequireConsent>
                  <WithHeader>
                    <TermsPage />
                  </WithHeader>
                </RequireConsent>
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAuth>
                <RequireAdmin>
                  <WithHeader>
                    <AdminDashboard />
                  </WithHeader>
                </RequireAdmin>
              </RequireAuth>
            }
          />
        </Routes>
        </RoutedErrorBoundary>
      </div>
      </InboxProvider>
    </BrowserRouter>
  )
}

export default App
