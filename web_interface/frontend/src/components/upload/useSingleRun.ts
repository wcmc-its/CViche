import { useState } from 'react'
import { createBatch } from '../../api/batches'
import { submitInboxItem } from '../../api/inbox'
import { getEstimate, isDuplicateError, uploadFile } from '../../api/upload'
import type { UploadResult } from '../../api/upload'
import type { Estimate } from '../../types'
import { getCapacity, startRun } from '../../api/runs'
import { isConsentError } from './useBatchUpload'
import { MAX_UPLOAD_BYTES, TOO_LARGE_REASON } from './batchRows'
import type { HeldFile } from './batchRows'
import type { SubmissionType } from './consentText'

interface SingleRunOptions {
  stripWcmInstructions: boolean
  submissionType: SubmissionType
  /** "Email me when job completes" (#1335): the run goes into a one-file batch that asks for the email. */
  notifyOnComplete: boolean
  onUploadSuccess: (runId: string) => void
  onConsentRequired: () => void
}

export interface SingleRun {
  uploading: boolean
  error: string | null
  setError: (message: string | null) => void
  pendingWarning: { runId: string } | null
  acknowledged: boolean
  setAcknowledged: (value: boolean) => void
  pendingStart: { runId: string } | null
  /** The server already ran this exact file (#1286); its message, held until the user re-submits to run it again. */
  pendingDuplicate: string | null
  /** Upload `file` and start it; or, with a run already held, start that run. */
  start: (file: File, held?: HeldFile | null) => Promise<void>
  /** Forget any held run (a new file was chosen or the file was removed). */
  reset: () => void
}

const INBOX_REFUSED = "Couldn't submit the emailed file. Please try again."
const START_FAILED =
  'Your file was uploaded, but processing could not start. Please try again in a moment.'
const AT_CAPACITY =
  "The system is temporarily at capacity and can't start a new run " +
  'right now. Any runs already in progress will keep going -- please ' +
  'wait a moment and try again.'

/** The single-run flow (one file; no batch unless the completion email is asked for): upload, then start, then hand off
 *  to the progress page. Moved unchanged from UploadPage. */
export function useSingleRun({
  stripWcmInstructions, submissionType, notifyOnComplete, onUploadSuccess, onConsentRequired,
}: SingleRunOptions): SingleRun {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
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
  // The upload was refused as a duplicate of an earlier run, so NO run exists.
  // The next click re-uploads with confirm_duplicate set. null = no duplicate pending.
  const [pendingDuplicate, setPendingDuplicate] = useState<string | null>(null)

  // Kick off the (paid) pipeline run for an already-uploaded run, then hand off.
  const beginRun = async (runId: string) => {
    await startRun(runId)
    onUploadSuccess(runId)
  }

  // Start a run that already exists (acknowledged template warning, or a
  // retry after a failed start) -- never re-upload, which would create a
  // duplicate `created` run (issue #177).
  const restartHeld = async (runId: string, fallback: string) => {
    setUploading(true)
    setError(null)
    try {
      await beginRun(runId)
    } catch (err: any) {
      setError(err.message || fallback)
      console.error(err)
    } finally {
      setUploading(false)
    }
  }

  // Pre-upload capacity check (issue #177): ADVISORY -- the start-time gate in
  // beginRun stays authoritative (capacity is per-pod and racy) -- so a probe
  // error must not block the upload. Returns false when the pod is full.
  const hasCapacity = async (): Promise<boolean> => {
    try {
      const capacity = await getCapacity()
      if (!capacity.available) {
        setError(AT_CAPACITY)
        return false
      }
    } catch (capErr) {
      console.error('Capacity probe failed; proceeding with upload', capErr)
    }
    return true
  }

  /** Create the run: upload the file, or submit the emailed CV held on the server (#1298). No batch,
   *  unless "Email me when job completes" is ticked: then a one-file batch that asks for the email (#1335).
   *  A refused batch (consent, quota) throws like a refused upload. */
  const createRun = async (file: File, held: HeldFile | null, confirmDuplicate: boolean): Promise<UploadResult> => {
    const batchId = notifyOnComplete ? (await createBatch(1, { notifyOnComplete: true })).id : undefined
    if (!held) return uploadFile(file, { stripWcmInstructions, submissionType, confirmDuplicate: confirmDuplicate || undefined, batchId })
    const result = await submitInboxItem(held.id, { stripWcmInstructions, submissionType, confirmDuplicate, batchId })
    if (result.status === 'submitted' && result.run_id) {
      return { run_id: result.run_id, wcm_template_warning: false, wcm_template_match_ratio: null }
    }
    // Same shape /upload's refusal has, so the catch below treats a duplicate the same way.
    throw { status: result.error === 'duplicate_file' ? 409 : 400, code: result.error ?? undefined, message: result.message ?? INBOX_REFUSED }
  }

  const uploadAndStart = async (file: File, held: HeldFile | null, confirmDuplicate: boolean) => {
    const data = await createRun(file, held, confirmDuplicate)
    // Blank-template heuristic tripped: don't start the run yet. Surface the
    // warning and require the acknowledgement checkbox before the next click
    // (which spends a paid run). The upload itself already created the run.
    if (data.wcm_template_warning) {
      setPendingWarning({ runId: data.run_id })
      return
    }
    // Starting is a SEPARATE step with its own failure modes (most often HTTP
    // 429 at capacity). Report those as a start failure: the file is already
    // safely uploaded, and the held run id makes the next click a retry.
    try {
      await beginRun(data.run_id)
    } catch (startErr: any) {
      setPendingStart({ runId: data.run_id })
      setError(startErr.message || START_FAILED)
      console.error(startErr)
    }
  }

  const start = async (file: File, held: HeldFile | null = null) => {
    if (pendingWarning) {
      if (acknowledged) await restartHeld(pendingWarning.runId, 'Failed to start processing. Please try again.')
      return
    }
    if (pendingStart) {
      await restartHeld(pendingStart.runId, START_FAILED)
      return
    }
    const confirmDuplicate = pendingDuplicate !== null
    setPendingDuplicate(null)
    setUploading(true)
    setError(null)
    if (!(await hasCapacity())) {
      setUploading(false)
      return
    }
    try {
      await uploadAndStart(file, held, confirmDuplicate)
    } catch (err: any) {
      if (isDuplicateError(err)) {
        setPendingDuplicate(err.message)
        return
      }
      if (isConsentError(err)) {
        onConsentRequired()
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

  const reset = () => {
    setPendingWarning(null)
    setAcknowledged(false)
    setPendingStart(null)
    setPendingDuplicate(null)
  }

  return { uploading, error, setError, pendingWarning, acknowledged, setAcknowledged, pendingStart, pendingDuplicate, start, reset }
}

export interface SingleFile {
  file: File | null
  estimate: Estimate | null
  estimating: boolean
  /** Choose a file: a .docx or .pdf is kept and estimated, anything else is refused. */
  pick: (file: File) => Promise<void>
  /** Take a file whose estimate is already known (carried over from the batch table). */
  adopt: (file: File, estimate: Estimate | null) => void
  /** The emailed CV chosen for this run (#1298), whose bytes are on the server; `file` is a name-only placeholder. */
  held: HeldFile | null
  adoptHeld: (item: HeldFile) => void
  clear: () => void
}

/** The single run's chosen file and its estimate. `onChange` runs whenever the
 *  file changes, so a held run from the previous file is forgotten; `onRefused`
 *  gets the message for a file of the wrong type. */
export function useSingleFile(onChange: () => void, onRefused: (message: string | null) => void): SingleFile {
  const [file, setFile] = useState<File | null>(null)
  const [estimate, setEstimate] = useState<Estimate | null>(null)
  const [estimating, setEstimating] = useState(false)
  const [held, setHeld] = useState<HeldFile | null>(null)

  const clear = () => {
    setHeld(null)
    setFile(null)
    setEstimate(null)
    onChange()
  }

  const pick = async (selected: File) => {
    const name = selected.name.toLowerCase()
    if (!name.endsWith('.docx') && !name.endsWith('.pdf')) {
      onRefused('Please select a .docx or .pdf file')
      clear()
      return
    }
    if (selected.size > MAX_UPLOAD_BYTES) {
      onRefused(`${TOO_LARGE_REASON}, so it won't be submitted`)
      clear()
      return
    }
    setHeld(null)
    setFile(selected)
    onRefused(null)
    setEstimate(null)
    onChange()
    setEstimating(true)
    try {
      setEstimate(await getEstimate(selected))
    } catch (err) {
      console.error('Estimation failed:', err)
    } finally {
      setEstimating(false)
    }
  }

  const adoptHeld = (item: HeldFile) => {
    setHeld(item)
    setFile(new File([], item.filename))
    setEstimate(null)
    onChange()
  }

  const adopt = (selected: File, known: Estimate | null) => {
    setHeld(null)
    setFile(selected)
    setEstimate(known)
    onChange()
  }

  return { file, estimate, estimating, pick, adopt, held, adoptHeld, clear }
}
