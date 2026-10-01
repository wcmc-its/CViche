// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import RunHistory from './RunHistory'
import { getRuns } from '../api/runs'
import { getBatch, getQueue, listBatches } from '../api/batches'
import type { BatchDetail, BatchSummary, RunSummary, User } from '../types'

vi.mock('../api/runs', () => ({ getRuns: vi.fn(), getRunFilterOptions: vi.fn() }))
vi.mock('../api/batches', () => ({ listBatches: vi.fn(), getBatch: vi.fn(), getQueue: vi.fn() }))

// Invented user; a non-admin, who has no admin filters but does see their own batches.
const MEMBER: User = {
  user_id: 7, email: 'tester@example.org', display_name: 'Test Member', role: 'user',
  consent_version: '1.0', default_submission_type: 'authorized_admin',
}
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ user: MEMBER }), useCanSeeCost: () => false }))

const SUMMARY: BatchSummary = {
  id: 'BQXZKD',
  submitted_by: { id: 7, display_name: 'Test Member', cwid: null, email: null, department: null },
  created_at: '2026-01-02T15:00:00Z',
  run_count: 2,
  files_submitted: 2,
}
const DETAIL: BatchDetail = {
  ...SUMMARY,
  status_counts: { complete: 0, running: 0, queued: 2, failed: 0, cancelled: 0, created: 0 },
  runs: [],
}
const RUN: RunSummary = {
  run_id: 'RUNAAA', filename: 'cv.docx', status: 'queued', started_at: '2026-01-02T15:00:00Z', completed_at: null,
  total_cost: null, total_duration_seconds: null, cv_owner_name: 'Pat Example', batch_id: 'BQXZKD',
  feedback: { count: 0, given_by_me: false, last_at: null, reviewers: null },
}

const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
let search = ''
function Location() {
  search = useLocation().search
  return null
}

async function renderAt(url: string) {
  render(<MemoryRouter initialEntries={[url]}><RunHistory onSelectRun={vi.fn()} /><Location /></MemoryRouter>)
  await flush()
}

beforeEach(() => {
  vi.mocked(getRuns).mockResolvedValue({ runs: [RUN], total: 1, has_more: false })
  vi.mocked(listBatches).mockResolvedValue([SUMMARY])
  vi.mocked(getBatch).mockResolvedValue(DETAIL)
  vi.mocked(getQueue).mockResolvedValue({ dispatch_mode: 'queue', single: null, batch: null })
})
afterEach(() => { cleanup(); vi.resetAllMocks() })

describe('RunHistory batch filter', () => {
  it('shows the batch view for ?batch= with a removable Batch chip', async () => {
    await renderAt('/runs?batch=BQXZKD')
    expect(getBatch).toHaveBeenCalledWith('BQXZKD')
    expect(screen.getByRole('region', { name: 'Batch' })).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
    const chip = screen.getByRole('button', { name: 'Remove Batch filter' }).parentElement!
    expect(within(chip).getByText(/ · 2 CVs$/)).toBeTruthy()
    expect(within(chip).queryByText('BQXZKD')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Remove Batch filter' }))
    await flush()
    expect(search).toBe('')
    expect(screen.getByRole('table')).toBeTruthy()
  })

  it('opens a batch from the Batch tag on a run row', async () => {
    await renderAt('/runs')
    fireEvent.click(screen.getByRole('button', { name: 'Batch' }))
    await flush()
    expect(search).toBe('?batch=BQXZKD')
    expect(getBatch).toHaveBeenCalledWith('BQXZKD')
  })

  it('offers the Batch filter to a non-admin who has batches', async () => {
    await renderAt('/runs')
    expect(screen.getByRole('button', { name: /^Batch:/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Department:/ })).toBeNull()
  })
})
