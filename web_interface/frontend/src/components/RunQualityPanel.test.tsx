// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { EMPTY_REPORT_RETRIES, EMPTY_REPORT_RETRY_MS, RunQualitySections } from './RunQualityPanel'
import { getRunQuality } from '../api/runs'
import type { RunQualityReport } from '../types'

vi.mock('../api/runs', () => ({
  getRunQuality: vi.fn(),
  getRunReviewNote: vi.fn(),
}))

const EMPTY: RunQualityReport = {
  run_id: 'ABCDEF', score: null, band: null, band_meaning: null, provisional: true,
  cap: null, cap_reason: null, cap_lint: null, earned: null, total_weight: null,
  data_complete: null, dimensions: [], doctor: null,
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

  it('links the cap banner lint to its Run Doctor row', async () => {
    const lint = 'missing_required_section'
    vi.mocked(getRunQuality).mockResolvedValue({
      ...SCORED, cap: 60, cap_reason: 'Hard fail', cap_lint: lint,
      doctor: {
        counts: { error: 1, warn: 0, info: 0 }, not_run: 0,
        findings: [{ lint, severity: 'ERROR', message: 'A section is missing', count: 1, prevalence: null, caps_score: true }],
      },
    })
    const { container } = render(<RunQualitySections runId="ABCDEF" />)
    await flush()
    const link = screen.getByRole('link', { name: lint })
    const target = link.getAttribute('href')!.slice(1)
    expect(container.querySelector(`[id="${target}"]`)).toBeTruthy()
  })
})
