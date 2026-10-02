// Counsel-approved upload text ("Re: CViche Launch Readiness Review", 15 Sep
// 2026, signed off 25 Sep 2026). Word for word: any change goes back to
// Counsel first. UploadPage.test.tsx compares the rendered text against the
// approved wording, so an edit here fails the suite.

export type SubmissionType = 'own_cv' | 'authorized_admin'

// Attestation language agreed with Faculty Affairs (John Spiers, 2026-09).
export const ATTESTATIONS: Record<SubmissionType, { role: string; text: string }> = {
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

/** Shown above the unchanged Role B statement when more than one file is selected. */
export const BATCH_ATTESTATION_LEAD_IN = 'This applies to each CV in this batch:'

/** Directly below the attestation checkbox. */
export const RISK_DISCLOSURE =
  "The text of the CV is sent to a third-party AI service (currently Anthropic's Claude on Amazon Bedrock; " +
  'the provider may change, for example to OpenAI). CViche attempts to withhold highly sensitive personal ' +
  'details such as date of birth or Social Security number, but you should not include anything you would ' +
  'not want these systems to see.'

/** Retention summary, rendered as RETENTION_LEAD + link(RETENTION_LINK_TEXT) + '.'. */
export const RETENTION_LEAD =
  'The original CV, intermediate outputs, and final output are retained to improve CViche and test proposed ' +
  'changes. See the '
export const RETENTION_LINK_TEXT = 'data retention policy'
/** The Help page's policy section (HelpPage.tsx section id "data-retention"). */
export const RETENTION_POLICY_HREF = '/help#data-retention'
