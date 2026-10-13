import { useState } from 'react'
import { Check, Loader2 } from 'lucide-react'
import type { CorrectedDocxResult } from '../types'
import type { ApiError } from '../api/client'
import { uploadCorrectedDocx } from '../api/feedback'
import ErrorBanner from './ErrorBanner'
import DropZone from './upload/DropZone'
import { HELP_IMPROVE } from './feedbackQuestions'

/** "Help improve CViche" (#1587): an optional drop target for a corrected
 *  copy of the delivered document, shown beside its Download to the owner,
 *  admins and staff (who may upload for any run). It asks nothing and never
 *  touches the feedback survey. */
export default function CorrectedUpload({ runId }: { runId: string }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<CorrectedDocxResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const upload = async (files: File[]) => {
    setBusy(true)
    setError(null)
    try {
      setResult(await uploadCorrectedDocx(runId, files[0]))
    } catch (e) {
      setError((e as ApiError).message || 'The upload failed. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="corrected-upload-heading">
      <div className="flex items-baseline justify-between gap-3">
        <h3 id="corrected-upload-heading" className="m-0 text-sm font-semibold text-gray-900">{HELP_IMPROVE.heading}</h3>
        <span className="text-xs font-medium text-gray-500">Optional</span>
      </div>
      <p className="mt-1 text-[13px] text-gray-500">
        {HELP_IMPROVE.intro} {HELP_IMPROVE.correctedIntro}
      </p>
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
          <DropZone multiple={false} title={HELP_IMPROVE.correctedTitle} hint={HELP_IMPROVE.correctedHint} onFiles={upload} compact />
        )}
        <p className="mt-2 text-xs text-gray-500">{HELP_IMPROVE.correctedPrivacy}</p>
      </div>
    </section>
  )
}
