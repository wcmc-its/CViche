import { api } from './client'
import { inboxRoutes } from './routes'
import type { InboxItem, InboxSubmitResult } from '../types'

/** The caller's emailed CVs waiting for confirmation, oldest first. */
export async function listInbox(): Promise<InboxItem[]> {
  return (await api.get<{ items: InboxItem[] }>(inboxRoutes.list())).items
}

export async function discardInboxItem(id: number): Promise<void> {
  await api.post(inboxRoutes.discard(id))
}

export interface InboxSubmitOptions {
  submissionType: 'own_cv' | 'authorized_admin'
  stripWcmInstructions: boolean
  batchId?: string
  /** Resend of a file the server already ran, after the user agreed to run it again. */
  confirmDuplicate?: boolean
}

/** Create a run (in `created`; the client still starts it) for one held item. A refusal for the
 *  item comes back as status 'failed', not as a thrown error. */
export async function submitInboxItem(id: number, options: InboxSubmitOptions): Promise<InboxSubmitResult> {
  const answer = await api.post<{ results: InboxSubmitResult[] }>(inboxRoutes.submit(), {
    item_ids: [id],
    submission_type: options.submissionType,
    include_track_changes: true,
    include_classification_comments: false,
    strip_wcm_instructions: options.stripWcmInstructions,
    batch_id: options.batchId ?? null,
    confirm_duplicate: options.confirmDuplicate ?? false,
  })
  return answer.results[0]
}
