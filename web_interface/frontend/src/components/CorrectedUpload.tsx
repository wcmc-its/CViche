import { useCallback, useEffect, useState } from 'react'
import { Check, Loader2 } from 'lucide-react'
import type { CorrectedCopyDetail, CorrectedDocxResult } from '../types'
import type { ApiError } from '../api/client'
import { getCorrectedCopies, getCorrectedCopyUrl, uploadCorrectedDocx } from '../api/feedback'
import { feedbackRoutes } from '../api/routes'
import { formatDate } from '../utils'
import ErrorBanner from './ErrorBanner'
import DropZone from './upload/DropZone'
import { HELP_IMPROVE } from './feedbackQuestions'
import { CORRECTED_COPIES_ANCHOR, uploaderLabel } from './runs/runFeedback'

/** What stands in for a copy's Download link while the malware scan holds it back. */
const HELD_BACK: Record<Exclude<CorrectedCopyDetail['download'], 'ready'>, string> = {
  scanning: HELP_IMPROVE.copyScanning,
  threats_found: HELP_IMPROVE.copyFlagged,
  unscannable: HELP_IMPROVE.copyUnscannable,
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

interface CopyListProps {
  runId: string
  copies: CorrectedCopyDetail[]
  onError: (message: string) => void
}

/** Every reviewer's corrected copy of the run: uploader and role, date, text-edit
 *  and comment counts (#1937: a comments-only copy must not read as nothing), Download. */
function CopyList({ runId, copies, onError }: CopyListProps) {
  const [fetching, setFetching] = useState<number | null>(null)
  // As the original CV's download: ask for the URL first so a refusal (409 still
  // scanning, 403 flagged) shows its message; the bucket has no CORS, so navigate.
  const download = async (copyId: number) => {
    if (fetching !== null) return
    setFetching(copyId)
    try {
      const url = await getCorrectedCopyUrl(runId, copyId)
      window.location.href = url ?? feedbackRoutes.correctedCopy(runId, copyId)
    } catch (e) {
      onError((e as ApiError).message || 'The download failed. Please try again.')
    } finally {
      setFetching(null)
    }
  }
  return (
    <div className="mt-3">
      <h4 className="m-0 text-[13px] font-semibold text-gray-900">{HELP_IMPROVE.copiesHeading}</h4>
      <ul className="mt-1 divide-y divide-sand-200">
        {copies.map((copy) => (
          <li key={copy.id} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1.5 text-[13px]">
            <span className="min-w-0 wrap-break-word text-gray-700">
              {uploaderLabel(copy)}
              <span className="text-gray-500">
                {' · '}{formatDate(copy.uploaded_at)}
                {' · '}{plural(copy.changes, 'text edit', 'text edits')}
                {' · '}{plural(copy.comments, 'comment', 'comments')}
              </span>
            </span>
            {copy.download === 'ready' ? (
              <button
                type="button"
                onClick={() => download(copy.id)}
                disabled={fetching !== null}
                className="font-medium text-primary-700 hover:underline disabled:opacity-60"
              >
                Download
              </button>
            ) : (
              <span className="text-gray-500">{HELD_BACK[copy.download]}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** "Help improve CViche" (#1587): the reviewers' corrected copies of the run,
 *  then an optional drop target for one more, shown beside the delivered
 *  document's Download to the owner, admins and staff (who may upload for any
 *  run). The owner sees staff's copies too. It never touches the feedback survey. */
export default function CorrectedUpload({ runId }: { runId: string }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<CorrectedDocxResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copies, setCopies] = useState<CorrectedCopyDetail[]>([])

  const loadCopies = useCallback(async () => {
    try {
      setCopies(await getCorrectedCopies(runId))
    } catch (e) {
      setError((e as ApiError).message || "We couldn't load the corrected copies.")
    }
  }, [runId])

  useEffect(() => { void loadCopies() }, [loadCopies])

  const upload = async (files: File[]) => {
    setBusy(true)
    setError(null)
    try {
      setResult(await uploadCorrectedDocx(runId, files[0]))
    } catch (e) {
      setError((e as ApiError).message || 'The upload failed. Please try again.')
      return
    } finally {
      setBusy(false)
    }
    await loadCopies()
  }

  const hasOwnCopy = copies.some((copy) => copy.mine)
  return (
    <section id={CORRECTED_COPIES_ANCHOR} aria-labelledby="corrected-upload-heading" className="scroll-mt-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 id="corrected-upload-heading" className="m-0 text-sm font-semibold text-gray-900">{HELP_IMPROVE.heading}</h3>
        <span className="text-xs font-medium text-gray-500">Optional</span>
      </div>
      <p className="mt-1 text-[13px] text-gray-500">
        {HELP_IMPROVE.intro} {HELP_IMPROVE.correctedIntro}
      </p>
      {copies.length > 0 && <CopyList runId={runId} copies={copies} onError={setError} />}
      <div className="mt-3">
        {error && (
          <div className="mb-3">
            <ErrorBanner message={error} onDismiss={() => setError(null)} />
          </div>
        )}
        {busy ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Comparing with the document we delivered...
          </div>
        ) : result ? (
          <p role="status" className="flex items-center gap-2 text-sm font-medium text-success-700">
            <Check className="h-4 w-4" strokeWidth={3} aria-hidden="true" />
            {result.summary}. Thank you.
          </p>
        ) : (
          <DropZone
            multiple={false}
            title={HELP_IMPROVE.correctedTitle}
            hint={hasOwnCopy ? `${HELP_IMPROVE.correctedHint} ${HELP_IMPROVE.correctedReplacesYours}` : HELP_IMPROVE.correctedHint}
            onFiles={upload}
            compact
          />
        )}
        <p className="mt-2 text-xs text-gray-500">{HELP_IMPROVE.correctedPrivacy}</p>
      </div>
    </section>
  )
}
