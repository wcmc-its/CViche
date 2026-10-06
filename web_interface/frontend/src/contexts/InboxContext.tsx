import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ApiError } from '../api/client'
import { discardInboxItem, listInbox } from '../api/inbox'
import type { InboxItem } from '../types'
import { useAuth } from './AuthContext'

interface InboxValue {
  /** Emailed CVs held for the user to confirm. */
  items: InboxItem[]
  /** Re-read the list; call after a submit or discard. Also re-read whenever the tab comes back into view. */
  refresh: () => Promise<void>
  discard: (id: number) => Promise<void>
}

const NO_INBOX: InboxValue = { items: [], refresh: async () => {}, discard: async () => {} }
const InboxContext = createContext<InboxValue>(NO_INBOX)

/** The held-item list, read when a consented user is signed in, when the tab comes back into
 *  view, and on `refresh`. */
export function InboxProvider({ children }: { children: React.ReactNode }) {
  const { user, needsConsent } = useAuth()
  const [items, setItems] = useState<InboxItem[]>([])
  const active = Boolean(user) && !needsConsent

  const refresh = useCallback(async () => {
    if (!active) return
    try {
      setItems(await listInbox())
    } catch (err) {
      console.error('Could not read the emailed-CV inbox', err)
    }
  }, [active])

  useEffect(() => {
    if (active) void refresh()
    else setItems([])
  }, [active, refresh])

  // Email intake holds an item, then marks it submitted seconds later when its run starts, so a
  // list read in between is stale (2026-10-06). Nothing polls: re-read when the user comes back.
  useEffect(() => {
    if (!active) return
    const onVisible = () => { if (document.visibilityState === 'visible') void refresh() }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [active, refresh])

  const discard = useCallback(async (id: number) => {
    try {
      await discardInboxItem(id)
    } catch (err) {
      // 404: already submitted, discarded or expired -- the list was stale, not the user wrong.
      if ((err as ApiError)?.status !== 404) throw err
    }
    await refresh()
  }, [refresh])

  const value = useMemo(() => ({ items, refresh, discard }), [items, refresh, discard])
  return <InboxContext.Provider value={value}>{children}</InboxContext.Provider>
}

export const useInbox = (): InboxValue => useContext(InboxContext)
