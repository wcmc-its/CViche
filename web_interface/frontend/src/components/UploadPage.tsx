import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, FileText, Loader2, Shield } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import ErrorBanner from './ErrorBanner'
import RunHistory from './RunHistory'

interface UploadPageProps {
  onUploadSuccess: (runId: string) => void
}

interface Estimate {
  document_tokens: number
  text_characters: number
  estimated_cost_min: number
  estimated_cost_max: number
  estimated_time_seconds_min: number
  estimated_time_seconds_max: number
  num_steps: number
  filename: string
  file_size_kb: number
}

export default function UploadPage({ onUploadSuccess }: UploadPageProps) {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [estimating, setEstimating] = useState(false)
  const [estimate, setEstimate] = useState<Estimate | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0]
    if (selectedFile) {
      const ext = selectedFile.name.toLowerCase()
      if (ext.endsWith('.docx')) {
        setFile(selectedFile)
        setError(null)
        setEstimate(null)

        setEstimating(true)
        try {
          const formData = new FormData()
          formData.append('file', selectedFile)

          const response = await fetch('/api/estimate', {
            method: 'POST',
            body: formData,
          })

          if (response.ok) {
            const data = await response.json()
            setEstimate(data)
          }
        } catch (err) {
          console.error('Estimation failed:', err)
        } finally {
          setEstimating(false)
        }
      } else {
        setError('Please select a .docx file')
        setFile(null)
        setEstimate(null)
      }
    }
  }

  const handleUpload = async () => {
    if (!file) return

    setUploading(true)
    setError(null)

    const formData = new FormData()
    formData.append('file', file)

    try {
      const response = await fetch('/api/upload', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        const errData = await response.json().catch(() => null)
        if (errData?.detail?.error === 'consent_required') {
          navigate('/consent')
          return
        }
        throw new Error(errData?.detail?.message || 'Upload failed')
      }

      const data = await response.json()

      await fetch(`/api/run/${data.run_id}/start`, {
        method: 'POST',
      })

      onUploadSuccess(data.run_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload file. Please try again.')
      console.error(err)
    } finally {
      setUploading(false)
    }
  }

  const formatTime = (seconds: number) => {
    if (seconds < 60) return `${seconds}s`
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
  }

  return (
    <main
      className="flex items-center justify-center min-h-screen p-4"
      style={{
        backgroundImage: 'url(/headerbg.png)',
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        backgroundRepeat: 'no-repeat'
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

        <section className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8 relative">
          <h1 className="sr-only">Upload CV for Processing</h1>
          <p className="text-gray-600 mb-8 italic">Upload a CV in any format. Get back a document in WCM institutional format.</p>

          <div className="space-y-6">
            <div>
              <label htmlFor="file-upload" className="block text-sm font-semibold text-gray-900 mb-2">
                Upload Your CV
              </label>
              <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center hover:border-primary-500 focus-within:border-primary-500 focus-within:ring-2 focus-within:ring-primary-500 transition-colors bg-white">
                <input
                  type="file"
                  onChange={handleFileChange}
                  className="sr-only"
                  id="file-upload"
                  aria-describedby="file-type-hint"
                />
                <label htmlFor="file-upload" className="cursor-pointer block">
                  <div className="text-gray-600">
                    {file ? (
                      <FileText className="mx-auto h-12 w-12 text-primary-600" aria-hidden="true" />
                    ) : (
                      <Upload className="mx-auto h-12 w-12 text-gray-400" aria-hidden="true" />
                    )}
                    <p className="mt-2 font-medium">
                      {file ? file.name : 'Click to select a file'}
                    </p>
                    <p className="text-xs text-gray-500 mt-1" id="file-type-hint">
                      .docx only
                    </p>
                  </div>
                </label>
              </div>
            </div>

            {estimating && (
              <div className="bg-primary-50 border border-primary-200 rounded-lg p-4" role="status" aria-live="polite">
                <div className="flex items-center gap-3">
                  <Loader2 className="h-5 w-5 text-primary-600 animate-spin" aria-hidden="true" />
                  <span className="text-primary-700">Analyzing document...</span>
                </div>
              </div>
            )}

            {estimate && !estimating && (
              <section className="bg-gradient-to-br from-primary-50 to-indigo-50 border border-primary-200 rounded-lg p-6" aria-label="Processing estimate">
                <h2 className="font-semibold text-gray-900 mb-3">Processing Estimate</h2>
                <dl className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <dt className="text-gray-600">Document content:</dt>
                    <dd className="font-medium">{estimate.text_characters.toLocaleString()} chars (~{estimate.document_tokens.toLocaleString()} tokens)</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-gray-600">Pipeline steps:</dt>
                    <dd className="font-medium">{estimate.num_steps} stages</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-gray-600">Estimated time:</dt>
                    <dd className="font-medium">
                      {formatTime(estimate.estimated_time_seconds_min)} - {formatTime(estimate.estimated_time_seconds_max)}
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-gray-600">Estimated cost:</dt>
                    <dd className="font-medium text-success-700">
                      ${estimate.estimated_cost_min.toFixed(2)} - ${estimate.estimated_cost_max.toFixed(2)}
                    </dd>
                  </div>
                </dl>
              </section>
            )}

            {error && (
              <ErrorBanner message={error} onDismiss={() => setError(null)} />
            )}

            <button
              onClick={handleUpload}
              disabled={!file || uploading || estimating}
              className="w-full bg-primary-600 text-white py-3 px-4 rounded-lg font-semibold hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none"
              style={{ touchAction: 'manipulation' }}
            >
              {uploading ? (
                <span className="flex items-center justify-center gap-2">
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                  Starting Pipeline...
                </span>
              ) : estimate ? (
                'Start Processing'
              ) : (
                'Start Pipeline'
              )}
            </button>

            {!estimate && !file && (
              <p className="text-xs text-gray-500 text-center">
                Select a file to see processing estimates
              </p>
            )}
          </div>

          {user?.role === 'admin' && (
            <div className="mt-6 pt-4 border-t border-gray-200">
              <button
                onClick={() => navigate('/admin')}
                className="inline-flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-600 transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500 rounded"
              >
                <Shield className="w-3.5 h-3.5" aria-hidden="true" />
                Admin Dashboard
              </button>
            </div>
          )}
        </section>

        {/* Run History — RunHistory returns null when empty, so no wrapper needed */}
        <RunHistory onSelectRun={(runId) => navigate(`/run/${runId}`)} />
      </div>
    </main>
  )
}
