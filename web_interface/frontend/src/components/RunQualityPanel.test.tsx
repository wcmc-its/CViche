// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { EMPTY_REPORT_RETRIES, EMPTY_REPORT_RETRY_MS, OwnerFixList, ReviewNote, RunQualitySections, seenOn } from './RunQualityPanel'
import { getRunFixList, getRunQuality, getRunReviewNote } from '../api/runs'
import type { RunDoctorReport, RunFixList, RunQualityReport, RunReviewNote } from '../types'

vi.mock('../api/runs', () => ({
  getRunFixList: vi.fn(),
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
/** A doctor report's Fix-list fields, empty. */
const NO_FIX = { fix_list: [], fix_list_held_back: 0, fix_list_more: 0, not_checked: [] }

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
        counts: { error: 1, warn: 0, info: 0 }, not_run: 0, ...NO_FIX,
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
        counts: { error: 0, warn: 2, info: 0 }, not_run: 0, ...NO_FIX,
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
        counts: { error: 0, warn: 1, info: 0 }, not_run: 0, ...NO_FIX,
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

describe('Run Doctor Fix list (#1589)', () => {
  const quote = 'Visiting Lecturer in Medicine, Northfield University, 1990-1991'
  const DOCTOR: RunDoctorReport = {
    counts: { error: 0, warn: 1, info: 0 }, not_run: 0,
    findings: [{
      lint: 'under_extraction', severity: 'WARN', message: 'm', title: 'Big entry mostly unread',
      what_to_do: 'Add the missing records.', count: 1, prevalence: 0.03, caps_score: false, instances: [],
    }],
    fix_list: [
      { section: null, items: [{ problems: [{ severity: 'ERROR', title: 'No document produced', what_to_do: 'Rerun the CV.', confidence: 'unmeasured', effort: 'longer' }], quotes: [] }] },
      { section: 'Honors & Awards', items: [{
        problems: [
          { severity: 'WARN', title: 'Heading printed as a record', what_to_do: 'Delete the quoted row.', confidence: 'high', effort: 'quick' },
          { severity: 'WARN', title: 'Big entry mostly unread', what_to_do: 'Add the missing records.', confidence: 'medium', effort: 'longer' },
        ],
        quotes: [quote],
      }] },
    ],
    fix_list_held_back: 2, fix_list_more: 1,
    not_checked: ['Journal names, volumes and pages in citations.'],
  }

  afterEach(() => { cleanup(); vi.mocked(getRunQuality).mockReset(); window.location.hash = '' })

  const renderDoctor = async () => {
    vi.mocked(getRunQuality).mockResolvedValue({ ...SCORED, doctor: DOCTOR })
    render(<RunQualitySections runId="ABCDEF" />)
    await act(async () => { await Promise.resolve() })
  }
  const panel = (name: string) => screen.getByRole('tabpanel', { name })

  it('opens on the Fix list, grouped by location, merged per entry, quoting the text at stake', async () => {
    await renderDoctor()
    expect(screen.getByRole('tab', { name: 'Fix list (3)' }).getAttribute('aria-selected')).toBe('true')
    const fix = within(panel('Fix list (3)'))
    expect(fix.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual(['Across the document', 'Honors & Awards'])
    const [, honors] = fix.getAllByRole('list')
    const item = within(within(honors).getByRole('listitem'))
    expect(item.getByText('Heading printed as a record')).toBeTruthy()
    expect(item.getByText('Big entry mostly unread')).toBeTruthy()
    expect(item.getByText(quote).tagName).toBe('Q')
    expect(item.getByText('High confidence')).toBeTruthy()
    expect(item.getByText('Quick fix')).toBeTruthy()
    expect(fix.getByText('Confidence not yet measured')).toBeTruthy()
    expect(fix.getByText('1 more item is listed under Diagnostics.')).toBeTruthy()
    expect(fix.getByText('2 findings that are wrong too often to act on are only under Diagnostics.')).toBeTruthy()
    // Developer language stays in Diagnostics.
    expect(fix.queryByText('under_extraction')).toBeNull()
    expect(fix.queryByText(/Seen on/)).toBeNull()
    expect(screen.queryByRole('tabpanel', { name: 'Diagnostics' })).toBeNull()  // hidden
  })

  it('switches to Diagnostics, which keeps the full per-check list', async () => {
    await renderDoctor()
    fireEvent.click(screen.getByRole('tab', { name: 'Diagnostics' }))
    const diagnostics = within(panel('Diagnostics'))
    expect(diagnostics.getByText('under_extraction')).toBeTruthy()
    expect(diagnostics.getByText('Seen on 3% of runs')).toBeTruthy()
    expect(screen.queryByRole('tabpanel', { name: 'Fix list (3)' })).toBeNull()
  })

  it('lists what the doctor does not check under either tab', async () => {
    await renderDoctor()
    expect(screen.getByText('Not checked on any run')).toBeTruthy()
    expect(screen.getByText('Journal names, volumes and pages in citations.')).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: 'Diagnostics' }))
    expect(screen.getByText('Journal names, volumes and pages in citations.')).toBeTruthy()
  })

  it('follows a cap link into Diagnostics', async () => {
    await renderDoctor()
    act(() => {
      window.location.hash = '#doctor-lint-under_extraction'
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    expect(screen.getByRole('tab', { name: 'Diagnostics' }).getAttribute('aria-selected')).toBe('true')
  })

  it('says a quiet doctor is not a clean bill of health', async () => {
    vi.mocked(getRunQuality).mockResolvedValue({ ...SCORED, doctor: { ...DOCTOR, fix_list: [], fix_list_held_back: 0, fix_list_more: 0 } })
    render(<RunQualitySections runId="ABCDEF" />)
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText("Nothing to fix was found. Some problems can't be checked, so read the list below too.")).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Fix list (0)' })).toBeTruthy()
  })
})

describe('OwnerFixList: the run owner sees the Fix list only (#1589)', () => {
  const quote = 'Visiting Lecturer in Medicine, Northfield University, 1990-1991'
  const FIX: RunFixList = {
    fix_list: [{ section: 'Honors & Awards', items: [{
      problems: [{ severity: 'WARN', title: 'Heading printed as a record', what_to_do: 'Delete the quoted row.', confidence: 'high', effort: 'quick' }],
      quotes: [quote],
    }] }],
    fix_list_more: 2,
    not_checked: ['Journal names, volumes and pages in citations.'],
  }

  afterEach(() => { cleanup(); vi.mocked(getRunFixList).mockReset(); window.location.hash = '' })

  const renderOwner = async (fix: RunFixList | null = FIX) => {
    vi.mocked(getRunFixList).mockResolvedValue(fix)
    render(<OwnerFixList runId="ABCDEF" />)
    await act(async () => { await Promise.resolve() })
  }

  it('shows one tab, the Fix list, with no Diagnostics and no score', async () => {
    await renderOwner()
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Fix list (3)'])
    const fix = within(screen.getByRole('tabpanel', { name: 'Fix list (3)' }))
    expect(fix.getByText('Heading printed as a record')).toBeTruthy()
    expect(fix.getByText(quote).tagName).toBe('Q')
    expect(fix.getByText('High confidence')).toBeTruthy()
    expect(fix.getByText('Quick fix')).toBeTruthy()
    // The cut is named without pointing at a Diagnostics tab the owner lacks.
    expect(fix.getByText("2 more items were found but aren't listed here.")).toBeTruthy()
    expect(screen.queryByText(/Diagnostics/)).toBeNull()
    expect(screen.queryByRole('region', { name: 'Quality score' })).toBeNull()
    expect(screen.getByText('Journal names, volumes and pages in citations.')).toBeTruthy()
    expect(getRunQuality).not.toHaveBeenCalled()
  })

  it('stays on the Fix list when a link points at a Diagnostics row', async () => {
    window.location.hash = '#doctor-lint-under_extraction'
    await renderOwner()
    expect(screen.getByRole('tabpanel', { name: 'Fix list (3)' })).toBeTruthy()
  })

  it('asks again while no report is stored, and shows the list once it is written', async () => {
    vi.useFakeTimers()
    try {
      vi.mocked(getRunFixList).mockResolvedValueOnce(null).mockResolvedValueOnce(FIX)
      render(<OwnerFixList runId="ABCDEF" />)
      await flush()
      expect(screen.getByText('No Run Doctor report is stored for this run.')).toBeTruthy()
      await tick()
      expect(getRunFixList).toHaveBeenCalledTimes(2)
      expect(screen.getByRole('tab', { name: 'Fix list (3)' })).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('says when no Run Doctor report is stored', async () => {
    await renderOwner(null)
    expect(screen.getByText('No Run Doctor report is stored for this run.')).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
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
