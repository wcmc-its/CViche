import { useState } from 'react'
import { getConsentPublishPreview, publishConsentVersion } from '../api/admin'
import type { ConsentPublishPreview } from '../types'
import ConsentPublishDialog from './ConsentPublishDialog'

interface ConsentPublishCardProps {
  currentVersion: string
  /** Called after a successful publish, so the page can reload the config. */
  onPublished: (version: string) => void
}

/** Settings card for the consent version: shows it and opens the publish dialog. The
 *  affected-user count is fetched when the dialog opens, so it is current at confirmation. */
export default function ConsentPublishCard({ currentVersion, onPublished }: ConsentPublishCardProps) {
  const [preview, setPreview] = useState<ConsentPublishPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const open = async () => {
    setError(null)
    setLoading(true)
    try {
      setPreview(await getConsentPublishPreview())
    } catch (err: any) {
      setError(err.message || 'Could not load the next consent version.')
    } finally {
      setLoading(false)
    }
  }

  const confirm = async () => {
    if (!preview) return
    setPublishing(true)
    setError(null)
    try {
      const published = await publishConsentVersion(preview.next_version)
      setPreview(null)
      onPublished(published.next_version)
    } catch (err: any) {
      setError(err.message || 'Failed to publish the new version.')
    } finally {
      setPublishing(false)
    }
  }

  return (
    <section className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
      <div className="flex items-baseline justify-between gap-3 mb-4">
        <h3 className="text-sm font-semibold text-gray-900">Consent terms</h3>
        <span className="text-sm text-gray-600">
          Version <strong className="tabular-nums">{currentVersion}</strong>
        </span>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        Publish a new version when the upload terms change. Every user must agree again on their next visit.
      </p>
      <button
        type="button"
        onClick={open}
        disabled={loading}
        className="px-4 py-2 text-sm font-medium text-gray-900 bg-white border border-sand-400 rounded-lg hover:bg-sand-50 disabled:opacity-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        Publish new version…
      </button>
      {error && !preview && <p role="alert" className="mt-3 text-sm text-error-700">{error}</p>}
      {preview && (
        <ConsentPublishDialog
          preview={preview}
          publishing={publishing}
          error={error}
          onConfirm={confirm}
          onClose={() => setPreview(null)}
        />
      )}
    </section>
  )
}
