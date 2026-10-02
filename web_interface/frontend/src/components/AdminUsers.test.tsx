// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import AdminUsers from './AdminUsers'
import { getAdminConfig, getAdminUsers, updateAdminConfig } from '../api/admin'
import type { AdminUser, SystemConfig } from '../types'

vi.mock('../api/admin', () => ({
  getAdminUsers: vi.fn(), getAdminConfig: vi.fn(), updateAdminConfig: vi.fn(), updateAdminUser: vi.fn(),
}))
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ user: { user_id: 1 } }) }))

const USER: AdminUser = {
  id: 1, email: 'boss@med.cornell.edu', display_name: 'Test Boss', role: 'admin', status: 'active',
  daily_limit: null, monthly_limit: null, runs_today: 0, total_runs: 4, total_cost: 1, feedback_count: 0,
  completed_run_count: 0, last_active_at: null, created_at: null,
}
const CONFIG: SystemConfig = {
  allowed_users: ['boss@med.cornell.edu', 'waiting@example.org'], admin_users: ['boss@med.cornell.edu'],
  rate_limit_daily: 10, rate_limit_monthly: 50, consent_version: '1.0', auth_mode: 'simple',
}
const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })

beforeEach(() => {
  vi.mocked(getAdminUsers).mockResolvedValue([USER])
  vi.mocked(getAdminConfig).mockResolvedValue(CONFIG)
  vi.mocked(updateAdminConfig).mockResolvedValue(undefined)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('AdminUsers (one Users panel)', () => {
  it('shows signed-in accounts and allowed-but-not-signed-in addresses in the same table', async () => {
    render(<AdminUsers />)
    await flush()
    const rows = screen.getAllByRole('row')
    expect(rows.some((r) => r.textContent?.includes('Test Boss'))).toBe(true)
    const pending = rows.find((r) => r.textContent?.includes('waiting@example.org'))!
    expect(within(pending).getByText('Not signed in yet')).toBeTruthy()
    expect(within(pending).getByText('Non-WCM')).toBeTruthy()
    // The signed-in admin appears once, not again as a pending address.
    expect(rows.filter((r) => r.textContent?.includes('boss@med.cornell.edu'))).toHaveLength(1)
  })

  it('adds an address to the allowed list from the top of the panel', async () => {
    render(<AdminUsers />)
    await flush()
    fireEvent.change(screen.getByLabelText('New user email'), { target: { value: ' New@Example.org ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add user' }))
    await flush()
    expect(updateAdminConfig).toHaveBeenCalledWith({ allowed_users: [...CONFIG.allowed_users, 'new@example.org'] })
  })

  it('refuses an address already on the list', async () => {
    render(<AdminUsers />)
    await flush()
    fireEvent.change(screen.getByLabelText('New user email'), { target: { value: 'WAITING@example.org' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add user' }))
    await flush()
    expect(updateAdminConfig).not.toHaveBeenCalled()
    expect(screen.getByText('User already in allowed list.')).toBeTruthy()
  })

  it('removes a pending address from the allowed list', async () => {
    render(<AdminUsers />)
    await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Remove waiting@example.org from the allowed list' }))
    await flush()
    expect(updateAdminConfig).toHaveBeenCalledWith({ allowed_users: ['boss@med.cornell.edu'] })
  })

  it('promotes a pending address through the admin list', async () => {
    render(<AdminUsers />)
    await flush()
    fireEvent.click(screen.getByRole('button', { name: /switch waiting@example.org to Admin/ }))
    await flush()
    expect(updateAdminConfig).toHaveBeenCalledWith({ admin_users: ['boss@med.cornell.edu', 'waiting@example.org'] })
  })

  it('notes that the list only applies to simple login when SSO is on', async () => {
    vi.mocked(getAdminConfig).mockResolvedValue({ ...CONFIG, auth_mode: 'saml' })
    render(<AdminUsers />)
    await flush()
    expect(screen.getByText(/only apply to simple \(email\) login/)).toBeTruthy()
  })
})
