import { api } from './client'
import { batchRoutes } from './routes'
import type { BatchDetail, BatchSummary, QueueOverview } from '../types'

export interface CreateBatchOptions {
  /** "Email me when job completes" (#1335): one email once every run of the batch is finished. */
  notifyOnComplete?: boolean
}

/** Create a batch for `filesSubmitted` valid files, before any is uploaded.
 *  429 when it is larger than the caller's remaining run quota. */
export async function createBatch(filesSubmitted: number, options: CreateBatchOptions = {}): Promise<{ id: string }> {
  return api.post<{ id: string }>(batchRoutes.batches(), {
    files_submitted: filesSubmitted,
    notify_on_complete: options.notifyOnComplete ?? false,
  })
}

/** Batches the caller may see (their own; every one for an admin), newest first. */
export async function listBatches(): Promise<BatchSummary[]> {
  const data = await api.get<{ batches: BatchSummary[] }>(batchRoutes.batches())
  return data.batches
}

/** The batch view; 404 for a batch that does not exist or is someone else's. */
export async function getBatch(batchId: string): Promise<BatchDetail> {
  return api.get<BatchDetail>(batchRoutes.detail(batchId))
}

/** Dispatch mode and, in queue mode, each queue's workers, depth and wait. */
export async function getQueue(): Promise<QueueOverview> {
  return api.get<QueueOverview>(batchRoutes.queue())
}
