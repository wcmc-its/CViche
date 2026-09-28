import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Upload, FileText, Loader2, Shield, HelpCircle, AlertTriangle } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import type { Estimate } from '../types'
import { getEstimate, uploadFile } from '../api/upload'
import { startRun, getCapacity } from '../api/runs'
import { formatDuration, formatCost } from '../utils'
import ErrorBanner from './ErrorBanner'
import RunHistory from './RunHistory'
import UserMenu from './UserMenu'

type SubmissionType = 'own_cv' | 'authorized_admin'

// Attestation language agreed with Faculty Affairs (John Spiers, 2026-09).
const ATTESTATIONS: Record<SubmissionType, { role: string; text: string }> = {
  own_cv: {
    role: 'I am the faculty member whose CV this is',
    text: 'I agree to upload my CV to this tool.',
  },
  authorized_admin: {
    role: 'I am an administrator uploading on behalf of a faculty member',
    text:
      'I have received permission of the faculty to upload the CV to this tool, and I agree to provide a copy ' +
      'of the modified document to said faculty for their review prior to any submission.',
  },
}

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
  // When the backend flags the upload as a blank/near-blank WCM template, we
  // hold the created (but not-yet-started) run here and require the user to tick
  // an acknowledgement before spending a paid run. null = no warning pending.
  const [pendingWarning, setPendingWarning] = useState<{ runId: string } | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  // The upload succeeded and a run was created, but starting it failed (most
  // often a 429 when the server is at capacity). We hold the existing run id
  // here so the next click RETRIES that run instead of re-uploading -- otherwise
  // each retry mints a brand-new `created` run and a capacity window produces a
  // pile of duplicate "Pending" rows for one file (issue #177). null = nothing
  // to retry; the upload path runs normally.
  const [pendingStart, setPendingStart] = useState<{ runId: string } | null>(null)
  // Output-rendering options (issue #153). Track Changes is always on (Faculty
  // Affairs asked for no opt-out); classification comments default off.
  const [includeClassificationComments, setIncludeClassificationComments] = useState(false)
  const [stripWcmInstructions, setStripWcmInstructions] = useState(true)
  // Per-upload role + attestation (Faculty Affairs requirement). The consent
  // page's choice is only the preselect; every upload records its own.
  const [submissionType, setSubmissionType] = useState<SubmissionType>(
    user?.default_submission_type === 'authorized_admin' ? 'authorized_admin' : 'own_cv',
  )
  const [attested, setAttested] = useState(false)

  // Shared selection path for both the file picker and drag-and-drop.
  const processFile = async (selectedFile: File) => {
    const ext = selectedFile.name.toLowerCase()
    if (ext.endsWith('.docx')) {
      setFile(selectedFile)
      setError(null)
      setEstimate(null)
      // A new file invalidates any pending template warning/acknowledgement and
      // any not-yet-started run left over from a prior failed start.
      setPendingWarning(null)
      setAcknowledged(false)
      setPendingStart(null)

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
      setPendingWarning(null)
      setAcknowledged(false)
      setPendingStart(null)
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

  // Kick off the (paid) pipeline run for an already-uploaded run, then hand off.
  const beginRun = async (runId: string) => {
    await startRun(runId)
    onUploadSuccess(runId)
  }

  const handleUpload = async () => {
    if (!file) return

    // Second click on an acknowledged template warning: the run already exists
    // (it was created on the first click's upload), so just start it. The button
    // stays disabled until the box is ticked, but guard here as well.
    if (pendingWarning) {
      if (!acknowledged) return
      setUploading(true)
      setError(null)
      try {
        await beginRun(pendingWarning.runId)
      } catch (err: any) {
        setError(err.message || 'Failed to start processing. Please try again.')
        console.error(err)
      } finally {
        setUploading(false)
      }
      return
    }

    // A prior upload succeeded but its start failed (e.g. server at capacity).
    // The run already exists, so retry starts THAT run -- we must not re-upload,
    // which would create another duplicate `created` run (issue #177).
    if (pendingStart) {
      setUploading(true)
      setError(null)
      try {
        await beginRun(pendingStart.runId)
      } catch (startErr: any) {
        setError(
          startErr.message ||
            'Your file was uploaded, but processing could not start. Please try again in a moment.',
        )
        console.error(startErr)
      } finally {
        setUploading(false)
      }
      return
    }

    setUploading(true)
    setError(null)

    // Pre-upload capacity check (issue #177): if this pod is already at its
    // concurrency cap, warn and skip the upload rather than minting a `created`
    // run that immediately fails to start. This is ADVISORY -- the start-time
    // gate in beginRun stays authoritative (capacity is per-pod and racy) -- so
    // a probe error must not block the upload: on failure we fall through and
    // let the normal upload + start path (and its retry) handle it.
    try {
      const capacity = await getCapacity()
      if (!capacity.available) {
        setError(
          "The system is temporarily at capacity and can't start a new run " +
            'right now. Any runs already in progress will keep going -- please ' +
            'wait a moment and try again.',
        )
        setUploading(false)
        return
      }
    } catch (capErr) {
      console.error('Capacity probe failed; proceeding with upload', capErr)
    }

    try {
      const data = await uploadFile(file, {
        includeClassificationComments,
        stripWcmInstructions,
        submissionType,
      })
      // Blank-template heuristic tripped: don't start the run yet. Surface the
      // warning and require the acknowledgement checkbox before the next click
      // (which spends a paid run). The upload itself already created the run.
      if (data.wcm_template_warning) {
        setPendingWarning({ runId: data.run_id })
        return
      }
      // The upload succeeded and the run exists. Starting it is a SEPARATE step
      // with its own failure modes (most often HTTP 429 when the server is at
      // capacity). Report those as a start failure, not an upload failure, so
      // the message reflects what actually happened -- the file is already
      // safely uploaded.
      try {
        await beginRun(data.run_id)
      } catch (startErr: any) {
        // Keep a handle to the already-created run so the next click retries it
        // in place rather than re-uploading and minting a duplicate (issue #177).
        setPendingStart({ runId: data.run_id })
        setError(
          startErr.message ||
            'Your file was uploaded, but processing could not start. Please try again in a moment.',
        )
        console.error(startErr)
      }
    } catch (err: any) {
      if (err.message?.includes('consent_required') || err.status === 403) {
        navigate('/consent')
        return
      }
      // The upload itself failed (bad file, storage unavailable, rate limit).
      // No run was created -- surface the server's reason as an upload error.
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

            {/* Output-rendering options (issue #153) */}
            <fieldset className="space-y-2">
              <legend className="block text-sm font-semibold text-gray-900 mb-1">Output options</legend>
              <label className="flex items-start gap-2 cursor-pointer text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={includeClassificationComments}
                  onChange={(e) => setIncludeClassificationComments(e.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
                <span>
                  Include classification comments
                  <span className="block text-xs text-gray-500">Add Word comments explaining how each entry was classified.</span>
                </span>
              </label>
              <label className="flex items-start gap-2 cursor-pointer text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={stripWcmInstructions}
                  onChange={(e) => setStripWcmInstructions(e.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
                <span>
                  Strip WCM template instructions
                  <span className="block text-xs text-gray-500">Remove the WCM CV template&apos;s instructional text (e.g. &quot;When preparing the WCM CV template&hellip;&quot;) from the output. On by default.</span>
                </span>
              </label>
            </fieldset>

            <fieldset className="space-y-2">
              <legend className="block text-sm font-semibold text-gray-900 mb-1">Who is uploading this CV?</legend>
              {(Object.keys(ATTESTATIONS) as SubmissionType[]).map((value) => (
                <label key={value} className="flex items-start gap-2 cursor-pointer text-sm text-gray-700">
                  <input
                    type="radio"
                    name="submission-type"
                    value={value}
                    checked={submissionType === value}
                    onChange={() => { setSubmissionType(value); setAttested(false) }}
                    className="mt-0.5 h-4 w-4 border-gray-300 text-primary-600 focus:ring-primary-500"
                  />
                  <span>{ATTESTATIONS[value].role}</span>
                </label>
              ))}
              <label className="flex items-start gap-2 cursor-pointer text-sm text-gray-700 pt-2">
                <input
                  type="checkbox"
                  checked={attested}
                  onChange={(e) => setAttested(e.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
                <span>
                  {ATTESTATIONS[submissionType].text}{' '}
                  <span className="block text-xs text-gray-500 mt-1">
                    The text of the CV is sent to a third-party AI service (currently Anthropic&apos;s Claude on Amazon
                    Bedrock; the provider may change, for example to OpenAI). CViche attempts to withhold highly
                    sensitive personal details such as date of birth or Social Security number, but you should not
                    include anything you would not want these systems to see.
                  </span>
                  <span className="block text-xs text-gray-500 mt-1">
                    The original CV, intermediate outputs, and final output are retained to improve CViche and test
                    proposed changes. See the{' '}
                    <a href="/help#data-retention" target="_blank" rel="noopener" className="text-primary-600 hover:underline">data retention policy</a>.
                  </span>
                </span>
              </label>
            </fieldset>

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

            {pendingWarning && (
              <section
                className="bg-amber-50 border border-amber-300 rounded-lg p-4"
                role="alert"
                aria-label="Blank template warning"
              >
                <div className="flex items-start gap-3">
                  <AlertTriangle className="h-5 w-5 text-amber-600 flex-shrink-0 mt-0.5" aria-hidden="true" />
                  <div className="text-sm text-amber-800">
                    <p className="font-semibold">This looks like a blank WCM CV template.</p>
                    <p className="mt-1">
                      We didn&apos;t find much filled-in content, so processing it may
                      return a document whose formatting has regressed rather than
                      improved &mdash; and each run has a cost. If you meant to
                      reformat an existing CV or publication list, you can continue.
                    </p>
                    <label className="mt-3 flex items-start gap-2 cursor-pointer font-medium">
                      <input
                        type="checkbox"
                        checked={acknowledged}
                        onChange={(e) => setAcknowledged(e.target.checked)}
                        className="mt-0.5 h-4 w-4 rounded border-amber-400 text-amber-600 focus:ring-amber-500"
                      />
                      <span>I understand and want to process this file anyway.</span>
                    </label>
                  </div>
                </div>
              </section>
            )}

            {error && (
              <ErrorBanner message={error} onDismiss={() => setError(null)} />
            )}

            <button
              onClick={handleUpload}
              disabled={!file || !attested || uploading || estimating || (pendingWarning !== null && !acknowledged)}
              className="w-full bg-primary-600 text-white py-3 px-4 rounded-lg font-semibold hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none"
              style={{ touchAction: 'manipulation' }}
            >
              {uploading ? (
                <span className="flex items-center justify-center gap-2">
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                  Starting Pipeline...
                </span>
              ) : pendingWarning ? (
                'Process Anyway'
              ) : pendingStart ? (
                'Retry Processing'
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