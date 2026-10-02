// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AppHeader from './AppHeader'
import { InboxProvider } from '../contexts/InboxContext'
import { listInbox } from '../api/inbox'
import { clearViewport, mockViewport } from '../hooks/mockViewport'
import type { User } from '../types'

const MEMBER: User = {
  user_id: 7, email: 'tester@example.org', display_name: 'Test Member', role: 'user',
  consent_version: '1.0', default_submission_type: 'authorized_admin',
}
let currentUser: User = MEMBER
vi.mock('../api/inbox', () => ({ listInbox: vi.fn(), discardInboxItem: vi.fn() }))
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ user: currentUser, logout: vi.fn() }) }))

const renderHeader = () => render(<MemoryRouter initialEntries={['/runs']}><InboxProvider><AppHeader /></InboxProvider></MemoryRouter>)
beforeEach(() => { vi.mocked(listInbox).mockResolvedValue([]) })
afterEach(() => { cleanup(); clearViewport(); currentUser = MEMBER; vi.resetAllMocks() })

describe('AppHeader narrow menu', () => {
  it('keeps the tabs and Help link on a wide screen, with no menu button', () => {
    mockViewport(480)
    renderHeader()
    expect(screen.queryByRole('button', { name: 'Main menu' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Runs' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Help and support' })).toBeTruthy()
  })

  it('below 480px folds the tabs and Help into a menu button', () => {
    mockViewport(479)
    renderHeader()
    expect(screen.queryByRole('link', { name: 'Runs' })).toBeNull()
    const button = screen.getByRole('button', { name: 'Main menu' })
    expect(button.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(button)
    expect(button.getAttribute('aria-expanded')).toBe('true')
    expect(['Runs', 'New run', 'Help'].every((name) => screen.getByRole('link', { name }))).toBe(true)
    expect(screen.queryByRole('link', { name: 'Dashboard' })).toBeNull()
  })

  it('lists Dashboard for an admin, moves focus into the menu, and Escape closes it back to the button', () => {
    mockViewport(360)
    currentUser = { ...MEMBER, role: 'admin' }
    renderHeader()
    const button = screen.getByRole('button', { name: 'Main menu' })
    fireEvent.click(button)
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeTruthy()
    expect(document.activeElement).toBe(screen.getByRole('link', { name: 'Runs' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(button.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('link', { name: 'Dashboard' })).toBeNull()
    expect(document.activeElement).toBe(button)
  })

  it('closes after picking a link', () => {
    mockViewport(360)
    renderHeader()
    fireEvent.click(screen.getByRole('button', { name: 'Main menu' }))
    fireEvent.click(screen.getByRole('link', { name: 'New run' }))
    expect(screen.getByRole('button', { name: 'Main menu' }).getAttribute('aria-expanded')).toBe('false')
  })
})

describe('AppHeader emailed-CV badge (#1298)', () => {
  const held = (id: number) => ({ id, filename: `f${id}.docx`, size_bytes: 1, received_at: '2026-10-01T00:00:00Z', duplicate: null })
  const settle = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })

  it('shows the held count on New run, and nothing at zero', async () => {
    mockViewport(480)
    renderHeader()
    await settle()
    expect(screen.queryByRole('status')).toBeNull()
    cleanup()
    vi.mocked(listInbox).mockResolvedValue([held(1), held(2)])
    renderHeader()
    await settle()
    expect(screen.getByRole('status', { name: '2 emailed CVs waiting' }).textContent).toBe('2')
    expect(screen.getByRole('link', { name: /New run/ }).textContent).toContain('2')
  })

  it('shows the count on New run in the narrow menu', async () => {
    mockViewport(360)
    vi.mocked(listInbox).mockResolvedValue([held(1)])
    renderHeader()
    await settle()
    fireEvent.click(screen.getByRole('button', { name: 'Main menu' }))
    expect(screen.getByRole('status', { name: '1 emailed CV waiting' })).toBeTruthy()
  })
})
