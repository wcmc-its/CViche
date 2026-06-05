import { api } from './client'
import { consentRoutes } from './routes'
import type { ConsentStatus } from '../types'

export async function getConsentStatus(): Promise<ConsentStatus> {
  return api.get<ConsentStatus>(consentRoutes.consent())
}

export async function submitConsent(defaultSubmissionType: string): Promise<void> {
  await api.post(consentRoutes.consent(), { default_submission_type: defaultSubmissionType })
}
