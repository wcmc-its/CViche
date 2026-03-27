import { api } from './client'
import type { ConsentStatus } from '../types'

export async function getConsentStatus(): Promise<ConsentStatus> {
  return api.get<ConsentStatus>('/api/consent')
}

export async function submitConsent(defaultSubmissionType: string): Promise<void> {
  await api.post('/api/consent', { default_submission_type: defaultSubmissionType })
}
