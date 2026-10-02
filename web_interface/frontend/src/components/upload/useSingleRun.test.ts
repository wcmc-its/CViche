// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useSingleFile, useSingleRun } from './useSingleRun'
import { MAX_UPLOAD_BYTES } from './batchRows'
import { uploadFile } from '../../api/upload'
import { getCapacity, startRun } from '../../api/runs'
import { createBatch } from '../../api/batches'
import { submitInboxItem } from '../../api/inbox'

vi.mock('../../api/upload', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/upload')>()),
  uploadFile: vi.fn(), getEstimate: vi.fn(),
}))
vi.mock('../../api/runs', () => ({ startRun: vi.fn(), getCapacity: vi.fn() }))
vi.mock('../../api/batches', () => ({ createBatch: vi.fn() }))
vi.mock('../../api/inbox', () => ({ submitInboxItem: vi.fn() }))

const onUploadSuccess = vi.fn()
const onConsentRequired = vi.fn()
const FILE = new File(['cv'], 'mine.docx')

function renderRun(notifyOnComplete = false) {
  return renderHook(() => useSingleRun({
    stripWcmInstructions: true, submissionType: 'own_cv', notifyOnComplete, onUploadSuccess, onConsentRequired,
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

  it('uploads with no batch when "Email me when job completes" is off', async () => {
    const { result } = renderRun(false)
    await act(() => result.current.start(FILE))
    expect(createBatch).not.toHaveBeenCalled()
    expect(vi.mocked(uploadFile).mock.calls[0][1].batchId).toBeUndefined()
  })

  it('puts the run in a one-file batch that asks for the email when it is on (#1335)', async () => {
    vi.mocked(createBatch).mockResolvedValue({ id: 'BQXZKD' })
    const { result } = renderRun(true)
    await act(() => result.current.start(FILE))
    expect(createBatch).toHaveBeenCalledWith(1, { notifyOnComplete: true })
    expect(vi.mocked(uploadFile).mock.calls[0][1].batchId).toBe('BQXZKD')
    expect(onUploadSuccess).toHaveBeenCalledWith('R1')
  })

  it('puts an emailed CV held for this run in the one-file batch too', async () => {
    vi.mocked(createBatch).mockResolvedValue({ id: 'BQXZKD' })
    vi.mocked(submitInboxItem).mockResolvedValue({ id: 11, status: 'submitted', run_id: 'RM1', error: null, message: null, last_processed_on: null })
    const { result } = renderRun(true)
    await act(() => result.current.start(new File([], 'emailed.docx'), { id: 11, filename: 'emailed.docx', size_bytes: 10 }))
    expect(vi.mocked(submitInboxItem).mock.calls[0][1].batchId).toBe('BQXZKD')
    expect(onUploadSuccess).toHaveBeenCalledWith('RM1')
  })

  it("shows a refused batch's quota message and uploads nothing", async () => {
    vi.mocked(createBatch).mockRejectedValue({ status: 429, message: 'Daily limit reached' })
    const { result } = renderRun(true)
    await act(() => result.current.start(FILE))
    expect(uploadFile).not.toHaveBeenCalled()
    expect(result.current.error).toBe('Daily limit reached')
  })

  it('sends the user to the consent page when the batch is refused for consent', async () => {
    vi.mocked(createBatch).mockRejectedValue({ status: 403, message: 'consent_required' })
    const { result } = renderRun(true)
    await act(() => result.current.start(FILE))
    expect(uploadFile).not.toHaveBeenCalled()
    expect(onConsentRequired).toHaveBeenCalled()
  })
})

describe('useSingleFile', () => {
  it("refuses a file over the size cap without estimating it", async () => {
    const big = new File(['x'], 'big.docx')
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD_BYTES + 1 })
    const onRefused = vi.fn()
    const { result } = renderHook(() => useSingleFile(vi.fn(), onRefused))
    await act(() => result.current.pick(big))
    expect(onRefused).toHaveBeenCalledWith(expect.stringMatching(/Larger than 10 MB/))
    expect(result.current.file).toBeNull()
  })
})
