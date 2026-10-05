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
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null }),
  useCanSeeCost: () => false,
  useCanViewAllRuns: () => false,
  canActOnRun: () => false,
}))

const RUN_ID = 'ABCDEF'
const STILL_SCANNING = 'This file is still being scanned for malware. Please try again in a minute.'
// A run in flight with no steps yet: renders the header without the
// complete-run panels, which load their own data.
const RUN_STATUS: RunStatus = {
  run_id: RUN_ID, filename: 'cv.docx', status: 'running', total_cost: null,
  total_tokens: 0, input_tokens: 0, output_tokens: 0, steps: [],
}
// The hook owns the status poll and the WebSocket; stand it in with a fixed run.
vi.mock('../hooks/usePipelineRun', () => ({
  usePipelineRun: () => ({
    runStatus: RUN_STATUS, currentStep: 1, setCurrentStep: vi.fn(), logs: {}, stepProgress: {},
    showPromptLogs: false, setShowPromptLogs: vi.fn(), promptLogs: [], promptLogsMessage: null,
    selectedPromptLog: null, setSelectedPromptLog: vi.fn(), localElapsedSeconds: 0,
    stepStartTimes: {}, stepStartCosts: {}, connectionLost: false, maybeStuck: false,
    fetchPromptLogsContext: vi.fn(), setRetryInFlight: vi.fn(),
  }),
}))

afterEach(() => { cleanup(); vi.resetAllMocks() })

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
