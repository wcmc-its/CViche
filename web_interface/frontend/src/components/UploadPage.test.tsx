// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import UploadPage from './UploadPage'
import { getBatchEstimate, getEstimate, uploadFile } from '../api/upload'
import type { UploadOptions, UploadResult } from '../api/upload'
import { getCapacity, startRun } from '../api/runs'
import { createBatch, getQueue } from '../api/batches'
import { getCurrentUser } from '../api/auth'
import type { BatchEstimate, Estimate, QueueOverview, User } from '../types'

vi.mock('../api/upload', () => ({ getEstimate: vi.fn(), getBatchEstimate: vi.fn(), uploadFile: vi.fn() }))
vi.mock('../api/runs', () => ({ startRun: vi.fn(), getCapacity: vi.fn() }))
vi.mock('../api/batches', () => ({ createBatch: vi.fn(), getQueue: vi.fn() }))
vi.mock('../api/auth', () => ({ getCurrentUser: vi.fn() }))

// Invented user; no real names in fixtures.
const ADMIN: User = {
  user_id: 7, email: 'tester@example.org', display_name: 'Test Admin', role: 'admin',
  consent_version: '1.0', default_submission_type: 'authorized_admin', quota: null,
}
let currentUser: User = ADMIN
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: currentUser }),
  useCanSeeCost: () => currentUser.role === 'admin',
}))

// The Counsel-approved text, typed out here on purpose: a test that imported the
// page's constants would pass whatever they said.
const ROLE_A_LABEL = 'I am the faculty member whose CV this is'
const ROLE_A_STATEMENT = 'I agree to upload my CV to this tool.'
const ROLE_B_LABEL = 'I am an administrator uploading on behalf of a faculty member'
const ROLE_B_STATEMENT =
  'I have received permission of the faculty to upload the CV to this tool, and I agree to provide a copy of the modified document to said faculty for their review prior to any submission.'
const BATCH_LEAD_IN = 'This applies to each CV in this batch:'
const RISK =
  "The text of the CV is sent to a third-party AI service (currently Anthropic's Claude on Amazon Bedrock; the provider may change, for example to OpenAI). CViche attempts to withhold highly sensitive personal details such as date of birth or Social Security number, but you should not include anything you would not want these systems to see."
const RETENTION =
  'The original CV, intermediate outputs, and final output are retained to improve CViche and test proposed changes. See the data retention policy.'

const QUEUE: QueueOverview = {
  dispatch_mode: 'queue',
  single: { workers: 6, ahead: 0, est_wait_minutes: 0 },
  batch: { workers: 3, ahead: 2, est_wait_minutes: 25 },
}
const IN_PROCESS: QueueOverview = { dispatch_mode: 'in_process', single: null, batch: null }

const EST: Estimate = {
  document_tokens: 1000, text_characters: 4000, estimated_cost_min: 1, estimated_cost_max: 2,
  estimated_time_seconds_min: 240, estimated_time_seconds_max: 360, num_steps: 12, filename: 'x.docx',
  file_size_kb: 10, pricing_model: 'test-model', text_characters_is_guess: false,
}

function batchEstimate(files: File[]): BatchEstimate {
  return {
    files: files.map((f) => ({ filename: f.name, estimate: EST, error: null })),
    estimated_time_seconds_min: 0, estimated_time_seconds_max: 0, estimated_cost_min: null, estimated_cost_max: null,
    num_steps: 12, pricing_model: null,
  }
}

const docx = (name: string) => new File(['cv'], name)
const docxSet = (n: number) => Array.from({ length: n }, (_, i) => docx(`cv_${i + 1}.docx`))
const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
const ok = (runId: string): UploadResult => ({ run_id: runId, wcm_template_warning: false, wcm_template_match_ratio: null })

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

const onUploadSuccess = vi.fn()

async function renderPage(queue: QueueOverview = QUEUE) {
  vi.mocked(getQueue).mockResolvedValue(queue)
  render(<MemoryRouter><UploadPage onUploadSuccess={onUploadSuccess} /></MemoryRouter>)
  await flush()
}

const fileInput = () => screen.getByTestId('file-input') as HTMLInputElement

async function addFiles(files: File[]) {
  fireEvent.change(fileInput(), { target: { files } })
  await flush()
}

const tickAttestation = () => fireEvent.click(screen.getByRole('checkbox', { name: /I (have received|agree)/ }))

async function submitBatch(files: File[]) {
  await addFiles(files)
  tickAttestation()
  fireEvent.click(screen.getByRole('button', { name: `Submit ${files.length} CVs` }))
  await flush()
}

/** Each upload gets run id R<n> in call order. */
function uploadsSucceed() {
  let n = 0
  vi.mocked(uploadFile).mockImplementation(async () => { n += 1; return ok(`R${n}`) })
}

beforeEach(() => {
  currentUser = ADMIN
  vi.mocked(getBatchEstimate).mockImplementation(async (files) => batchEstimate(files))
  vi.mocked(getEstimate).mockResolvedValue(EST)
  vi.mocked(getCapacity).mockResolvedValue({ available: true, active: 0, limit: 6 })
  vi.mocked(createBatch).mockResolvedValue({ id: 'BQXZKD' })
  vi.mocked(startRun).mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('UploadPage gating', () => {
  it('offers several files only on behalf of faculty and only in queue mode', async () => {
    await renderPage(QUEUE)
    expect(fileInput().multiple).toBe(true)
    expect(screen.getByText(/Drop \.docx files here/)).toBeTruthy()

    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    expect(fileInput().multiple).toBe(false)
  })

  it('keeps the single-file page when the backend is not in queue mode', async () => {
    await renderPage(IN_PROCESS)
    expect(fileInput().multiple).toBe(false)
    expect(screen.getByText(/Drop a \.docx or \.pdf file here/)).toBeTruthy()
  })

  it('treats a failed queue probe as no queue', async () => {
    vi.mocked(getQueue).mockRejectedValue({ status: 500, message: 'boom' })
    render(<MemoryRouter><UploadPage onUploadSuccess={onUploadSuccess} /></MemoryRouter>)
    await flush()
    expect(fileInput().multiple).toBe(false)
  })
})

describe('UploadPage approved text', () => {
  it('renders the Role A statement, risk disclosure and retention summary word for word', async () => {
    await renderPage()
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    expect(screen.getByLabelText(ROLE_B_LABEL)).toBeTruthy()
    expect(screen.getByTestId('attestation-text').textContent).toBe(ROLE_A_STATEMENT)
    expect(screen.getByTestId('risk-disclosure').textContent).toBe(RISK)
    expect(screen.getByTestId('retention-summary').textContent).toBe(RETENTION)
    const link = within(screen.getByTestId('retention-summary')).getByRole('link', { name: 'data retention policy' })
    expect(link.getAttribute('href')).toBe('/help#data-retention')
    expect(screen.queryByTestId('attestation-lead-in')).toBeNull()
  })

  it('adds the batch lead-in above the unchanged Role B statement for several files', async () => {
    await renderPage()
    await addFiles([docx('a.docx')])
    expect(screen.getByTestId('attestation-text').textContent).toBe(ROLE_B_STATEMENT)
    expect(screen.queryByTestId('attestation-lead-in')).toBeNull()

    await addFiles([docx('b.docx')])
    expect(screen.getByTestId('attestation-lead-in').textContent).toBe(BATCH_LEAD_IN)
    expect(screen.getByTestId('attestation-text').textContent).toBe(ROLE_B_STATEMENT)
  })

  it('offers no track changes control', async () => {
    await renderPage()
    expect(screen.queryByText(/track changes/i)).toBeNull()
  })
})

describe('UploadPage batch selection', () => {
  it('estimates the whole selection in one call and skips non-.docx files', async () => {
    await renderPage()
    await addFiles([docx('a.docx'), docx('b.docx'), new File(['x'], 'c.pdf')])
    expect(getBatchEstimate).toHaveBeenCalledTimes(1)
    expect(vi.mocked(getBatchEstimate).mock.calls[0][0].map((f) => f.name)).toEqual(['a.docx', 'b.docx'])
    expect(screen.getByText('2 to submit · 1 skipped')).toBeTruthy()
    expect(screen.getByText("Won't be submitted")).toBeTruthy()
    expect(screen.getByText('Not a .docx file')).toBeTruthy()
  })

  it('marks a file the estimate refused as won\'t be submitted, with the server reason', async () => {
    vi.mocked(getBatchEstimate).mockImplementation(async (files) => ({
      ...batchEstimate(files),
      files: files.map((f, i) => (i === 1
        ? { filename: f.name, estimate: null, error: 'File too large. Maximum size is 10 MB.' }
        : { filename: f.name, estimate: EST, error: null })),
    }))
    await renderPage()
    await addFiles([docx('a.docx'), docx('big.docx')])
    expect(screen.getByText('File too large. Maximum size is 10 MB.')).toBeTruthy()
    expect(screen.getByText('1 to submit · 1 skipped')).toBeTruthy()
  })

  it('blocks more than 50 files and estimates no more than 50', async () => {
    await renderPage()
    await addFiles(docxSet(51))
    expect(vi.mocked(getBatchEstimate).mock.calls[0][0]).toHaveLength(50)
    tickAttestation()
    expect(screen.getByText('Batches are limited to 50 files. Remove 1.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Submit 51 CVs' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('blocks a non-admin batch larger than the runs left today', async () => {
    currentUser = { ...ADMIN, role: 'user' }
    vi.mocked(getCurrentUser).mockResolvedValue({
      ...currentUser,
      quota: { daily_limit: 10, daily_used: 9, daily_remaining: 1, monthly_limit: 50, monthly_used: 12, monthly_remaining: 38, is_admin: false },
    })
    await renderPage()
    await addFiles(docxSet(3))
    tickAttestation()
    expect(screen.getByText('1 of 10 runs left today · 38 of 50 this month')).toBeTruthy()
    expect(screen.getByText('You have 1 run left today. Remove 2 files, or submit the rest tomorrow.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Submit 3 CVs' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows the batch lane finish line', async () => {
    await renderPage()
    await addFiles(docxSet(2))
    // 2 files x 5 min each, 3 workers, 25 min already waiting: 25 + 10/3 = 28 min.
    expect(screen.getByText('Runs go into a shared queue behind 2 others and run 3 at a time. Expect the last to finish in about 28 min.')).toBeTruthy()
  })
})

describe('UploadPage batch upload wire', () => {
  it('creates the batch first, then uploads each file with its batch id and starts it', async () => {
    const calls: string[] = []
    vi.mocked(createBatch).mockImplementation(async (n) => { calls.push(`batch:${n}`); return { id: 'BQXZKD' } })
    let n = 0
    vi.mocked(uploadFile).mockImplementation(async (file: File, opts: UploadOptions) => {
      n += 1
      calls.push(`upload:${file.name}:${opts.batchId}:${opts.submissionType}`)
      return ok(`R${n}`)
    })
    vi.mocked(startRun).mockImplementation(async (id) => { calls.push(`start:${id}`) })
    await renderPage()
    await addFiles([docx('a.docx'), new File(['x'], 'skip.pdf'), docx('b.docx')])
    tickAttestation()
    fireEvent.click(screen.getByRole('button', { name: 'Submit 2 CVs' }))
    await flush()

    expect(calls[0]).toBe('batch:2')
    expect(calls.filter((c) => c.startsWith('upload'))).toEqual([
      'upload:a.docx:BQXZKD:authorized_admin',
      'upload:b.docx:BQXZKD:authorized_admin',
    ])
    expect(calls.indexOf('start:R1')).toBeGreaterThan(calls.indexOf('upload:a.docx:BQXZKD:authorized_admin'))
    expect(calls).toContain('start:R2')
    expect(screen.getByText('All 2 CVs queued')).toBeTruthy()
  })

  it('keeps at most two uploads in flight', async () => {
    const pending: { resolve: (r: UploadResult) => void }[] = []
    vi.mocked(uploadFile).mockImplementation(() => {
      const d = deferred<UploadResult>()
      pending.push(d)
      return d.promise
    })
    await renderPage()
    await submitBatch(docxSet(4))
    expect(uploadFile).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Uploading 0 of 4')).toBeTruthy()
    expect(screen.getByText('Two files at a time')).toBeTruthy()

    await act(async () => { pending[0].resolve(ok('R1')) })
    await flush()
    expect(uploadFile).toHaveBeenCalledTimes(3)

    await act(async () => { pending.slice(1).forEach((d, i) => d.resolve(ok(`R${i + 2}`))) })
    await flush()
    await act(async () => { pending[3]?.resolve(ok('R4')) })
    await flush()
    expect(uploadFile).toHaveBeenCalledTimes(4)
  })

  it('retries a failed start with the same run id and never re-uploads', async () => {
    uploadsSucceed()
    vi.mocked(startRun).mockImplementation(async (id) => {
      if (id === 'R2' && vi.mocked(startRun).mock.calls.filter(([c]) => c === 'R2').length === 1) {
        throw { status: 503, message: 'Service unavailable' }
      }
    })
    await renderPage()
    await submitBatch(docxSet(2))
    expect(screen.getByText('1 of 2 CVs queued')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Retry 1 failed upload' }))
    await flush()
    expect(uploadFile).toHaveBeenCalledTimes(2)
    expect(vi.mocked(startRun).mock.calls.map(([id]) => id)).toEqual(['R1', 'R2', 'R2'])
    expect(screen.getByText('All 2 CVs queued')).toBeTruthy()
  })

  it('re-uploads a file whose upload itself failed with a network error', async () => {
    let n = 0
    vi.mocked(uploadFile).mockImplementation(async () => {
      n += 1
      if (n === 2) throw new TypeError('Failed to fetch')
      return ok(`R${n}`)
    })
    await renderPage()
    await submitBatch(docxSet(2))
    expect(screen.getByText('Upload interrupted')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry 1 failed upload' }))
    await flush()
    expect(uploadFile).toHaveBeenCalledTimes(3)
    expect(screen.getByText('All 2 CVs queued')).toBeTruthy()
  })

  it('offers no retry for a 4xx validation failure and says to upload it on its own', async () => {
    let n = 0
    vi.mocked(uploadFile).mockImplementation(async () => {
      n += 1
      if (n === 1) throw { status: 400, message: "Couldn't open the file; it may be corrupt" }
      return ok(`R${n}`)
    })
    await renderPage()
    await submitBatch(docxSet(2))
    expect(screen.getByText("Couldn't open the file; it may be corrupt. Fix it and upload it on its own.")).toBeTruthy()
    expect(screen.getByText("1 file couldn't be opened. Fix it and upload it on its own.")).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('warns before leaving while uploads are in flight, and not after', async () => {
    const pending: { resolve: (r: UploadResult) => void }[] = []
    vi.mocked(uploadFile).mockImplementation(() => {
      const d = deferred<UploadResult>()
      pending.push(d)
      return d.promise
    })
    await renderPage()
    await submitBatch(docxSet(2))
    const during = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(during)
    expect(during.defaultPrevented).toBe(true)

    await act(async () => { pending.forEach((d, i) => d.resolve(ok(`R${i + 1}`))) })
    await flush()
    const after = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(after)
    expect(after.defaultPrevented).toBe(false)
  })

  it('shows the done body with the runs ahead and the refreshed finish time', async () => {
    uploadsSucceed()
    await renderPage()
    vi.mocked(getQueue).mockResolvedValue({ ...QUEUE, batch: { workers: 3, ahead: 4, est_wait_minutes: 40 } })
    await submitBatch(docxSet(2))
    expect(screen.getByText(
      'They run one after another behind 2 runs already in the queue. The last should finish in about 40 min. You can close this tab.',
    )).toBeTruthy()
    expect(screen.getByRole('button', { name: 'View batch in Runs' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Start another batch' })).toBeTruthy()
  })

  it('runs one file on behalf of faculty as a single run, with no batch', async () => {
    uploadsSucceed()
    await renderPage()
    await addFiles([docx('a.docx')])
    tickAttestation()
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }))
    await flush()
    expect(createBatch).not.toHaveBeenCalled()
    expect(vi.mocked(uploadFile).mock.calls[0][1].batchId).toBeUndefined()
    expect(onUploadSuccess).toHaveBeenCalledWith('R1')
  })

  it('keeps the own-CV flow on one file with no batch', async () => {
    uploadsSucceed()
    await renderPage()
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    await addFiles([docx('mine.docx')])
    tickAttestation()
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }))
    await flush()
    expect(createBatch).not.toHaveBeenCalled()
    expect(vi.mocked(uploadFile).mock.calls[0][1]).toEqual({ stripWcmInstructions: true, submissionType: 'own_cv' })
    expect(startRun).toHaveBeenCalledWith('R1')
    expect(onUploadSuccess).toHaveBeenCalledWith('R1')
  })
})
