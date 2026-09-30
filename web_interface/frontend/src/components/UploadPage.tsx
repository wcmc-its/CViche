import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, FileText, Loader2, AlertTriangle, X, Check } from 'lucide-react'
import { useAuth, useCanSeeCost } from '../contexts/AuthContext'
import type { Estimate } from '../types'
import { getEstimate, uploadFile } from '../api/upload'
import { startRun, getCapacity } from '../api/runs'
import { formatDuration, formatCost } from '../utils'
import ErrorBanner from './ErrorBanner'

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

const WHO_LABELS: Record<SubmissionType, string> = {
  own_cv: 'My own CV',
  authorized_admin: 'On behalf of faculty',
}

const WHO_HELP: Record<SubmissionType, string> = {
  own_cv: 'You are the faculty member whose CV this is.',
  authorized_admin: 'You are an administrator uploading on behalf of a faculty member.',
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const H2 = 'text-base font-semibold text-gray-900'

// Drawn checkbox: the native input stays (sr-only) for keyboard and aria.
function CheckBox({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  children: React.ReactNode
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="peer sr-only"
      />
      <span
        aria-hidden="true"
        className={`mt-px flex h-[18px] w-[18px] flex-none items-center justify-center rounded-[4px] border-[1.5px] peer-focus-visible:ring-2 peer-focus-visible:ring-primary-500 peer-focus-visible:ring-offset-1 ${
          checked ? 'border-primary-600 bg-primary-600' : 'border-sand-400 bg-white'
        }`}
      >
        {checked && <Check className="h-3 w-3 text-white" strokeWidth={3.5} />}
      </span>
      <span className="min-w-0">{children}</span>
    </label>
  )
}

interface UploadPageProps {
  onUploadSuccess: (runId: string) => void
}

export default function UploadPage({ onUploadSuccess }: UploadPageProps) {
  const showCost = useCanSeeCost()
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
  // Affairs asked for no opt-out).
  const [stripWcmInstructions, setStripWcmInstructions] = useState(true)
  // Per-upload role + attestation (Faculty Affairs requirement). The consent
  // page's choice is only the preselect; every upload records its own.
  const [submissionType, setSubmissionType] = useState<SubmissionType>(
    user?.default_submission_type === 'authorized_admin' ? 'authorized_admin' : 'own_cv',
  )
  const [attested, setAttested] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const removeFile = () => {
    setFile(null)
    setEstimate(null)
    setPendingWarning(null)
    setAcknowledged(false)
    setPendingStart(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

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


  const startDisabled =
    !file || !attested || uploading || estimating || (pendingWarning !== null && !acknowledged)
  const missing: string[] = []
  if (!file) missing.push('Add a CV')
  if (!attested) missing.push('Agree to the upload terms')

  return (
    <main className="px-4 py-8">
      <div className="w-full max-w-[760px] mx-auto">
        <h1 className="text-[26px] font-semibold text-gray-900">New run</h1>
        <p className="text-sm text-gray-600 italic mt-1 mb-5">Upload a CV as a Word document. Get back a document in WCM institutional format.</p>

        <section className="bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] p-5 sm:p-6">
          <div className="flex flex-col gap-4">
            {/* 1 - Whose CV */}
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className={H2} id="who-heading">1 &middot; Whose CV is this?</h2>
              <div
                role="radiogroup"
                aria-labelledby="who-heading"
                className="flex rounded-lg bg-sand-50 p-[3px]"
              >
                {(['authorized_admin', 'own_cv'] as SubmissionType[]).map((value) => {
                  const selected = submissionType === value
                  return (
                    <label
                      key={value}
                      className={`cursor-pointer rounded-md px-3 py-1.5 text-[13px] font-medium has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-primary-500 ${
                        selected
                          ? 'bg-white text-gray-900 shadow-[0_1px_2px_rgba(60,40,10,0.12)]'
                          : 'text-gray-500 hover:text-gray-700'
                      }`}
                    >
                      <input
                        type="radio"
                        name="submission-type"
                        value={value}
                        checked={selected}
                        onChange={() => { setSubmissionType(value); setAttested(false) }}
                        className="sr-only"
                        aria-label={ATTESTATIONS[value].role}
                      />
                      {WHO_LABELS[value]}
                    </label>
                  )
                })}
              </div>
            </div>
            <p className="text-[13px] text-gray-500">{WHO_HELP[submissionType]}</p>

            {/* 2 - CV file */}
            <h2 className={`${H2} mt-2`}>2 &middot; CV file</h2>
            <div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`relative flex items-center gap-4 rounded-lg border-[1.5px] border-dashed p-5 transition-colors focus-within:ring-2 focus-within:ring-primary-500 ${
                isDragging
                  ? 'border-primary-500 bg-primary-50'
                  : 'border-sand-400 bg-sand-50/40 hover:bg-sand-50 hover:border-[#B8A67F]'
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                onChange={handleFileChange}
                className="sr-only"
                id="file-upload"
                aria-describedby="file-type-hint"
              />
              <div className="flex h-10 w-10 flex-none items-center justify-center rounded-[10px] bg-sand-200 text-[#6B5E45]">
                <Upload className="h-5 w-5" aria-hidden="true" />
              </div>
              <label htmlFor="file-upload" className="block min-w-0 cursor-pointer after:absolute after:inset-0 after:content-['']">
                <span className="block font-medium text-gray-900">
                  Drop a .docx file here or <span className="text-primary-700">browse</span>
                </span>
                <span className="block text-[13px] text-gray-500" id="file-type-hint">
                  .docx only. One file per run.
                </span>
              </label>
            </div>

            {file && (
              <div className="flex items-center gap-3 rounded-lg border border-sand-200 px-3.5 py-3">
                <FileText className="h-5 w-5 flex-none text-primary-600" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-gray-900">{file.name}</div>
                  <div className="text-xs text-gray-500">{formatFileSize(file.size)}</div>
                </div>
                <button
                  type="button"
                  onClick={removeFile}
                  disabled={uploading}
                  aria-label={`Remove ${file.name}`}
                  title="Remove"
                  className="flex h-7 w-7 flex-none items-center justify-center rounded-md text-gray-500 hover:bg-sand-100 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
                >
                  <X className="h-3.5 w-3.5" strokeWidth={2.2} aria-hidden="true" />
                </button>
              </div>
            )}

            {/* 3 - Output options (issue #153) */}
            <fieldset className="mt-2 flex flex-col gap-3">
              <legend className={`${H2} mb-3`}>3 &middot; Output options</legend>
              <CheckBox checked={stripWcmInstructions} onChange={setStripWcmInstructions}>
                <span className="block font-semibold text-gray-900">Strip WCM template instructions</span>
                <span className="block text-[13px] text-gray-500">Remove the WCM CV template&apos;s instructional text (e.g. &quot;When preparing the WCM CV template&hellip;&quot;) from the output. On by default.</span>
              </CheckBox>
            </fieldset>

            {/* 4 - Data handling */}
            <h2 className={`${H2} mt-2`}>4 &middot; Data handling</h2>
            <div className="flex flex-col gap-2 rounded-[10px] border border-sand-200 bg-sand-50 px-4 py-3.5 text-[13px] text-gray-700">
              <p>
                The text of the CV is sent to a third-party AI service: Anthropic&apos;s Claude, running on Amazon
                Bedrock. AWS states that Bedrock does not share CV text or AI output with Anthropic or any other
                model provider, and does not use it to train models. Before the text is sent, CViche removes the
                dates of birth and Social Security numbers it recognizes. It can miss some formats, so you should
                not include anything you would not want these systems to see.
              </p>
              <p>
                The original CV, intermediate outputs, and final output are retained to improve CViche and test
                proposed changes. See the{' '}
                <a href="/help#data-retention" target="_blank" rel="noopener" className="text-primary-600 hover:underline">data retention policy</a>.
              </p>
            </div>

            <CheckBox checked={attested} onChange={setAttested}>
              <span className="block font-medium text-gray-900">{ATTESTATIONS[submissionType].text}</span>
            </CheckBox>

            {estimating && (
              <div className="bg-primary-50 border border-primary-200 rounded-lg p-4" role="status" aria-live="polite">
                <div className="flex items-center gap-3">
                  <Loader2 className="h-5 w-5 text-primary-600 animate-spin" aria-hidden="true" />
                  <span className="text-primary-700">Analyzing document...</span>
                </div>
              </div>
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

            {/* Footer */}
            <div className="mt-1 flex flex-wrap items-center justify-between gap-4 border-t border-sand-200 pt-[18px]">
              <div className="flex min-w-0 flex-col gap-0.5 text-[13px] text-gray-500">
                {estimate && !estimating && (
                  <div aria-label="Processing estimate">
                    <span className="font-medium text-gray-900">
                      Estimated time: {formatDuration(estimate.estimated_time_seconds_min)} - {formatDuration(estimate.estimated_time_seconds_max)}
                      {showCost && (
                        <>
                          {' '}&middot; Estimated cost: {formatCost(estimate.estimated_cost_min)} - {formatCost(estimate.estimated_cost_max)}
                        </>
                      )}
                    </span>
                    {showCost && (
                      <span className="block text-xs">Cost estimated for {estimate.pricing_model}.</span>
                    )}
                    {estimate.text_characters_is_guess && (
                      <span className="block text-xs text-amber-700">
                        We couldn&apos;t read this document&apos;s text, so the {showCost ? 'time and cost' : 'time'} above {showCost ? 'are' : 'is'} a rough guess, not based on its length.
                      </span>
                    )}
                    <span className="block text-xs">
                      You don&apos;t need to wait on this page. Processing continues if you close it, and your results will appear in Runs.
                    </span>
                  </div>
                )}
                {missing.length > 0 && (
                  <ul className="flex flex-col gap-0.5">
                    {missing.map((m) => (
                      <li key={m} className="flex items-center gap-1.5">
                        <span aria-hidden="true" className="h-[5px] w-[5px] rounded-full bg-[#C2410C]" />
                        {m}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <button
                onClick={handleUpload}
                disabled={startDisabled}
                className="rounded-lg bg-primary-600 px-5 py-[11px] font-semibold text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-gray-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 max-sm:w-full"
                style={{ touchAction: 'manipulation' }}
              >
                {uploading ? (
                  <span className="flex items-center justify-center gap-2">
                    <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                    Starting run...
                  </span>
                ) : pendingWarning ? (
                  'Process anyway'
                ) : pendingStart ? (
                  'Retry'
                ) : (
                  'Start run'
                )}
              </button>
            </div>
          </div>
        </section>
      </div>
    </main>
  )
}
