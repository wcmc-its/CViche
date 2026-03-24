import { BrowserRouter, Routes, Route, Navigate, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from './contexts/AuthContext'
import UploadPage from './components/UploadPage'
import PipelineViewer from './components/PipelineViewer'
import LoginPage from './components/LoginPage'
import ConsentPage from './components/ConsentPage'
import AdminDashboard from './components/AdminDashboard'
import HelpPage from './components/HelpPage'
import { Loader2 } from 'lucide-react'

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
      onBack={() => navigate('/')}
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

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-surface-muted">
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
                  <UploadRoute />
                </RequireConsent>
              </RequireAuth>
            }
          />
          <Route
            path="/run/:runId"
            element={
              <RequireAuth>
                <RequireConsent>
                  <PipelineRoute />
                </RequireConsent>
              </RequireAuth>
            }
          />
          <Route
            path="/help"
            element={
              <RequireAuth>
                <RequireConsent>
                  <HelpPage />
                </RequireConsent>
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAuth>
                <RequireAdmin>
                  <AdminDashboard />
                </RequireAdmin>
              </RequireAuth>
            }
          />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App
