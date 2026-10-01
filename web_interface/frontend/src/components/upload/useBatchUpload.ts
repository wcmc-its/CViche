import { useEffect, useRef, useState } from 'react'
import type { ApiError } from '../../api/client'
import { createBatch } from '../../api/batches'
import { startRun } from '../../api/runs'
import { getBatchEstimate, uploadFile } from '../../api/upload'
import {
  MAX_BATCH_FILES, MAX_UPLOADS_IN_FLIGHT, classifyFailure, isValidRow, makeRow, runPool, startFailure,
} from './batchRows'
import type { BatchRow } from './batchRows'
import type { SubmissionType } from './consentText'
import type { Estimate } from '../../types'

export type BatchPhase = 'edit' | 'uploading' | 'done'

export interface BatchSubmitOptions {
  submissionType: SubmissionType
  stripWcmInstructions: boolean
}

const UPLOAD_FAILED_REASON = "Couldn't upload the file"
const BATCH_CREATE_FAILED = "We couldn't create the batch. Please try again."

/** The server refused because the user has not accepted the current consent terms. */
export function isConsentError(err: unknown): boolean {
  const { status, message } = (err ?? {}) as Partial<ApiError>
  return status === 403 || Boolean(message?.includes('consent_required'))
}

type Patch = (key: string, change: Partial<BatchRow>) => void

/** Upload one row (unless an earlier attempt already created its run) and start it.
 *  A start that fails keeps the run id, so a retry re-starts that run and never re-uploads. */
async function sendRow(row: BatchRow, batchId: string, options: BatchSubmitOptions, patch: Patch): Promise<void> {
  patch(row.key, { state: 'uploading', failure: null })
  let runId = row.runId
  if (runId === null) {
    try {
      runId = (await uploadFile(row.file, { ...options, batchId })).run_id
    } catch (err) {
      console.error('Batch file upload failed', err)
      patch(row.key, { state: 'failed', failure: classifyFailure(err, UPLOAD_FAILED_REASON) })
      return
    }
    patch(row.key, { runId })
  }
  try {
    await startRun(runId)
    patch(row.key, { state: 'queued' })
  } catch (err) {
    console.error('Batch run start failed', err)
    patch(row.key, { state: 'failed', failure: startFailure(err) })
  }
}

/** Warn before the tab closes while files are still uploading. */
function useLeaveWarning(active: boolean) {
  useEffect(() => {
    if (!active) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [active])
}

export interface BatchUpload {
  rows: BatchRow[]
  phase: BatchPhase
  batchId: string | null
  /** A valid row's estimate is still pending. */
  estimating: boolean
  error: string | null
  clearError: () => void
  addFiles: (files: File[]) => void
  removeRow: (key: string) => void
  /** Start the table from a file already chosen in the single-file picker. */
  adopt: (file: File, estimate: Estimate | null) => void
  submit: (options: BatchSubmitOptions) => Promise<void>
  retryFailed: (options: BatchSubmitOptions) => Promise<void>
  reset: () => void
}

/** State and wire for a batch: the file rows, one estimate call per add, then
 *  POST /batches and each file's upload + start, two at a time. */
export function useBatchUpload(onConsentRequired: () => void): BatchUpload {
  const [rows, setRows] = useState<BatchRow[]>([])
  const [phase, setPhase] = useState<BatchPhase>('edit')
  const [batchId, setBatchId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const keySeq = useRef(0)
  useLeaveWarning(phase === 'uploading')

  const patch: Patch = (key, change) => setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...change } : r)))
  const patchMany = (keys: Set<string>, change: (r: BatchRow) => Partial<BatchRow>) =>
    setRows((prev) => prev.map((r) => (keys.has(r.key) ? { ...r, ...change(r) } : r)))

  const estimateRows = async (targets: BatchRow[]) => {
    const keys = new Set(targets.map((r) => r.key))
    try {
      const result = await getBatchEstimate(targets.map((r) => r.file))
      const byKey = new Map(targets.map((r, i) => [r.key, result.files[i]]))
      patchMany(keys, (r) => {
        const answer = byKey.get(r.key)
        return answer?.error ? { estimate: null, invalidReason: answer.error } : { estimate: answer?.estimate ?? null }
      })
    } catch (err) {
      console.error('Batch estimate failed', err)
      patchMany(keys, () => ({ estimate: null }))
      setError(`We couldn't estimate these files: ${(err as ApiError)?.message ?? 'request failed'}`)
    }
  }

  const nextKey = () => {
    keySeq.current += 1
    return `row-${keySeq.current}`
  }

  const addFiles = (files: File[]) => {
    const fresh = files.map((file) => makeRow(file, nextKey()))
    // One estimate call holds at most MAX_BATCH_FILES files; any beyond the room left go unestimated.
    const room = Math.max(0, MAX_BATCH_FILES - rows.filter(isValidRow).length)
    const toEstimate = fresh.filter(isValidRow).slice(0, room)
    const estimated = new Set(toEstimate.map((r) => r.key))
    setRows((prev) => [...prev, ...fresh.map((r) => (isValidRow(r) && !estimated.has(r.key) ? { ...r, estimate: null } : r))])
    if (toEstimate.length) void estimateRows(toEstimate)
  }

  const sendAll = async (targets: BatchRow[], id: string, options: BatchSubmitOptions) => {
    setPhase('uploading')
    await runPool(targets, MAX_UPLOADS_IN_FLIGHT, (row) => sendRow(row, id, options, patch))
    setPhase('done')
  }

  const submit = async (options: BatchSubmitOptions) => {
    const valid = rows.filter(isValidRow)
    const keys = new Set(valid.map((r) => r.key))
    setError(null)
    setPhase('uploading')
    patchMany(keys, () => ({ state: 'waiting' }))
    let id: string
    try {
      id = (await createBatch(valid.length)).id
    } catch (err) {
      console.error('Batch creation failed', err)
      patchMany(keys, () => ({ state: 'ready' }))
      setPhase('edit')
      if (isConsentError(err)) onConsentRequired()
      else setError((err as ApiError)?.message || BATCH_CREATE_FAILED)
      return
    }
    setBatchId(id)
    await sendAll(valid, id, options)
  }

  const retryFailed = async (options: BatchSubmitOptions) => {
    if (!batchId) return
    const targets = rows.filter((r) => r.state === 'failed' && r.failure?.retryable)
    patchMany(new Set(targets.map((r) => r.key)), () => ({ state: 'waiting', failure: null }))
    await sendAll(targets, batchId, options)
  }

  const reset = () => {
    setRows([])
    setPhase('edit')
    setBatchId(null)
    setError(null)
  }

  return {
    rows,
    phase,
    batchId,
    estimating: rows.some((r) => isValidRow(r) && r.estimate === undefined),
    error,
    clearError: () => setError(null),
    addFiles,
    removeRow: (key) => setRows((prev) => prev.filter((r) => r.key !== key)),
    adopt: (file, estimate) => setRows([makeRow(file, nextKey(), estimate)]),
    submit,
    retryFailed,
    reset,
  }
}
