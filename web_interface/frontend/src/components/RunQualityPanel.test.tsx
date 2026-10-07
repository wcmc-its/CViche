// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { EMPTY_REPORT_RETRIES, EMPTY_REPORT_RETRY_MS, ReviewNote, RunQualitySections, seenOn } from './RunQualityPanel'
import { getRunQuality, getRunReviewNote } from '../api/runs'
import type { RunQualityReport, RunReviewNote } from '../types'

vi.mock('../api/runs', () => ({
  getRunQuality: vi.fn(),
  getRunReviewNote: vi.fn(),
}))

const EMPTY: RunQualityReport = {
  run_id: 'ABCDEF', score: null, band: null, band_meaning: null, provisional: true,
  cap: null, cap_reason: null, cap_lint: null, earned: null, total_weight: null,
  data_complete: null, dimensions: [], gates_fired: [], doctor: null,
}
const SCORED: RunQualityReport = { ...EMPTY, score: 87, band: 'GREEN', band_meaning: 'Ship', earned: 87, total_weight: 95 }

const NO_SCORE = 'No quality score is stored for this run.'

// Lets the resolved getRunQuality promise and the resulting state update land.
const flush = () => act(async () => { await Promise.resolve() })
const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(EMPTY_REPORT_RETRY_MS) })

describe('RunQualitySections', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.mocked(getRunQuality).mockReset() })

  it('fetches again when the report is empty, and shows the score once it is written', async () => {
    vi.mocked(getRunQuality).mockResolvedValueOnce(EMPTY).mockResolvedValueOnce(SCORED)
    render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    expect(screen.getByText(NO_SCORE)).toBeTruthy()

    await tick()
    expect(getRunQuality).toHaveBeenCalledTimes(2)
    expect(screen.queryByText(NO_SCORE)).toBeNull()
    expect(screen.getByText('87')).toBeTruthy()

    await tick()
    expect(getRunQuality).toHaveBeenCalledTimes(2)
  })

  it('stops after EMPTY_REPORT_RETRIES when nothing is ever stored', async () => {
    vi.mocked(getRunQuality).mockResolvedValue(EMPTY)
    render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    for (let i = 0; i < EMPTY_REPORT_RETRIES + 3; i++) await tick()
    expect(getRunQuality).toHaveBeenCalledTimes(1 + EMPTY_REPORT_RETRIES)
    expect(screen.getByText(NO_SCORE)).toBeTruthy()
  })

  it('does not fetch again when the first report already has a score', async () => {
    vi.mocked(getRunQuality).mockResolvedValue(SCORED)
    render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    await tick()
    expect(getRunQuality).toHaveBeenCalledTimes(1)
  })

  it('cancels a pending retry on unmount', async () => {
    vi.mocked(getRunQuality).mockResolvedValue(EMPTY)
    const { unmount } = render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    unmount()
    await tick()
    expect(getRunQuality).toHaveBeenCalledTimes(1)
  })

  it('links the cap banner to its Run Doctor row by the plain title', async () => {
    const lint = 'owner_contact_missing'
    vi.mocked(getRunQuality).mockResolvedValue({
      ...SCORED, cap: 25, cap_reason: 'owner name missing', cap_lint: lint,
      doctor: {
        counts: { error: 1, warn: 0, info: 0 }, not_run: 0,
        findings: [{ lint, severity: 'ERROR', message: 'No name', title: 'Owner name not found', what_to_do: 'Rerun.', count: 1, prevalence: null, caps_score: true, instances: [] }],
      },
    })
    const { container } = render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    const link = screen.getByRole('link', { name: 'Owner name not found' })
    const target = link.getAttribute('href')!.slice(1)
    expect(container.querySelector(`[id="${target}"]`)).toBeTruthy()
  })

  it('shows a finding as title, what to do, and Once / N times', async () => {
    const finding = { severity: 'WARN' as const, message: 'm', what_to_do: 'Delete the repeat.', prevalence: 0.12, caps_score: false, instances: [] }
    vi.mocked(getRunQuality).mockResolvedValue({
      ...SCORED,
      doctor: {
        counts: { error: 0, warn: 2, info: 0 }, not_run: 0,
        findings: [
          { ...finding, lint: 'duplicate_records', title: 'Repeated numbered entry', count: 1 },
          { ...finding, lint: 'pipe_leaks', title: null, what_to_do: null, count: 3 },
        ],
      },
    })
    render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    expect(screen.getByText('Repeated numbered entry')).toBeTruthy()
    expect(screen.getByText('Delete the repeat.')).toBeTruthy()
    expect(screen.getByText('Once')).toBeTruthy()
    expect(screen.getByText('3 times')).toBeTruthy()
    expect(screen.getByText('pipe_leaks')).toBeTruthy()
    expect(screen.getAllByText('Seen on 12% of runs')).toHaveLength(2)
  })

  it('lists where each instance is, its section and quoted text, and says when the list is cut short', async () => {
    vi.mocked(getRunQuality).mockResolvedValue({
      ...SCORED,
      doctor: {
        counts: { error: 0, warn: 1, info: 0 }, not_run: 0,
        findings: [{
          lint: 'multi_record_coverage', severity: 'WARN', message: 'm', title: 'Several records read as one',
          what_to_do: 'Add each missing record.', count: 30, prevalence: null, caps_score: false,
          instances: [
            { severity: 'WARN', section: 'Academic Appointments', detail: '1 other clause(s) on no line of the output', quotes: ['Associate Professor of Medicine, 2016-2022'], notes: [] },
            { severity: 'INFO', section: null, detail: 'no section named', quotes: [], notes: ['row 3: empty date'] },
          ],
        }],
      },
    })
    render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    expect(screen.getByText('Where (2)').closest('details')!.open).toBe(false)
    expect(screen.getByText('Academic Appointments:')).toBeTruthy()
    expect(screen.getByText('Associate Professor of Medicine, 2016-2022').tagName).toBe('Q')
    expect(screen.getByText('no section named')).toBeTruthy()
    // The doctor's own locator is plain text, not styled as a CV quote.
    expect(screen.getByText('row 3: empty date').tagName).toBe('SPAN')
    expect(screen.getByText('Showing the first 2 of 30.')).toBeTruthy()
  })

  it('opens a row card on focus and lists a fired gate with its cap', async () => {
    const wording = { checks: 'Checks tables.', scoring: 'Caps at 84.', if_lost: 'Re-enter rows.' }
    vi.mocked(getRunQuality).mockResolvedValue({
      ...SCORED,
      dimensions: [{ name: 'Sparse tables', label: 'Tables filled in', weight: 12, points: 10, can_cap: false, ...wording }],
      gates_fired: [{ name: 'Lost table gate', label: 'Source tables read in full', cap: 84, lint: 'table_lost', ...wording }],
    })
    render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.focus(screen.getByText('Tables filled in').parentElement!)
    expect(screen.getByRole('tooltip').textContent).toContain('Scored as \u201cSparse tables\u201d \u00b7 weight 12')
    expect(screen.getByRole('link', { name: 'Caps score at 84' }).getAttribute('href')).toBe('#doctor-lint-table_lost')
  })

  it('reads a rate that rounds to 0% as under 1%', () => {
    expect([seenOn(0.001), seenOn(0.005), seenOn(0.12)]).toEqual(['under 1%', '1%', '12%'])
  })

  it('labels a gate card as cap-only', async () => {
    vi.mocked(getRunQuality).mockResolvedValue({
      ...SCORED,
      dimensions: [{ name: 'Dup', label: null, weight: 10, points: 10, can_cap: false, checks: null, scoring: null, if_lost: null }],
      gates_fired: [{ name: 'Gate', label: 'Source tables read in full', cap: 84, lint: 'table_lost', checks: null, scoring: null, if_lost: 'Re-enter rows.' }],
    })
    render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    fireEvent.focus(screen.getByText('Source tables read in full').parentElement!)
    const card = screen.getByRole('tooltip').textContent
    expect(card).toContain('If it fires:')
    expect(card).toContain('caps only, no points')
  })
})

const UNSCORED_NOTE: RunReviewNote = { needs_cleanup: false, scored: false }
const CLEANUP_NOTE: RunReviewNote = { needs_cleanup: true, scored: true }

describe('ReviewNote', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.mocked(getRunReviewNote).mockReset() })

  it('asks again while the run is unscored, and shows the note once the score lands (#1199)', async () => {
    vi.mocked(getRunReviewNote).mockResolvedValueOnce(UNSCORED_NOTE).mockResolvedValueOnce(CLEANUP_NOTE)
    render(<ReviewNote runId="ABCDEF" />)
    await flush()
    expect(screen.queryByRole('note')).toBeNull()

    await tick()
    expect(getRunReviewNote).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('note')).toBeTruthy()

    await tick()
    expect(getRunReviewNote).toHaveBeenCalledTimes(2)
  })

  it('does not ask again once the run is scored, even when no cleanup is needed', async () => {
    vi.mocked(getRunReviewNote).mockResolvedValue({ needs_cleanup: false, scored: true })
    render(<ReviewNote runId="ABCDEF" />)
    await flush()
    await tick()
    expect(getRunReviewNote).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('stops after EMPTY_REPORT_RETRIES when the run is never scored', async () => {
    vi.mocked(getRunReviewNote).mockResolvedValue(UNSCORED_NOTE)
    render(<ReviewNote runId="ABCDEF" />)
    await flush()
    for (let i = 0; i < EMPTY_REPORT_RETRIES + 3; i++) await tick()
    expect(getRunReviewNote).toHaveBeenCalledTimes(1 + EMPTY_REPORT_RETRIES)
  })
})
