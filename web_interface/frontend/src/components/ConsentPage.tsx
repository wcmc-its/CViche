import { useState } from 'react'
import { Loader2, ShieldCheck } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { submitConsent } from '../api/consent'
import ErrorBanner from './ErrorBanner'
import ConsentTextRenderer from './shared/ConsentTextRenderer'
import UserMenu from './UserMenu'

export default function ConsentPage() {
  const { user, consentStatus, refreshConsent, refreshUser } = useAuth()
  const [submissionType, setSubmissionType] = useState<string>(
    user?.default_submission_type || 'own_cv'
  )
  const [agreed, setAgreed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!agreed) return

    setSubmitting(true)
    setError(null)

    try {
      await submitConsent(submissionType)

      // Refresh both consent status and user data so the app re-evaluates
      await Promise.all([refreshConsent(), refreshUser()])
    } catch (err: any) {
      setError(err.message || 'Failed to submit consent')
    } finally {
      setSubmitting(false)
    }
  }

  if (!consentStatus) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
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
      <div className="w-full max-w-2xl">
        {/* Logo */}
        <div className="flex justify-center mb-6">
          <img
            src="/header-logo.png"
            alt="CViche"
            className="h-16 object-contain"
          />
        </div>

        <form onSubmit={handleSubmit}>
          <section className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8">
            {/* Header */}
            <div className="flex items-center justify-between gap-3 mb-6">
              <div className="flex items-center gap-3 min-w-0">
                <ShieldCheck className="h-6 w-6 text-primary-600 flex-shrink-0" aria-hidden="true" />
                <div className="min-w-0">
                  <h1 className="text-xl font-bold text-gray-900">Consent to Participate</h1>
                  <p className="text-sm text-gray-500">Version {consentStatus.version}</p>
                </div>
              </div>
              <UserMenu />
            </div>

            {/* Consent text -- rendered as prose */}
            <div className="prose prose-sm prose-gray max-w-none mb-8 max-h-96 overflow-y-auto border border-gray-200 rounded-lg p-4 bg-gray-50">
              <ConsentTextRenderer text={consentStatus.text} />
            </div>

            {/* User info (pre-filled, read-only) */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
              <div>
                <label className="block text-sm font-semibold text-gray-900 mb-1">Name</label>
                <input
                  type="text"
                  value={user?.display_name || ''}
                  readOnly
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg bg-gray-50 text-gray-700 cursor-not-allowed"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-900 mb-1">Email</label>
                <input
                  type="text"
                  value={user?.email || ''}
                  readOnly
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg bg-gray-50 text-gray-700 cursor-not-allowed"
                />
              </div>
            </div>

            {/* Authorization role toggle */}
            <div className="mb-6">
              <label className="block text-sm font-semibold text-gray-900 mb-2">
                Authorization Role
              </label>
              <p className="text-sm text-gray-600 mb-3">
                How will you primarily use CViche? You can change this per submission on the upload page.
              </p>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => setSubmissionType('own_cv')}
                  className={`flex-1 py-3 px-4 rounded-lg border-2 text-sm font-medium transition-colors ${
                    submissionType === 'own_cv'
                      ? 'border-primary-600 bg-primary-50 text-primary-700'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                  }`}
                >
                  I am submitting my own CV
                </button>
                <button
                  type="button"
                  onClick={() => setSubmissionType('authorized_admin')}
                  className={`flex-1 py-3 px-4 rounded-lg border-2 text-sm font-medium transition-colors ${
                    submissionType === 'authorized_admin'
                      ? 'border-primary-600 bg-primary-50 text-primary-700'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                  }`}
                >
                  I am submitting on behalf of faculty
                </button>
              </div>
            </div>

            {/* Consent checkbox */}
            <div className="mb-6">
              <label className="flex items-start gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  className="mt-1 h-4 w-4 rounded border-gray-300 text-primary-600 focus-visible:ring-primary-500"
                />
                <span className="text-sm text-gray-700">
                  I have read the information above and consent to participate in the CViche pilot program.
                </span>
              </label>
            </div>

            {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

            {/* Submit button */}
            <button
              type="submit"
              disabled={!agreed || submitting}
              className="w-full bg-primary-600 text-white py-3 px-4 rounded-lg font-semibold hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none flex items-center justify-center gap-2"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                  Submitting...
                </>
              ) : (
                <>
                  <ShieldCheck className="h-5 w-5" aria-hidden="true" />
                  Agree and Continue
                </>
              )}
            </button>
          </section>
        </form>
      </div>
    </main>
  )
}

