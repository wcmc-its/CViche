// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import CorrectedUpload from './CorrectedUpload'
import { getCorrectedCopies, getCorrectedCopyUrl, uploadCorrectedDocx } from '../api/feedback'
import type { CorrectedCopyDetail } from '../types'

vi.mock('../api/feedback', () => ({
  uploadCorrectedDocx: vi.fn(),
  getCorrectedCopies: vi.fn(),
  getCorrectedCopyUrl: vi.fn(),
}))

const copy = (over: Partial<CorrectedCopyDetail>): CorrectedCopyDetail => ({
  id: 1, uploader: 'Sam Staff', role: 'staff', uploaded_at: '2026-10-13T14:00:00Z', changes: 3,
  mine: false, download: 'ready', ...over,
})

beforeEach(() => {
  vi.mocked(getCorrectedCopies).mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

// "Help improve CViche" (#1587). Synthetic data only.
describe('CorrectedUpload', () => {
  it('says it is optional and that tracked changes and notes can stay in', async () => {
    render(<CorrectedUpload runId="run-1" />)
    expect(screen.getByText('Optional')).toBeTruthy()
    expect(screen.getByText(/Tracked changes and CViche's notes can stay in/)).toBeTruthy()
    await waitFor(() => expect(getCorrectedCopies).toHaveBeenCalledWith('run-1'))
    expect(screen.queryByText('Corrected copies')).toBeNull()  // none yet: no list
    expect(uploadCorrectedDocx).not.toHaveBeenCalled()
  })

  it('uploads a corrected copy, shows the one-line confirmation and refreshes the list', async () => {
    vi.mocked(uploadCorrectedDocx).mockResolvedValue({ changes: 7, summary: '7 changes recorded' })
    render(<CorrectedUpload runId="run-1" />)
    await waitFor(() => expect(getCorrectedCopies).toHaveBeenCalledTimes(1))
    vi.mocked(getCorrectedCopies).mockResolvedValue([copy({ uploader: 'Pat Example', role: 'owner', mine: true, changes: 7 })])
    const file = new File(['x'], 'corrected.docx')
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [file] } })
    expect((await screen.findByRole('status')).textContent).toBe('7 changes recorded. Thank you.')
    expect(uploadCorrectedDocx).toHaveBeenCalledWith('run-1', file)
    expect(await screen.findByText('Pat Example (owner)')).toBeTruthy()
    expect(getCorrectedCopies).toHaveBeenCalledTimes(2)
  })

  it('shows the server message when the upload is refused', async () => {
    vi.mocked(uploadCorrectedDocx).mockRejectedValue({ status: 400, message: 'Upload a Word file.' })
    render(<CorrectedUpload runId="run-1" />)
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['x'], 'a.pdf')] } })
    expect(await screen.findByText('Upload a Word file.')).toBeTruthy()
    expect(screen.getByTestId('file-input')).toBeTruthy()  // can try again
  })

  it("lists staff's copy to the owner with its uploader, role, date, changes and Download", async () => {
    vi.mocked(getCorrectedCopies).mockResolvedValue([
      copy({ id: 2 }), copy({ id: 1, uploader: 'Pat Example', role: 'owner', mine: true, changes: 1 })])
    render(<CorrectedUpload runId="run-1" />)
    const staffRow = (await screen.findByText('Sam Staff (staff)')).closest('li')!
    expect(staffRow.textContent).toMatch(/Oct 13.* · 3 changes/)
    expect(within(staffRow).getByRole('button', { name: 'Download' })).toBeTruthy()
    expect(screen.getByText('Pat Example (owner)').closest('li')!.textContent).toContain('1 change')
    // The list sits above the drop zone.
    const list = screen.getByText('Corrected copies')
    expect(list.compareDocumentPosition(screen.getByTestId('file-input')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('says a new upload replaces the viewer\'s own copy only when they have one', async () => {
    vi.mocked(getCorrectedCopies).mockResolvedValue([copy({ mine: true })])
    render(<CorrectedUpload runId="run-1" />)
    expect(await screen.findByText(/\(replaces your own copy\)/)).toBeTruthy()
    cleanup()
    vi.mocked(getCorrectedCopies).mockResolvedValue([copy({ mine: false })])
    render(<CorrectedUpload runId="run-1" />)
    await screen.findByText('Sam Staff (staff)')
    expect(screen.queryByText(/\(replaces your own copy\)/)).toBeNull()
  })

  it('shows Scanning… instead of a Download link while the malware scan holds a copy back', async () => {
    vi.mocked(getCorrectedCopies).mockResolvedValue([copy({ download: 'scanning' }),
      copy({ id: 2, uploader: 'Ari Admin', role: 'admin', download: 'threats_found' })])
    render(<CorrectedUpload runId="run-1" />)
    const row = (await screen.findByText('Sam Staff (staff)')).closest('li')!
    expect(within(row).getByText('Scanning…')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Download' })).toBeNull()
    expect(screen.getByText('Flagged by the malware scan')).toBeTruthy()
  })

  it('downloads a ready copy through its URL, and shows a refusal\'s message', async () => {
    const location = { href: '' }
    vi.stubGlobal('location', location)
    vi.mocked(getCorrectedCopies).mockResolvedValue([copy({ id: 4 })])
    vi.mocked(getCorrectedCopyUrl).mockResolvedValueOnce('https://s3.example/copy?sig=1')
    render(<CorrectedUpload runId="run-1" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Download' }))
    await waitFor(() => expect(location.href).toBe('https://s3.example/copy?sig=1'))
    expect(getCorrectedCopyUrl).toHaveBeenCalledWith('run-1', 4)

    vi.mocked(getCorrectedCopyUrl).mockRejectedValueOnce({ status: 409, message: 'This file is still being scanned.' })
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    expect(await screen.findByText('This file is still being scanned.')).toBeTruthy()
  })
})
