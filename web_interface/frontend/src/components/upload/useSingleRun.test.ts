// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useSingleRun } from './useSingleRun'
import { uploadFile } from '../../api/upload'
import { getCapacity, startRun } from '../../api/runs'

vi.mock('../../api/upload', () => ({ uploadFile: vi.fn(), getEstimate: vi.fn() }))
vi.mock('../../api/runs', () => ({ startRun: vi.fn(), getCapacity: vi.fn() }))

const onUploadSuccess = vi.fn()
const FILE = new File(['cv'], 'mine.docx')

function renderRun() {
  return renderHook(() => useSingleRun({
    stripWcmInstructions: true, submissionType: 'own_cv', onUploadSuccess, onConsentRequired: vi.fn(),
  }))
}

beforeEach(() => {
  vi.mocked(getCapacity).mockResolvedValue({ available: true, active: 0, limit: 6 })
  vi.mocked(uploadFile).mockResolvedValue({ run_id: 'R1', wcm_template_warning: false, wcm_template_match_ratio: null })
  vi.mocked(startRun).mockResolvedValue(undefined)
})
afterEach(() => vi.resetAllMocks())

describe('useSingleRun', () => {
  it('retries a failed start with the same run and never re-uploads (#177)', async () => {
    vi.mocked(startRun).mockRejectedValueOnce({ status: 429, message: 'At capacity' })
    const { result } = renderRun()
    await act(() => result.current.start(FILE))
    expect(result.current.pendingStart).toEqual({ runId: 'R1' })
    expect(result.current.error).toBe('At capacity')

    await act(() => result.current.start(FILE))
    expect(uploadFile).toHaveBeenCalledTimes(1)
    expect(vi.mocked(startRun).mock.calls.map(([id]) => id)).toEqual(['R1', 'R1'])
    expect(onUploadSuccess).toHaveBeenCalledWith('R1')
  })

  it('does not upload when the capacity probe says the system is full', async () => {
    vi.mocked(getCapacity).mockResolvedValue({ available: false, active: 6, limit: 6 })
    const { result } = renderRun()
    await act(() => result.current.start(FILE))
    expect(uploadFile).not.toHaveBeenCalled()
    expect(result.current.error).toMatch(/temporarily at capacity/)
  })

  it('holds a blank-template upload until the warning is acknowledged, then starts that run', async () => {
    vi.mocked(uploadFile).mockResolvedValue({ run_id: 'R1', wcm_template_warning: true, wcm_template_match_ratio: 0.9 })
    const { result } = renderRun()
    await act(() => result.current.start(FILE))
    expect(result.current.pendingWarning).toEqual({ runId: 'R1' })
    expect(startRun).not.toHaveBeenCalled()

    await act(() => result.current.start(FILE))
    expect(startRun).not.toHaveBeenCalled()

    act(() => result.current.setAcknowledged(true))
    await act(() => result.current.start(FILE))
    expect(uploadFile).toHaveBeenCalledTimes(1)
    expect(startRun).toHaveBeenCalledWith('R1')
    expect(onUploadSuccess).toHaveBeenCalledWith('R1')
  })
})
