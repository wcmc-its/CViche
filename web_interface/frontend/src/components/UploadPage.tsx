import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { useAuth, useCanSeeCost } from '../contexts/AuthContext'
import type { QueueOverview, QuotaInfo } from '../types'
import ErrorBanner from './ErrorBanner'
import BatchFileTable from './upload/BatchFileTable'
import { DoneCard, UploadingBanner } from './upload/BatchStatus'
import {
  MAX_BATCH_FILES, countText, estimateMinutes, estimateText, finishText, isValidRow, missingItems, quotaText,
  sendProgress, totalsOf,
} from './upload/batchRows'
import ConsentSection from './upload/ConsentSection'
import type { SubmissionType } from './upload/consentText'
import DropZone from './upload/DropZone'
import SingleFileRow from './upload/SingleFileRow'
import UploadFooter from './upload/UploadFooter'
import { H2, OptionsSection, DuplicateNotice, SingleEstimate, TemplateWarning, WhoToggle } from './upload/UploadSections'
import { useBatchUpload } from './upload/useBatchUpload'
import type { BatchUpload } from './upload/useBatchUpload'
import { useSingleFile, useSingleRun } from './upload/useSingleRun'
import type { SingleFile, SingleRun } from './upload/useSingleRun'
import { useUploadContext } from './upload/useUploadContext'

interface BatchFilesProps {
  batch: BatchUpload
  showCost: boolean
  onRunAgain: (key: string) => void
}

/** Step 2 for a batch: heading with "N to submit · M skipped", the multi-file drop zone, the table. */
function BatchFiles({ batch, showCost, onRunAgain }: BatchFilesProps) {
  const editable = batch.phase === 'edit'
  return (
    <>
      <div className={`flex flex-wrap items-baseline justify-between gap-3 ${editable ? 'mt-2' : ''}`}>
        <h2 className={H2}>{editable ? '2 · CV files' : 'Files in this batch'}</h2>
        <div className="flex items-baseline gap-3.5 text-[13px] text-gray-500">
          <span>{countText(batch.rows)}</span>
          {editable && batch.rows.length > 1 && (
            <button type="button" onClick={batch.reset} className="text-primary-700 hover:underline">Clear all</button>
          )}
        </div>
      </div>
      {editable && (
        <DropZone
          multiple
          compact={batch.rows.length > 0}
          title="Drop .docx files here"
          hint={`Up to ${MAX_BATCH_FILES} files. Each one becomes its own run.`}
          onFiles={batch.addFiles}
        />
      )}
      {batch.rows.length > 0 && (
        <BatchFileTable rows={batch.rows} showCost={showCost} editable={editable} onRemove={batch.removeRow} onRunAgain={batch.phase === 'done' ? onRunAgain : undefined} />
      )}
    </>
  )
}

/** Step 2 without a batch: one file, as before. */
function SingleFiles({ single, run }: { single: SingleFile; run: SingleRun }) {
  return (
    <>
      <h2 className={`${H2} mt-2`}>2 &middot; CV file</h2>
      <DropZone
        multiple={false}
        title="Drop a .docx or .pdf file here"
        hint=".docx or .pdf (text-based PDFs work best). One file per run."
        onFiles={(files) => void single.pick(files[0])}
      />
      {single.file && <SingleFileRow file={single.file} disabled={run.uploading} onRemove={single.clear} />}
    </>
  )
}

interface FooterInput {
  multi: boolean
  batch: BatchUpload
  single: SingleFile
  run: SingleRun
  attested: boolean
  isAdmin: boolean
  showCost: boolean
  queue: QueueOverview | null
  quota: QuotaInfo | null
}

/** Batch mode with exactly one valid file and nothing else runs as a single run (design). */
function submitsAsSingle(multi: boolean, batch: BatchUpload): boolean {
  return !multi || (batch.rows.length === 1 && isValidRow(batch.rows[0]))
}

function footerLabel(input: FooterInput, count: number): string {
  const { run, multi, batch } = input
  if (run.uploading) return 'Starting run...'
  if (run.pendingWarning) return 'Process anyway'
  if (run.pendingDuplicate) return 'Run it again'
  if (run.pendingStart) return 'Retry'
  if (multi && batch.estimating) return 'Estimating…'
  return multi && count > 1 ? `Submit ${count} CVs` : 'Start run'
}

/** Everything the footer shows, for the single and the batch flow. */
function footerModel(input: FooterInput) {
  const { multi, batch, single, run, attested, isAdmin, showCost, queue, quota } = input
  const valid = batch.rows.filter(isValidRow)
  const count = multi ? valid.length : single.file ? 1 : 0
  const missing = multi
    ? missingItems({ rows: batch.rows, multi, attested, quota: isAdmin ? null : quota })
    : [...(single.file ? [] : ['Add a CV']), ...(attested ? [] : ['Agree to the upload terms'])]
  const estimating = multi ? batch.estimating : single.estimating
  const totals = multi ? totalsOf(valid) : { minutes: single.estimate ? estimateMinutes(single.estimate) : 0, cost: null }
  const lane = submitsAsSingle(multi, batch) ? queue?.single : queue?.batch
  const hasEstimate = count > 0 && !estimating && (multi || single.estimate !== null)
  const blocked = run.pendingWarning !== null && !run.acknowledged
  return {
    summary: !hasEstimate ? null : multi
      ? <span className="font-semibold text-gray-900">{estimateText(count, totals, showCost)}</span>
      : single.estimate && <SingleEstimate estimate={single.estimate} showCost={showCost} />,
    finish: hasEstimate && queue?.dispatch_mode === 'queue' && lane ? finishText(lane, totals.minutes) : '',
    quota: !isAdmin && quota ? quotaText(quota) : '',
    missing,
    label: footerLabel(input, count),
    busy: run.uploading,
    disabled: missing.length > 0 || estimating || run.uploading || blocked || batch.phase !== 'edit',
  }
}

interface UploadPageProps {
  onUploadSuccess: (runId: string) => void
}

export default function UploadPage({ onUploadSuccess }: UploadPageProps) {
  const showCost = useCanSeeCost()
  const navigate = useNavigate()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const toConsent = () => navigate('/consent')
  const { queue, quota, refreshQueue, refreshQuota } = useUploadContext(isAdmin)
  const [stripWcmInstructions, setStripWcmInstructions] = useState(true)
  // Per-upload role + attestation (Faculty Affairs requirement). The consent
  // page's choice is only the preselect; every upload records its own.
  const [submissionType, setSubmissionType] = useState<SubmissionType>(
    user?.default_submission_type === 'authorized_admin' ? 'authorized_admin' : 'own_cv',
  )
  const [attested, setAttested] = useState(false)
  const [aheadBefore, setAheadBefore] = useState(0)
  const [finishMinutes, setFinishMinutes] = useState<number | null>(null)
  const run = useSingleRun({ stripWcmInstructions, submissionType, onUploadSuccess, onConsentRequired: toConsent })
  const single = useSingleFile(run.reset, run.setError)
  // A file added to or removed from the table forgets any run held for the old
  // selection (template warning or failed start), as picking a new single file does.
  const batch = useBatchUpload(toConsent, run.reset)
  const queueMode = queue?.dispatch_mode === 'queue'
  // Several files only "On behalf of faculty", and only when runs wait in a queue.
  const multi = queueMode && submissionType === 'authorized_admin'
  const editable = batch.phase === 'edit'
  const options = { submissionType, stripWcmInstructions }

  // Switching between one file and a batch carries the first valid file across.
  const chooseWho = (value: SubmissionType) => {
    setSubmissionType(value)
    setAttested(false)
    const nextMulti = queueMode && value === 'authorized_admin'
    if (nextMulti === multi) return
    if (nextMulti && single.file) batch.adopt(single.file, single.estimate)
    const carried = batch.rows.find(isValidRow)
    if (!nextMulti && carried) single.adopt(carried.file, carried.estimate ?? null)
    if (nextMulti) single.clear()
    else batch.reset()
  }

  const afterSend = async () => {
    const overview = await refreshQueue()
    setFinishMinutes(overview?.batch?.est_wait_minutes ?? null)
  }

  const runAgain = async (key: string) => {
    await batch.runAgain(key, options)
    await afterSend()
  }

  const handleStart = async () => {
    if (submitsAsSingle(multi, batch)) {
      const file = multi ? batch.rows[0].file : single.file
      if (file) await run.start(file)
      return
    }
    setAheadBefore(queue?.batch?.ahead ?? 0)
    await batch.submit(options)
    await afterSend()
  }

  const footer = footerModel({ multi, batch, single, run, attested, isAdmin, showCost, queue, quota })
  const fileCount = multi ? batch.rows.length : single.file ? 1 : 0
  const validCount = batch.rows.filter(isValidRow).length

  return (
    <main className="px-4 py-8">
      <div className={`w-full mx-auto ${multi ? 'max-w-[900px]' : 'max-w-[760px]'}`}>
        <h1 className="text-[26px] font-semibold text-gray-900">New run</h1>
        <p className="text-sm text-gray-600 italic mt-1 mb-5">
          {multi
            ? 'Upload CVs as Word documents. Get back documents in WCM institutional format.'
            : 'Upload a CV as a Word document or PDF. Get back a document in WCM institutional format.'}
        </p>

        {batch.phase === 'uploading' && <div className="mb-[18px]"><UploadingBanner progress={sendProgress(batch.rows)} /></div>}
        {batch.phase === 'done' && (
          <div className="mb-[18px]">
            <DoneCard
              progress={sendProgress(batch.rows)}
              aheadBefore={aheadBefore}
              finishMinutes={finishMinutes}
              onViewBatch={() => navigate(`/runs?batch=${batch.batchId}`)}
              onRetry={async () => { await batch.retryFailed(options); await afterSend() }}
              onNewBatch={() => { batch.reset(); setAttested(false); refreshQuota() }}
            />
          </div>
        )}

        <section className="bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] p-5 sm:p-6">
          <div className="flex flex-col gap-4">
            {editable && <WhoToggle value={submissionType} queueMode={queueMode} onChange={chooseWho} />}
            {multi ? <BatchFiles batch={batch} showCost={showCost} onRunAgain={(key) => void runAgain(key)} /> : <SingleFiles single={single} run={run} />}

            {editable && (
              <>
                <OptionsSection
                  heading={multi && validCount > 1 ? `Options for all ${validCount} CVs` : 'Options'}
                  strip={stripWcmInstructions}
                  onStripChange={setStripWcmInstructions}
                />
                <ConsentSection
                  heading="3 · Data handling"
                  submissionType={submissionType}
                  fileCount={fileCount}
                  attested={attested}
                  onAttestedChange={setAttested}
                />

                {!multi && single.estimating && (
                  <div className="bg-primary-50 border border-primary-200 rounded-lg p-4" role="status" aria-live="polite">
                    <div className="flex items-center gap-3">
                      <Loader2 className="h-5 w-5 text-primary-600 animate-spin" aria-hidden="true" />
                      <span className="text-primary-700">Analyzing document...</span>
                    </div>
                  </div>
                )}
                {run.pendingWarning && <TemplateWarning acknowledged={run.acknowledged} onAcknowledge={run.setAcknowledged} />}
                {run.pendingDuplicate && <DuplicateNotice message={run.pendingDuplicate} />}
                {run.error && <ErrorBanner message={run.error} onDismiss={() => run.setError(null)} />}
                {batch.error && <ErrorBanner message={batch.error} onDismiss={batch.clearError} />}

                <UploadFooter {...footer} onStart={() => void handleStart()} />
              </>
            )}
          </div>
        </section>
      </div>
    </main>
  )
}
