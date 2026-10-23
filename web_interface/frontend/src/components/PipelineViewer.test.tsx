// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import PipelineViewer from './PipelineViewer'
import { REVIEW_COPY_NOTE } from './OutputFiles'
import { getInputFileUrl } from '../api/runs'
import type { ApiError } from '../api/client'
import type { RunStatus } from '../types'

vi.mock('../api/runs', () => ({
  getInputFileUrl: vi.fn(), getRunDataJson: vi.fn(), cancelRun: vi.fn(),
  restartRun: vi.fn(), retryStep: vi.fn(), startRun: vi.fn(),
}))
// Mutable per test: the viewer's role and the run the page shows.
const view = vi.hoisted(() => ({ canViewAllRuns: false, canAct: false, runStatus: null as unknown }))
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null }),
  useCanSeeCost: () => false,
  useCanViewAllRuns: () => view.canViewAllRuns,
  canActOnRun: () => view.canAct,
}))
// FeedbackForm.test.tsx covers what the feedback card shows; here, only who gets it.
vi.mock('./FeedbackForm', () => ({
  default: ({ readOnly }: { readOnly?: boolean }) => <div>{readOnly ? 'Feedback read-only' : 'Feedback form'}</div>,
}))
// The doctor cards load their own data (RunQualityPanel.test.tsx covers them).
vi.mock('./RunQualityPanel', () => ({
  RunQualitySections: () => <div>Staff quality and Run Doctor</div>,
  OwnerFixList: () => <div>Owner Fix list</div>,
  ReviewNote: () => null,
}))

const RUN_ID = 'ABCDEF'
const STILL_SCANNING = 'This file is still being scanned for malware. Please try again in a minute.'
// A run in flight with no steps yet: renders the header without the
// complete-run panels, which load their own data.
const RUN_STATUS: RunStatus = {
  run_id: RUN_ID, filename: 'cv.docx', status: 'running', total_cost: null,
  total_tokens: 0, input_tokens: 0, output_tokens: 0, steps: [],
}
view.runStatus = RUN_STATUS
// The hook owns the status poll and the WebSocket; stand it in with a fixed run.
vi.mock('../hooks/usePipelineRun', () => ({
  usePipelineRun: () => ({
    runStatus: view.runStatus, currentStep: 1, setCurrentStep: vi.fn(), logs: {}, stepProgress: {},
    showPromptLogs: false, setShowPromptLogs: vi.fn(), promptLogs: [], promptLogsMessage: null,
    selectedPromptLog: null, setSelectedPromptLog: vi.fn(), localElapsedSeconds: 0,
    stepStartTimes: {}, stepStartCosts: {}, connectionLost: false, maybeStuck: false,
    fetchPromptLogsContext: vi.fn(), setRetryInFlight: vi.fn(),
  }),
}))

afterEach(() => { cleanup(); vi.resetAllMocks(); view.canViewAllRuns = false; view.canAct = false; view.runStatus = RUN_STATUS })

describe('PipelineViewer "Original file"', () => {
  it('shows a refused download in the page ErrorBanner', async () => {
    const refusal: ApiError = { status: 409, message: STILL_SCANNING, code: 'conflict' }
    vi.mocked(getInputFileUrl).mockRejectedValue(refusal)
    render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
    expect(screen.queryByRole('alert')).toBeNull()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Original file/ }))
    })

    expect(getInputFileUrl).toHaveBeenCalledWith(RUN_ID)
    expect(screen.getByRole('alert').textContent).toContain(STILL_SCANNING)
  })
})

describe('PipelineViewer image tags (#1239)', () => {
  it.each([['staff', true, 1], ['the owner', false, 0]])(
    'shows %s the run\'s image', (_who, staff, count) => {
      view.canViewAllRuns = staff
      view.runStatus = { ...RUN_STATUS, image_tags: ['dev-235.1.f5e15871'] }
      render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
      expect(screen.queryAllByText('Image dev-235.1.f5e15871')).toHaveLength(count)
    },
  )
})

describe('PipelineViewer on a finished run (#1589)', () => {
  const FINISHED: RunStatus = {
    ...RUN_STATUS, status: 'complete',
    steps: [{
      step_number: 1, step_name: 'Word document', stage_id: '6', status: 'complete',
      output_files: JSON.stringify(['/o/DEMO01_wcm.docx', '/o/DEMO01_wcm_review.docx']),
    } as RunStatus['steps'][number]],
  }

  it.each([
    ['the owner', false, 'Owner Fix list', 'Staff quality and Run Doctor'],
    ['staff', true, 'Staff quality and Run Doctor', 'Owner Fix list'],
  ])('makes the review copy %s the only download, says its notes are comments, and shows the matching doctor card', (_who, staff, shown, hidden) => {
    view.canViewAllRuns = staff
    view.runStatus = FINISHED
    render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
    const main = screen.getByRole('link', { name: 'Download review copy DEMO01_wcm_review.docx' })
    expect(main.getAttribute('href')).toContain('/api/run/ABCDEF/data/DEMO01_wcm_review.docx')
    expect(screen.getAllByRole('link', { name: /^Download/ })).toHaveLength(1)
    expect(document.querySelector('a[href$="/data/DEMO01_wcm.docx"]')).toBeNull()
    expect(screen.getByText(REVIEW_COPY_NOTE)).toBeTruthy()
    expect(screen.getByText(shown)).toBeTruthy()
    expect(screen.queryByText(hidden)).toBeNull()
  })

  it.each([['the owner', false], ['staff', true]])(
    'offers %s the corrected-copy upload right under the Download, and the survey does not repeat it',
    (_who, staff) => {
      view.canViewAllRuns = staff
      view.runStatus = FINISHED
      render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
      const upload = screen.getByRole('region', { name: 'Help improve CViche' })
      expect(within(upload).getByTestId('file-input')).toBeTruthy()
      expect(screen.getAllByTestId('file-input')).toHaveLength(1)
    },
  )

  it('scrolls to the corrected copies when opened from the runs list\'s Corrected line', () => {
    vi.useFakeTimers()
    const scrolled: string[] = []
    const scrollIntoView = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this.id) }
    window.history.replaceState(null, '', '#corrected-copies')
    try {
      view.runStatus = FINISHED
      render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
      act(() => { vi.advanceTimersByTime(500) })
      expect(scrolled).toEqual(['corrected-copies'])
      expect(document.getElementById('corrected-copies')?.getAttribute('aria-labelledby')).toBe('corrected-upload-heading')
    } finally {
      Element.prototype.scrollIntoView = scrollIntoView
      window.history.replaceState(null, '', '#')
      vi.useRealTimers()
    }
  })

  it.each([
    ['an admin', true, true, 'Feedback form'],
    ['the owner', true, false, 'Feedback form'],
    ['staff on a run that is not theirs', false, true, 'Feedback read-only'],
  ])('gives %s the feedback card', (_who, canAct, staff, shown) => {
    view.canAct = canAct
    view.canViewAllRuns = staff
    view.runStatus = FINISHED
    render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
    expect(screen.getByText(shown)).toBeTruthy()
  })

  it('shows no feedback card to a viewer who can neither act nor view all runs, nor before the run finishes', () => {
    view.runStatus = FINISHED
    render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
    expect(screen.queryByText(/^Feedback /)).toBeNull()
    cleanup()
    view.canAct = true
    view.canViewAllRuns = true
    view.runStatus = RUN_STATUS
    render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
    expect(screen.queryByText(/^Feedback /)).toBeNull()
  })

  it('offers no upload before the run has a finished document', () => {
    render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
    expect(screen.queryByRole('region', { name: 'Help improve CViche' })).toBeNull()
  })

  it('offers the finished document when no review copy was written', () => {
    view.runStatus = { ...FINISHED, steps: [{ ...FINISHED.steps[0], output_files: JSON.stringify(['/o/DEMO01_wcm.docx']) }] }
    render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
    expect(screen.getByRole('link', { name: 'Download final output file DEMO01_wcm.docx' })).toBeTruthy()
    expect(screen.queryByRole('link', { name: /review copy/i })).toBeNull()
    expect(screen.queryByText(REVIEW_COPY_NOTE)).toBeNull()
  })
})
