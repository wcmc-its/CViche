import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Upload, FileText, Loader2, Shield, HelpCircle } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import type { Estimate } from '../types'
import { getEstimate, uploadFile } from '../api/upload'
import { startRun } from '../api/runs'
import { formatDuration, formatCost } from '../utils'
import ErrorBanner from './ErrorBanner'
import RunHistory from './RunHistory'
import UserMenu from './UserMenu'

interface UploadPageProps {
  onUploadSuccess: (runId: string) => void
}

export default function UploadPage({ onUploadSuccess }: UploadPageProps) {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [estimating, setEstimating] = useState(false)
  const [estimate, setEstimate] = useState<Estimate | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  // Shared selection path for both the file picker and drag-and-drop.
  const processFile = async (selectedFile: File) => {
    const ext = selectedFile.name.toLowerCase()
    if (ext.endsWith('.docx')) {
      setFile(selectedFile)
      setError(null)
      setEstimate(null)

      setEstimating(true)
      try {
        const data = await getEstimate(selectedFile)
        setEstimate(data)
      } catch (err: any) {
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

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0]
    if (selectedFile) processFile(selectedFile)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    if (!isDragging) setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    // Ignore dragleave events fired when moving over child elements.
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setIsDragging(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    const droppedFile = e.dataTransfer.files?.[0]
    if (droppedFile) processFile(droppedFile)
  }

  const handleUpload = async () => {
    if (!file) return

    setUploading(true)
    setError(null)

    try {
      const data = await uploadFile(file)
      await startRun(data.run_id)
      onUploadSuccess(data.run_id)
    } catch (err: any) {
      if (err.message?.includes('consent_required') || err.status === 403) {
        navigate('/consent')
        return
      }
      setError(err.message || 'Failed to upload file. Please try again.')
      console.error(err)
    } finally {
      setUploading(false)
    }
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
      <div className="w-full max-w-3xl">
        {/* Logo */}
        <div className="flex justify-center mb-6">
          <img
            src="/header-logo.png"
            alt="CViche - CV Processing Pipeline"
            className="h-16 object-contain"
          />
        </div>

        <section className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8 relative max-w-md mx-auto">
          <div className="absolute top-4 right-4 flex items-center gap-1">
            <Link
              to="/help"
              aria-label="Help and support"
              title="Help and support"
              className="text-gray-400 hover:text-primary-600 transition-colors"
            >
              <HelpCircle className="h-5 w-5" />
            </Link>
            <UserMenu />
          </div>
          <h1 className="sr-only">Upload CV for Processing</h1>
          <p className="text-gray-600 mb-8 italic">Upload a CV in any format. Get back a document in WCM institutional format.</p>

          <div className="space-y-6">
            <div>
              <label htmlFor="file-upload" className="block text-sm font-semibold text-gray-900 mb-2">
                Upload Your CV
              </label>
              <div
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                className={`border-2 border-dashed rounded-lg p-6 text-center focus-within:border-primary-500 focus-within:ring-2 focus-within:ring-primary-500 transition-colors ${
                  isDragging
                    ? 'border-primary-500 bg-primary-50'
                    : 'border-gray-300 hover:border-primary-500 bg-white'
                }`}
              >
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
                      {file ? file.name : 'Click to select or drag a file here'}
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
                      {formatDuration(estimate.estimated_time_seconds_min)} - {formatDuration(estimate.estimated_time_seconds_max)}
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-gray-600">Estimated cost:</dt>
                    <dd className="font-medium text-success-700">
                      {formatCost(estimate.estimated_cost_min)} - {formatCost(estimate.estimated_cost_max)}
                    </dd>
                  </div>
                </dl>
                <p className="text-xs text-gray-500 mt-3">
                  Cost estimated for {estimate.pricing_model}.
                </p>
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