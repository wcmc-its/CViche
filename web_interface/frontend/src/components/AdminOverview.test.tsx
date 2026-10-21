// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AdminOverview from './AdminOverview'
import type { Stats } from '../types'

vi.mock('../api/admin', () => ({
  getAdminRuns: vi.fn().mockResolvedValue({ runs: [] }),
  exportFeedbackCsv: vi.fn().mockResolvedValue({ ok: false }),
}))
vi.mock('../contexts/AuthContext', () => ({ useCanSeeCost: () => false }))

const STATS: Stats = { total_runs: 3, active_users: 1, total_cost: 0, feedback_rate: 0 }
const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })

async function renderWith(stats: Stats) {
  render(<MemoryRouter><AdminOverview stats={stats} onOpenFeedback={() => {}} /></MemoryRouter>)
  await flush()
}

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('AdminOverview rowless-archive count (#802)', () => {
  it('shows the count of uploaded files with no run', async () => {
    await renderWith({ ...STATS, rowless_archives: 2 })
    expect(screen.getByRole('status').textContent).toContain('2 uploaded files are in storage with no run.')
  })

  it('uses the singular for one file', async () => {
    await renderWith({ ...STATS, rowless_archives: 1 })
    expect(screen.getByRole('status').textContent).toContain('1 uploaded file is in storage with no run.')
  })

  it('says storage could not be checked when the count is null, never "none"', async () => {
    await renderWith({ ...STATS, rowless_archives: null })
    expect(screen.getByRole('status').textContent).toBe('Could not check storage for uploaded files with no run.')
  })

  it('shows nothing when there are none, or the backend sent no count', async () => {
    await renderWith({ ...STATS, rowless_archives: 0 })
    expect(screen.queryByRole('status')).toBeNull()
    cleanup()
    await renderWith(STATS)
    expect(screen.queryByRole('status')).toBeNull()
  })
})
