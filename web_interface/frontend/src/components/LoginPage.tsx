import { useState, FormEvent, useEffect } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import ErrorBanner from './ErrorBanner'
import { LogIn, Loader2 } from 'lucide-react'

const SAML_ERROR_MESSAGES: Record<string, string> = {
  auth_failed: "Authentication failed. Please try again or contact IT support.",
  missing_attributes: "Your account is missing required information. Contact your IT administrator.",
  saml_not_enabled: "SSO login is not available. Please use the standard sign-in form.",
  not_authorized: "You are not authorized to use CViche. Contact Paul Albert at paa2013@med.cornell.edu to request access.",
  directory_unavailable: "Unable to verify group membership. The directory service may be temporarily unavailable. Please try again in a few minutes.",
}

export default function LoginPage() {
  const { login } = useAuth()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [samlError, setSamlError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Read SAML error from URL query params on mount
  useEffect(() => {
    const errorCode = searchParams.get('error')
    if (errorCode) {
      const message = SAML_ERROR_MESSAGES[errorCode] || "Something went wrong during sign-in. Please try again."
      setSamlError(message)
      // Clean URL -- remove error param so it doesn't persist on refresh
      navigate(window.location.pathname, { replace: true })
    }
  }, [searchParams, navigate])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email.trim(), displayName.trim())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main
      className="flex items-center justify-center min-h-screen p-4"
      style={{
        backgroundImage: 'url(/headerbg.png)',
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        backgroundRepeat: 'no-repeat',
      }}
    >
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="flex justify-center mb-6">
          <img
            src="/header-logo.png"
            alt="CViche - CV Processing Pipeline"
            className="h-16 object-contain"
          />
        </div>

        <section className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8">
          <h1 className="text-xl font-semibold text-gray-900 mb-2">Sign In</h1>
          <p className="text-gray-600 mb-6 italic">
            Enter your name and WCM email to get started.
          </p>

          {samlError && (
            <div className="mb-4">
              <ErrorBanner message={samlError} onDismiss={() => setSamlError(null)} />
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label
                htmlFor="display-name"
                className="block text-sm font-semibold text-gray-900 mb-1"
              >
                Full Name
              </label>
              <input
                id="display-name"
                type="text"
                required
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Jane Smith"
                autoComplete="name"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 placeholder-gray-400 focus:border-primary-500 focus:ring-2 focus:ring-primary-500 focus:outline-none transition-colors"
              />
            </div>

            <div>
              <label
                htmlFor="email"
                className="block text-sm font-semibold text-gray-900 mb-1"
              >
                WCM Email
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="jas9999@med.cornell.edu"
                autoComplete="email"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-gray-900 placeholder-gray-400 focus:border-primary-500 focus:ring-2 focus:ring-primary-500 focus:outline-none transition-colors"
              />
            </div>

            {error && (
              <ErrorBanner message={error} onDismiss={() => setError(null)} />
            )}

            <button
              type="submit"
              disabled={submitting || !displayName.trim() || !email.trim()}
              className="w-full bg-primary-600 text-white py-3 px-4 rounded-lg font-semibold hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none"
              style={{ touchAction: 'manipulation' }}
            >
              {submitting ? (
                <span className="flex items-center justify-center gap-2">
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                  Signing in...
                </span>
              ) : (
                <span className="flex items-center justify-center gap-2">
                  <LogIn className="h-5 w-5" aria-hidden="true" />
                  Sign In
                </span>
              )}
            </button>
          </form>
        </section>
      </div>
    </main>
  )
}
