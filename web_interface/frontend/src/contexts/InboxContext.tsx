import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { discardInboxItem, listInbox } from '../api/inbox'
import type { InboxItem } from '../types'
import { useAuth } from './AuthContext'

interface InboxValue {
  /** Emailed CVs held for the user to confirm. */
  items: InboxItem[]
  /** Re-read the list; call after a submit or discard (the badge never polls). */
  refresh: () => Promise<void>
  discard: (id: number) => Promise<void>
}

const NO_INBOX: InboxValue = { items: [], refresh: async () => {}, discard: async () => {} }
const InboxContext = createContext<InboxValue>(NO_INBOX)

/** The held-item list, read once when a consented user is signed in and again on `refresh`. */
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

  const discard = useCallback(async (id: number) => {
    await discardInboxItem(id)
    await refresh()
  }, [refresh])

  const value = useMemo(() => ({ items, refresh, discard }), [items, refresh, discard])
  return <InboxContext.Provider value={value}>{children}</InboxContext.Provider>
}

export const useInbox = (): InboxValue => useContext(InboxContext)
