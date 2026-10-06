// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { InboxProvider, useInbox } from './InboxContext'
import { discardInboxItem, listInbox } from '../api/inbox'
import type { InboxItem } from '../types'

vi.mock('../api/inbox', () => ({ listInbox: vi.fn(), discardInboxItem: vi.fn() }))
vi.mock('./AuthContext', () => ({ useAuth: () => ({ user: { user_id: 7 }, needsConsent: false }) }))

const held = (id: number) => ({ id, filename: `cv${id}.pdf`, size_bytes: 1, received_at: '2026-10-06T15:36:06Z' }) as unknown as InboxItem

let discardOf: ((id: number) => Promise<void>) | undefined
function Probe() {
  const inbox = useInbox()
  discardOf = inbox.discard
  return <span data-testid="count">{inbox.items.length}</span>
}
const count = () => screen.getByTestId('count').textContent

beforeEach(() => { vi.mocked(listInbox).mockResolvedValue([held(1), held(2)]) })
afterEach(() => { cleanup(); vi.resetAllMocks(); discardOf = undefined })

describe('InboxProvider', () => {
  it('re-reads the list when the window regains focus (intake submits held items seconds later)', async () => {
    render(<InboxProvider><Probe /></InboxProvider>)
    await waitFor(() => expect(count()).toBe('2'))
    vi.mocked(listInbox).mockResolvedValue([])
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await waitFor(() => expect(count()).toBe('0'))
  })

  it('treats a 404 on discard as a stale list: no error, list re-read', async () => {
    render(<InboxProvider><Probe /></InboxProvider>)
    await waitFor(() => expect(count()).toBe('2'))
    vi.mocked(discardInboxItem).mockRejectedValue({ status: 404, message: 'Inbox item not found' })
    vi.mocked(listInbox).mockResolvedValue([held(2)])
    await act(async () => { await discardOf!(1) })
    expect(count()).toBe('1')
  })

  it('still surfaces a discard failure that is not a 404', async () => {
    render(<InboxProvider><Probe /></InboxProvider>)
    await waitFor(() => expect(count()).toBe('2'))
    vi.mocked(discardInboxItem).mockRejectedValue({ status: 500, message: 'boom' })
    await expect(discardOf!(1)).rejects.toMatchObject({ status: 500 })
  })
})
