// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import PipelineViewer from './PipelineViewer'
import { getInputFileUrl } from '../api/runs'
import type { ApiError } from '../api/client'
import type { RunStatus } from '../types'

vi.mock('../api/runs', () => ({
  getInputFileUrl: vi.fn(), getRunDataJson: vi.fn(), cancelRun: vi.fn(),
  restartRun: vi.fn(), retryStep: vi.fn(), startRun: vi.fn(),
}))
// Mutable per test: the viewer's role and the run the page shows.
const view = vi.hoisted(() => ({ canViewAllRuns: false, runStatus: null as unknown }))
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null }),
  useCanSeeCost: () => false,
  useCanViewAllRuns: () => view.canViewAllRuns,
  canActOnRun: () => false,
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

afterEach(() => { cleanup(); vi.resetAllMocks(); view.canViewAllRuns = false; view.runStatus = RUN_STATUS })

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
  ])('offers %s the review copy beside the download, and the matching doctor card', (_who, staff, shown, hidden) => {
    view.canViewAllRuns = staff
    view.runStatus = FINISHED
    render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
    const link = screen.getByRole('link', { name: /Review copy \(with CViche's notes\)/ })
    expect(link.getAttribute('href')).toContain('/api/run/ABCDEF/data/DEMO01_wcm_review.docx')
    expect(screen.getByRole('link', { name: 'Download final output file DEMO01_wcm.docx' })).toBeTruthy()
    expect(screen.getByText(shown)).toBeTruthy()
    expect(screen.queryByText(hidden)).toBeNull()
  })

  it('leaves the review link out when no review copy was written', () => {
    view.runStatus = { ...FINISHED, steps: [{ ...FINISHED.steps[0], output_files: JSON.stringify(['/o/DEMO01_wcm.docx']) }] }
    render(<PipelineViewer runId={RUN_ID} onBack={() => {}} />)
    expect(screen.queryByRole('link', { name: /Review copy/ })).toBeNull()
  })
})
