import { AlertTriangle } from 'lucide-react'
import type { Estimate } from '../../types'
import { formatCost, formatDuration } from '../../utils'
import { scannedPagesText } from './batchRows'
import CheckBox from './CheckBox'
import { ATTESTATIONS } from './consentText'
import type { SubmissionType } from './consentText'

export const H2 = 'text-base font-semibold text-gray-900'

const WHO_LABELS: Record<SubmissionType, string> = {
  own_cv: 'My own CV',
  authorized_admin: 'On behalf of faculty',
}

// Help under the toggle without a queue (one file per run either way) ...
const WHO_HELP: Record<SubmissionType, string> = {
  own_cv: 'You are the faculty member whose CV this is.',
  authorized_admin: 'You are an administrator uploading on behalf of a faculty member.',
}
// ... and with one, where "On behalf of faculty" takes a whole batch.
const WHO_HELP_QUEUE: Record<SubmissionType, string> = {
  own_cv: 'One file. The output will be filed under your name.',
  authorized_admin: 'Faculty names are read from each CV, so you can drop a whole folder at once.',
}

interface WhoToggleProps {
  value: SubmissionType
  /** Queue mode: the batch wording ("Whose CVs are these?"). */
  queueMode: boolean
  onChange: (value: SubmissionType) => void
}

/** Step 1: whose CVs these are; sets submission_type. */
export function WhoToggle({ value, queueMode, onChange }: WhoToggleProps) {
  return (
    <>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className={H2} id="who-heading">{queueMode ? '1 · Whose CVs are these?' : '1 · Whose CV is this?'}</h2>
        <div role="radiogroup" aria-labelledby="who-heading" className="flex rounded-lg bg-sand-50 p-[3px]">
          {(['authorized_admin', 'own_cv'] as SubmissionType[]).map((option) => {
            const selected = value === option
            return (
              <label
                key={option}
                className={`cursor-pointer rounded-md px-3 py-1.5 text-[13px] font-medium has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-primary-500 ${
                  selected ? 'bg-white text-gray-900 shadow-[0_1px_2px_rgba(60,40,10,0.12)]' : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                <input
                  type="radio"
                  name="submission-type"
                  value={option}
                  checked={selected}
                  onChange={() => onChange(option)}
                  className="sr-only"
                  aria-label={ATTESTATIONS[option].role}
                />
                {WHO_LABELS[option]}
              </label>
            )
          })}
        </div>
      </div>
      <p className="text-[13px] text-gray-500">{(queueMode ? WHO_HELP_QUEUE : WHO_HELP)[value]}</p>
    </>
  )
}

/** Output options (issue #153); track changes is always on and has no control. */
export function OptionsSection({ heading, strip, onStripChange }: { heading: string; strip: boolean; onStripChange: (v: boolean) => void }) {
  return (
    <fieldset className="mt-2 flex flex-col gap-3">
      <legend className={`${H2} mb-3`}>{heading}</legend>
      <CheckBox checked={strip} onChange={onStripChange}>
        <span className="block font-semibold text-gray-900">Strip WCM template instructions</span>
        <span className="block text-[13px] text-gray-500">Remove the WCM CV template&apos;s instructional text (e.g. &quot;When preparing the WCM CV template&hellip;&quot;) from the output.</span>
      </CheckBox>
    </fieldset>
  )
}

interface TemplateWarningProps {
  acknowledged: boolean
  onAcknowledge: (value: boolean) => void
}

/** The backend flagged the upload as a blank WCM template; starting needs an acknowledgement. */
export function TemplateWarning({ acknowledged, onAcknowledge }: TemplateWarningProps) {
  return (
    <section className="bg-amber-50 border border-amber-300 rounded-lg p-4" role="alert" aria-label="Blank template warning">
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
              onChange={(e) => onAcknowledge(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-amber-400 text-amber-600 focus-visible:ring-amber-500"
            />
            <span>I understand and want to process this file anyway.</span>
          </label>
        </div>
      </div>
    </section>
  )
}

/** The server already ran this exact file; the next click on the footer button runs it again. */
export function DuplicateNotice({ message }: { message: string }) {
  return (
    <section className="bg-amber-50 border border-amber-300 rounded-lg p-4" role="alert" aria-label="Duplicate file notice">
      <div className="flex items-start gap-3">
        <AlertTriangle className="h-5 w-5 text-amber-600 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <p className="text-sm text-amber-800">{message}</p>
      </div>
    </section>
  )
}

/** The single run's estimate block, as before batch upload. */
export function SingleEstimate({ estimate, showCost }: { estimate: Estimate; showCost: boolean }) {
  return (
    <div aria-label="Processing estimate">
      <span className="font-medium text-gray-900">
        Estimated time: {formatDuration(estimate.estimated_time_seconds_min)} - {formatDuration(estimate.estimated_time_seconds_max)}
        {showCost && (
          <>
            {' '}&middot; Estimated cost: {formatCost(estimate.estimated_cost_min)} - {formatCost(estimate.estimated_cost_max)}
          </>
        )}
      </span>
      {showCost && <span className="block text-xs">Cost estimated for {estimate.pricing_model}.</span>}
      {estimate.text_characters_is_guess && (
        <span className="block text-xs text-amber-700">
          We couldn&apos;t read this document&apos;s text, so the {showCost ? 'time and cost' : 'time'} above {showCost ? 'are' : 'is'} a rough guess, not based on its length.
        </span>
      )}
      {!!estimate.scanned_pages?.length && (
        <span className="block text-xs text-amber-700">{scannedPagesText(estimate.scanned_pages)}</span>
      )}
      <span className="block text-xs">
        You don&apos;t need to wait on this page. Processing continues if you close it, and your results will appear in Runs.
      </span>
    </div>
  )
}
