// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import BatchView, { BATCH_REFRESH_MS, COPY_FEEDBACK_MS } from './BatchView'
import { getBatch, getQueue } from '../../api/batches'
import type { BatchDetail, BatchRunRow, QueueOverview } from '../../types'

vi.mock('../../api/batches', () => ({ getBatch: vi.fn(), getQueue: vi.fn() }))

const row = (over: Partial<BatchRunRow>): BatchRunRow => ({
  run_id: 'RUNAAA', filename: 'cv.docx', cv_owner_name: null, status: 'queued', queue_position: null, quality_score: null, ...over,
})

// Invented names only.
const BATCH: BatchDetail = {
  id: 'BQXZKD',
  submitted_by: { id: 7, display_name: 'Test Admin', cwid: null, email: null, department: null },
  created_at: '2026-01-02T15:00:00Z',
  run_count: 4,
  files_submitted: 5,
  status_counts: { complete: 1, running: 1, queued: 1, failed: 1, cancelled: 0, created: 0 },
  runs: [
    row({ run_id: 'RUNAAA', filename: 'alpha_cv.docx', cv_owner_name: 'Pat Example', status: 'complete', quality_score: 88 }),
    row({ run_id: 'RUNBBB', filename: 'beta_cv.docx', status: 'running' }),
    row({ run_id: 'RUNCCC', filename: 'gamma_cv.docx', status: 'queued', queue_position: 3 }),
    row({ run_id: 'RUNDDD', filename: 'delta_cv.docx', status: 'failed' }),
  ],
}
const QUEUE: QueueOverview = { dispatch_mode: 'queue', single: null, batch: { workers: 3, ahead: 1, est_wait_minutes: 70 } }

const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
const onSelectRun = vi.fn()

async function renderView(detail: BatchDetail = BATCH, isAdmin = true) {
  vi.mocked(getBatch).mockResolvedValue(detail)
  vi.mocked(getQueue).mockResolvedValue(QUEUE)
  render(<BatchView batchId={detail.id} isAdmin={isAdmin} currentUserId={7} onSelectRun={onSelectRun} />)
  await flush()
}

const lines = () => screen.getAllByTestId('batch-run')

afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers() })

describe('BatchView', () => {
  it('shows the header, the time left and one row per run', async () => {
    await renderView()
    expect(getBatch).toHaveBeenCalledWith('BQXZKD')
    expect(screen.getByRole('heading', { name: '4 CVs' })).toBeTruthy()
    expect(screen.getByText(/by Test Admin \(you\)/)).toBeTruthy()
    expect(screen.getByText('BQXZKD')).toBeTruthy()
    expect(screen.getByText('Up to 3 run at once. The last should finish in about 1 h 10 m.')).toBeTruthy()
    expect(lines().map((l) => within(l).getByText(/_cv\.docx$/).textContent)).toEqual([
      'alpha_cv.docx', 'beta_cv.docx', 'gamma_cv.docx', 'delta_cv.docx',
    ])
  })

  it('names the faculty member, or says it is read from the CV when it runs', async () => {
    await renderView()
    expect(within(lines()[0]).getByText('Pat Example')).toBeTruthy()
    const pending = within(lines()[1]).getByText('Read from the CV when it runs')
    expect(pending.className).toContain('italic')
  })

  it('shows how many are ahead of a queued run', async () => {
    await renderView()
    expect(within(lines()[2]).getByText('3 ahead')).toBeTruthy()
    expect(within(lines()[1]).queryByText(/ahead/)).toBeNull()
  })

  it('opens running and complete runs, not queued or failed ones', async () => {
    await renderView()
    lines().forEach((l) => fireEvent.click(l))
    expect(onSelectRun.mock.calls.map(([id]) => id)).toEqual(['RUNAAA', 'RUNBBB'])
  })

  it('shows the Score column to admins only', async () => {
    await renderView(BATCH, true)
    expect(screen.getByText('Score')).toBeTruthy()
    expect(within(lines()[0]).getByText('88')).toBeTruthy()
    cleanup()
    await renderView(BATCH, false)
    expect(screen.queryByText('Score')).toBeNull()
    expect(within(lines()[0]).queryByText('88')).toBeNull()
  })

  it('notes files that never became runs', async () => {
    await renderView()
    expect(screen.getByText("1 file didn't upload, so it isn't in this batch.")).toBeTruthy()
    cleanup()
    await renderView({ ...BATCH, files_submitted: 4 })
    expect(screen.queryByText(/didn't upload/)).toBeNull()
  })

  it('says all runs finished once none is queued or running', async () => {
    await renderView({ ...BATCH, status_counts: { complete: 3, running: 0, queued: 0, failed: 1, cancelled: 0, created: 0 } })
    expect(screen.getByText('All runs finished.')).toBeTruthy()
  })

  it('does not say all runs finished while one is still running', async () => {
    await renderView({ ...BATCH, status_counts: { complete: 3, running: 1, queued: 0, failed: 0, cancelled: 0, created: 0 } })
    expect(screen.queryByText('All runs finished.')).toBeNull()
    expect(screen.getByText('Up to 3 run at once. The last should finish in about 1 h 10 m.')).toBeTruthy()
  })

  it('leaves statuses with no runs out of the status bar', async () => {
    await renderView()
    const legend = screen.getByTestId('batch-status-counts')
    expect(legend.textContent).toBe('Complete 1Running 1Queued 1Failed 1')
  })

  it('names a run that was never started the same way in the bar and on its row', async () => {
    await renderView({
      ...BATCH,
      status_counts: { complete: 0, running: 0, queued: 0, failed: 0, cancelled: 0, created: 1 },
      runs: [row({ status: 'created' })],
    })
    expect(screen.getByTestId('batch-status-counts').textContent).toBe('Pending 1')
    expect(within(lines()[0]).getByText('Pending')).toBeTruthy()
  })

  it('copies the batch view link', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    await renderView()
    fireEvent.click(screen.getByRole('button', { name: 'Copy link' }))
    await flush()
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/runs?batch=BQXZKD`)
    expect(screen.getByRole('button', { name: 'Link copied' })).toBeTruthy()
    expect(COPY_FEEDBACK_MS).toBe(1600)
  })

  it('re-reads the batch while runs are queued or running', async () => {
    vi.useFakeTimers()
    await renderView()
    expect(getBatch).toHaveBeenCalledTimes(1)
    vi.mocked(getBatch).mockResolvedValue({ ...BATCH, status_counts: { ...BATCH.status_counts, running: 0, queued: 0, complete: 3 } })
    await act(async () => { vi.advanceTimersByTime(BATCH_REFRESH_MS) })
    await flush()
    expect(getBatch).toHaveBeenCalledTimes(2)
    expect(screen.getByText('All runs finished.')).toBeTruthy()

    await act(async () => { vi.advanceTimersByTime(BATCH_REFRESH_MS * 3) })
    await flush()
    expect(getBatch).toHaveBeenCalledTimes(2)
  })

  it('keeps showing the batch when a re-read fails, and tries again', async () => {
    vi.useFakeTimers()
    await renderView()
    vi.mocked(getBatch).mockRejectedValueOnce({ status: 503, message: 'down' })
    await act(async () => { vi.advanceTimersByTime(BATCH_REFRESH_MS) })
    await flush()
    expect(screen.getByRole('heading', { name: '4 CVs' })).toBeTruthy()
    await act(async () => { vi.advanceTimersByTime(BATCH_REFRESH_MS) })
    await flush()
    expect(getBatch).toHaveBeenCalledTimes(3)
  })

  it("says the batch can't be shown when the API answers 404", async () => {
    vi.mocked(getBatch).mockRejectedValue({ status: 404, message: 'Batch not found' })
    vi.mocked(getQueue).mockResolvedValue(QUEUE)
    render(<BatchView batchId="NOPENO" isAdmin={false} currentUserId={7} onSelectRun={onSelectRun} />)
    await flush()
    expect(screen.getByText("This batch doesn't exist, or you don't have access to it.")).toBeTruthy()
  })
})
