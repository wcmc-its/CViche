import CheckBox from './CheckBox'
import {
  ATTESTATIONS, BATCH_ATTESTATION_LEAD_IN, RETENTION_LEAD, RETENTION_LINK_TEXT, RETENTION_POLICY_HREF,
  RISK_DISCLOSURE,
} from './consentText'
import type { SubmissionType } from './consentText'

interface ConsentSectionProps {
  heading: string
  submissionType: SubmissionType
  /** Files selected; the Role B lead-in appears when it is above one. */
  fileCount: number
  attested: boolean
  onAttestedChange: (value: boolean) => void
}

/** Step 3: the attestation checkbox, then the risk disclosure and the retention
 *  summary directly below it, all in the Counsel-approved wording. */
export default function ConsentSection({ heading, submissionType, fileCount, attested, onAttestedChange }: ConsentSectionProps) {
  const leadIn = submissionType === 'authorized_admin' && fileCount > 1
  return (
    <>
      <h2 className="mt-2 text-base font-semibold text-gray-900">{heading}</h2>
      <CheckBox checked={attested} onChange={onAttestedChange}>
        {leadIn && (
          <span className="block text-[13px] text-gray-600" data-testid="attestation-lead-in">{BATCH_ATTESTATION_LEAD_IN}</span>
        )}
        <span className="block font-medium text-gray-900" data-testid="attestation-text">{ATTESTATIONS[submissionType].text}</span>
      </CheckBox>
      <div className="flex flex-col gap-2 rounded-[10px] border border-sand-200 bg-sand-50 px-4 py-3.5 text-[13px] text-gray-700">
        <p data-testid="risk-disclosure">{RISK_DISCLOSURE}</p>
        <p data-testid="retention-summary">
          {RETENTION_LEAD}
          <a href={RETENTION_POLICY_HREF} target="_blank" rel="noopener" className="text-primary-600 hover:underline">{RETENTION_LINK_TEXT}</a>.
        </p>
      </div>
    </>
  )
}
