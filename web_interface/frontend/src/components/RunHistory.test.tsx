// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import RunHistory from './RunHistory'
import { getMyStatusCounts, getRunFilterOptions, getRuns } from '../api/runs'
import { getBatch, getQueue, listBatches } from '../api/batches'
import { clearViewport, mockViewport } from '../hooks/mockViewport'
import type { BatchDetail, BatchSummary, RunFilterOptions, RunSummary, User } from '../types'

vi.mock('../api/runs', () => ({ getRuns: vi.fn(), getRunFilterOptions: vi.fn(), getMyStatusCounts: vi.fn() }))
vi.mock('../api/batches', () => ({ listBatches: vi.fn(), getBatch: vi.fn(), getQueue: vi.fn() }))

// Invented user; a non-admin, who has no admin filters but does see their own batches.
const MEMBER: User = {
  user_id: 7, email: 'tester@example.org', display_name: 'Test Member', role: 'user',
  consent_version: '1.0', default_submission_type: 'authorized_admin',
}
const ADMIN: User = { ...MEMBER, user_id: 1, role: 'admin' }
let currentUser: User = MEMBER
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: currentUser }),
  useCanSeeCost: () => false,
  useCanViewAllRuns: () => currentUser.role === 'admin' || currentUser.role === 'staff',
}))

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
  currentUser = MEMBER
  vi.mocked(getRuns).mockResolvedValue({ runs: [RUN], total: 1, has_more: false })
  vi.mocked(getMyStatusCounts).mockResolvedValue({ all: 5, running: 1, awaiting_feedback: 2, failed: 0, red: 0 })
  vi.mocked(listBatches).mockResolvedValue([SUMMARY])
  vi.mocked(getBatch).mockResolvedValue(DETAIL)
  vi.mocked(getQueue).mockResolvedValue({ dispatch_mode: 'queue', single: null, batch: null, completion_email_available: false })
})
afterEach(() => { cleanup(); clearViewport(); vi.resetAllMocks() })

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

const OPTIONS: RunFilterOptions = {
  departments: [], faculty: [], run_by: [], self_count: 0, on_behalf_count: 0,
  status: { all: 9, running: 2, awaiting_feedback: 4, failed: 1, red: 3 },
  feedback: { given: 0, needed: 4 }, input_format: { wcm: 0, other: 0, unknown: 0 },
}
const pill = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) })
const lastParams = () => vi.mocked(getRuns).mock.lastCall![2]

describe('RunHistory status pills', () => {
  it('shows a member the pills that apply to them, with counts of their own runs and no Red score', async () => {
    await renderAt('/runs')
    const group = screen.getByRole('group', { name: 'Show runs' })
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(
      ['All faculty5', 'Running1', 'Awaiting feedback2', 'Had failures0'])
    expect(getRunFilterOptions).not.toHaveBeenCalled()
    // Members get no Input format (or any other admin) filter.
    expect(screen.queryByRole('button', { name: /^Input format:/ })).toBeNull()
    expect(pill('All faculty').getAttribute('aria-pressed')).toBe('true')
  })

  it('a member picking Running lists their own running runs; Awaiting feedback swaps it for feedback=needed', async () => {
    await renderAt('/runs')
    fireEvent.click(pill('Running'))
    await flush()
    expect(search).toBe('?status=running')
    expect(lastParams()).toEqual({ status: 'running' })
    fireEvent.click(pill('Awaiting feedback'))
    await flush()
    expect(search).toBe('?feedback=needed')
    expect(lastParams()).toEqual({ feedback: 'needed' })
    expect(pill('Awaiting feedback').getAttribute('aria-pressed')).toBe('true')
  })

  it('ignores ?status=red for a member', async () => {
    await renderAt('/runs?status=red')
    expect(lastParams()).toEqual({})
  })

  it('shows an admin the Input format filter', async () => {
    currentUser = ADMIN
    vi.mocked(getRunFilterOptions).mockResolvedValue(OPTIONS)
    await renderAt('/runs')
    expect(screen.getByRole('button', { name: /^Input format:/ })).toBeTruthy()
    expect(getMyStatusCounts).not.toHaveBeenCalled()
  })

  it('shows an admin all five pills with counts and sends the pill with the other filters', async () => {
    currentUser = ADMIN
    vi.mocked(getRunFilterOptions).mockResolvedValue(OPTIONS)
    await renderAt('/runs?department=Medicine')
    const group = screen.getByRole('group', { name: 'Show runs' })
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(
      ['All faculty9', 'Running2', 'Awaiting feedback4', 'Had failures1', 'Red score3'])
    fireEvent.click(pill('Red score'))
    await flush()
    expect(search).toBe('?department=Medicine&status=red')
    expect(lastParams()).toEqual({ scope: 'all', department: 'Medicine', status: 'red' })
    expect(vi.mocked(getRunFilterOptions).mock.lastCall![0]).toEqual(lastParams())
    // The Department filter keeps its chip; the pill has none.
    expect(screen.getByRole('button', { name: 'Remove Department filter' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Remove Status/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    await flush()
    expect(search).toBe('')
  })

  it('labels the On their behalf Run by value in its chip', async () => {
    currentUser = ADMIN
    vi.mocked(getRunFilterOptions).mockResolvedValue(OPTIONS)
    await renderAt('/runs?run_by=on_behalf')
    expect(lastParams()).toEqual({ scope: 'all', run_by: 'on_behalf' })
    const chip = screen.getByRole('button', { name: 'Remove Run by filter' }).parentElement!
    expect(within(chip).getByText('On their behalf')).toBeTruthy()
  })
})

describe('RunHistory narrow Filters panel', () => {
  const asAdmin = async (url: string, width: number) => {
    currentUser = ADMIN
    mockViewport(width)
    vi.mocked(getRunFilterOptions).mockResolvedValue(OPTIONS)
    await renderAt(url)
  }

  it('shows the five combos in a row from 1024px up, with no Filters button', async () => {
    await asAdmin('/runs?department=Medicine', 1024)
    expect(screen.queryByRole('button', { name: /^Filters/ })).toBeNull()
    expect(screen.getByRole('button', { name: /^Department:/ })).toBeTruthy()
  })

  it('below 1024px swaps them for one Filters (n) button that opens the combos', async () => {
    await asAdmin('/runs?department=Medicine&batch=BQXZKD', 1023)
    expect(screen.queryByRole('button', { name: /^Department:/ })).toBeNull()
    const button = screen.getByRole('button', { name: 'Filters (2)' })
    expect(button.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(button)
    expect(button.getAttribute('aria-expanded')).toBe('true')
    const panel = screen.getByRole('group', { name: 'Filters' })
    for (const label of ['Department', 'Faculty', 'Run by', 'Feedback', 'Input format', 'Batch']) {
      expect(within(panel).getByRole('button', { name: new RegExp(`^${label}:`) })).toBeTruthy()
    }
  })

  it('does not count the status pill, and drops the number at zero', async () => {
    await asAdmin('/runs?status=failed', 360)
    expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy()
  })

  it('keeps the chips and Clear all working, and Escape closes the panel', async () => {
    await asAdmin('/runs?department=Medicine', 360)
    expect(screen.getByRole('button', { name: 'Remove Department filter' })).toBeTruthy()
    const button = screen.getByRole('button', { name: 'Filters (1)' })
    fireEvent.click(button)
    fireEvent.keyDown(screen.getByRole('group', { name: 'Filters' }), { key: 'Escape' })
    expect(screen.queryByRole('group', { name: 'Filters' })).toBeNull()
    expect(document.activeElement).toBe(button)
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    await flush()
    expect(search).toBe('')
    expect(screen.getByRole('button', { name: 'Filters' })).toBeTruthy()
  })

  it('gives a member no Filters button, only their Batch filter', async () => {
    mockViewport(360)
    await renderAt('/runs')
    expect(screen.queryByRole('button', { name: /^Filters/ })).toBeNull()
    expect(screen.getByRole('button', { name: /^Batch:/ })).toBeTruthy()
  })
})
