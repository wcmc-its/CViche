// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import CorrectedUpload from './CorrectedUpload'
import { uploadCorrectedDocx } from '../api/feedback'

vi.mock('../api/feedback', () => ({ uploadCorrectedDocx: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// "Help improve CViche" (#1587). Synthetic data only.
describe('CorrectedUpload', () => {
  it('says it is optional and that tracked changes and notes can stay in', () => {
    render(<CorrectedUpload runId="run-1" />)
    expect(screen.getByText('Optional')).toBeTruthy()
    expect(screen.getByText(/Tracked changes and CViche's notes can stay in/)).toBeTruthy()
    expect(uploadCorrectedDocx).not.toHaveBeenCalled()
  })

  it('uploads a corrected copy and shows only the one-line confirmation', async () => {
    vi.mocked(uploadCorrectedDocx).mockResolvedValue({ changes: 7, summary: '7 changes recorded' })
    render(<CorrectedUpload runId="run-1" />)
    const file = new File(['x'], 'corrected.docx')
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [file] } })
    expect((await screen.findByRole('status')).textContent).toBe('7 changes recorded. Thank you.')
    expect(uploadCorrectedDocx).toHaveBeenCalledWith('run-1', file)
  })

  it('shows the server message when the upload is refused', async () => {
    vi.mocked(uploadCorrectedDocx).mockRejectedValue({ status: 400, message: 'Upload a Word file.' })
    render(<CorrectedUpload runId="run-1" />)
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['x'], 'a.pdf')] } })
    expect(await screen.findByText('Upload a Word file.')).toBeTruthy()
    expect(screen.getByTestId('file-input')).toBeTruthy()  // can try again
  })
})
