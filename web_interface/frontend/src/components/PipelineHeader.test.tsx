// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import PipelineHeader from './PipelineHeader'
import ErrorBanner from './ErrorBanner'
import { getInputFileUrl } from '../api/runs'

vi.mock('../api/runs', () => ({ getInputFileUrl: vi.fn() }))
vi.mock('../contexts/AuthContext', () => ({ useCanSeeCost: () => false }))

const RUN_ID = 'ABCDEF'
const PRESIGNED = 'https://s3.example/input/ABCDEF.docx?sig=abc'
const STILL_SCANNING = 'This file is still being scanned for malware. Please try again in a minute.'

// The page's own error pattern: PipelineViewer keeps the message and shows it
// in an ErrorBanner.
function Page() {
  const [error, setError] = useState<string | null>(null)
  return (
    <>
      {error && <ErrorBanner message={error} />}
      <PipelineHeader
        runId={RUN_ID} filename="cv.docx" status="complete" steps={[]} stepProgress={{}}
        displayProgress={100} totalCost={null} inputTokens={0} outputTokens={0}
        elapsedSeconds={0} isCancelling={false} onBack={() => {}} onError={setError}
      />
    </>
  )
}

const clickOriginal = () => act(async () => {
  fireEvent.click(screen.getByRole('button', { name: /Original file/ }))
})

describe('PipelineHeader "Original file"', () => {
  let location: { href: string }
  beforeEach(() => {
    location = { href: 'http://localhost/run/ABCDEF' }
    vi.stubGlobal('location', location)
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.mocked(getInputFileUrl).mockReset() })

  it('navigates to the presigned URL the API returns', async () => {
    vi.mocked(getInputFileUrl).mockResolvedValue(PRESIGNED)
    render(<Page />)
    await clickOriginal()
    expect(getInputFileUrl).toHaveBeenCalledWith(RUN_ID)
    expect(location.href).toBe(PRESIGNED)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('falls back to the plain endpoint when storage has no URL', async () => {
    vi.mocked(getInputFileUrl).mockResolvedValue(null)
    render(<Page />)
    await clickOriginal()
    expect(location.href).toBe(`/api/run/${RUN_ID}/input`)
  })

  it('shows the refusal message on 409 and does not navigate', async () => {
    vi.mocked(getInputFileUrl).mockRejectedValue({ status: 409, message: STILL_SCANNING, code: 'conflict' })
    render(<Page />)
    await clickOriginal()
    expect(screen.getByRole('alert').textContent).toContain(STILL_SCANNING)
    expect(location.href).toBe('http://localhost/run/ABCDEF')
  })

  it('is disabled while the request is in flight', async () => {
    let resolve: (url: string | null) => void = () => {}
    vi.mocked(getInputFileUrl).mockReturnValue(new Promise((r) => { resolve = r }))
    render(<Page />)
    await clickOriginal()
    const button = screen.getByRole('button', { name: /Original file/ }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    await act(async () => { resolve(PRESIGNED) })
    expect(button.disabled).toBe(false)
  })
})
