import { useCallback, useEffect, useState } from 'react'
import { getCurrentUser } from '../../api/auth'
import { getQueue } from '../../api/batches'
import type { QueueOverview, QuotaInfo } from '../../types'

export interface UploadContext {
  /** null until loaded, or when GET /api/queue failed (the page then offers the single-file flow only). */
  queue: QueueOverview | null
  /** Fresh run quota from GET /api/auth/me; null for admins and until loaded. */
  quota: QuotaInfo | null
  /** Re-read the queue; resolves to the new overview, or null when it could not be read. */
  refreshQueue: () => Promise<QueueOverview | null>
}

/** What the New run page reads from the server beyond the file itself: the
 *  dispatch mode and queue wait, and (non-admins) the run quota left. */
export function useUploadContext(isAdmin: boolean): UploadContext {
  const [queue, setQueue] = useState<QueueOverview | null>(null)
  const [quota, setQuota] = useState<QuotaInfo | null>(null)

  const refreshQueue = useCallback(async () => {
    try {
      const overview = await getQueue()
      setQueue(overview)
      return overview
    } catch (err) {
      console.error('Queue overview unavailable; offering the single-file flow', err)
      return null
    }
  }, [])

  useEffect(() => {
    void refreshQueue()
  }, [refreshQueue])

  useEffect(() => {
    if (isAdmin) return
    let cancelled = false
    getCurrentUser()
      .then((me) => { if (!cancelled) setQuota(me.quota ?? null) })
      .catch((err) => console.error('Run quota unavailable; the quota line is hidden', err))
    return () => { cancelled = true }
  }, [isAdmin])

  return { queue, quota, refreshQueue }
}
