// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import UploadPage from './UploadPage'
import { SingleEstimate } from './upload/UploadSections'
import { getBatchEstimate, getEstimate, uploadFile } from '../api/upload'
import type { UploadOptions, UploadResult } from '../api/upload'
import { getCapacity, getRunStatus, startRun } from '../api/runs'
import { createBatch, getQueue } from '../api/batches'
import { getCurrentUser } from '../api/auth'
import { discardInboxItem, listInbox, submitInboxItem } from '../api/inbox'
import { InboxProvider } from '../contexts/InboxContext'
import type { BatchEstimate, Estimate, InboxItem, InboxSubmitResult, QueueOverview, QuotaInfo, RunStatus, User } from '../types'

vi.mock('../api/upload', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/upload')>()),
  getEstimate: vi.fn(), getBatchEstimate: vi.fn(), uploadFile: vi.fn(),
}))
vi.mock('../api/runs', () => ({ startRun: vi.fn(), getCapacity: vi.fn(), getRunStatus: vi.fn() }))
vi.mock('../api/batches', () => ({ createBatch: vi.fn(), getQueue: vi.fn() }))
vi.mock('../api/auth', () => ({ getCurrentUser: vi.fn() }))
vi.mock('../api/inbox', () => ({ listInbox: vi.fn(), discardInboxItem: vi.fn(), submitInboxItem: vi.fn() }))

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
  completion_email_available: false,
}
const IN_PROCESS: QueueOverview = { dispatch_mode: 'in_process', single: null, batch: null, completion_email_available: false }

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
  render(<MemoryRouter><InboxProvider><UploadPage onUploadSuccess={onUploadSuccess} /></InboxProvider></MemoryRouter>)
  await flush()
}

const fileInput = () => screen.getByTestId('file-input') as HTMLInputElement

async function addFiles(files: File[]) {
  fireEvent.change(fileInput(), { target: { files } })
  await flush()
}

const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement
const attestationBox = () => screen.getByRole('checkbox', { name: /I (have received|agree)/ }) as HTMLInputElement
const QUOTA_LEFT: QuotaInfo = {
  daily_limit: 10, daily_used: 0, daily_remaining: 10, monthly_limit: 50, monthly_used: 0, monthly_remaining: 50, is_admin: false,
}
const MEMBER: User = { ...ADMIN, role: 'user', display_name: 'Test Member' }
const runStatus = (status: string) => ({ run_id: 'R', filename: 'x.docx', status }) as RunStatus

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
  vi.mocked(listInbox).mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('UploadPage gating', () => {
  it('offers several files only on behalf of faculty and only in queue mode', async () => {
    await renderPage(QUEUE)
    expect(fileInput().multiple).toBe(true)
    expect(screen.getByText(/Drop \.docx or \.pdf files here/)).toBeTruthy()

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
    render(<MemoryRouter><InboxProvider><UploadPage onUploadSuccess={onUploadSuccess} /></InboxProvider></MemoryRouter>)
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
  it('estimates the whole selection in one call, PDFs included, and skips other types (#1273)', async () => {
    await renderPage()
    await addFiles([docx('a.docx'), new File(['x'], 'b.pdf'), new File(['x'], 'c.doc')])
    expect(getBatchEstimate).toHaveBeenCalledTimes(1)
    expect(vi.mocked(getBatchEstimate).mock.calls[0][0].map((f) => f.name)).toEqual(['a.docx', 'b.pdf'])
    expect(screen.getByText('2 to submit · 1 skipped')).toBeTruthy()
    expect(screen.getByText("Won't be submitted")).toBeTruthy()
    expect(screen.getByText('Not a .docx or .pdf file')).toBeTruthy()
  })

  it("shows a PDF row's scanned pages from its estimate (#1282)", async () => {
    vi.mocked(getBatchEstimate).mockImplementation(async (files) => ({
      ...batchEstimate(files),
      files: files.map((f) => ({ filename: f.name, estimate: { ...EST, scanned_pages: [2] }, error: null })),
    }))
    await renderPage()
    await addFiles([new File(['x'], 'scan.pdf')])
    expect(screen.getByText(/Page 2 of this PDF is a scanned image/)).toBeTruthy()
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
    await addFiles([docx('a.docx'), new File(['x'], 'skip.doc'), new File(['x'], 'b.pdf')])
    tickAttestation()
    fireEvent.click(screen.getByRole('button', { name: 'Submit 2 CVs' }))
    await flush()

    expect(calls[0]).toBe('batch:2')
    expect(calls.filter((c) => c.startsWith('upload'))).toEqual([
      'upload:a.docx:BQXZKD:authorized_admin',
      'upload:b.pdf:BQXZKD:authorized_admin',
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

const DUPLICATE_MESSAGE = 'This file was already processed on March 4, 2026. Run it again?'
const duplicateError = { status: 409, code: 'duplicate_file', message: DUPLICATE_MESSAGE }

describe('UploadPage duplicate file confirmation (#1286)', () => {
  it('holds a single duplicate, then re-uploads with confirmDuplicate on the next click', async () => {
    vi.mocked(uploadFile).mockRejectedValueOnce(duplicateError).mockResolvedValueOnce(ok('R1'))
    await renderPage(IN_PROCESS)
    await addFiles([docx('a.docx')])
    tickAttestation()
    fireEvent.click(button('Start run'))
    await flush()

    expect(screen.getByRole('alert', { name: 'Duplicate file notice' }).textContent).toContain(DUPLICATE_MESSAGE)
    expect(startRun).not.toHaveBeenCalled()
    expect(vi.mocked(uploadFile).mock.calls[0][1].confirmDuplicate).toBeFalsy()

    fireEvent.click(button('Run it again'))
    await flush()
    expect(vi.mocked(uploadFile).mock.calls[1][1].confirmDuplicate).toBe(true)
    expect(startRun).toHaveBeenCalledWith('R1')
    expect(onUploadSuccess).toHaveBeenCalledWith('R1')
  })

  it('holds a duplicate batch row, queues the rest, and re-sends only that row on Run it again', async () => {
    let n = 0
    vi.mocked(uploadFile).mockImplementation(async (file: File, opts: UploadOptions) => {
      if (file.name === 'cv_1.docx' && !opts.confirmDuplicate) throw duplicateError
      n += 1
      return ok(`R${n}`)
    })
    await renderPage()
    await submitBatch(docxSet(2))

    expect(screen.getByText(DUPLICATE_MESSAGE)).toBeTruthy()
    expect(screen.queryByText(/Fix it and upload it on its own/)).toBeNull()
    expect(startRun).toHaveBeenCalledTimes(1)

    fireEvent.click(button('Run it again'))
    await flush()
    const resent = vi.mocked(uploadFile).mock.calls.filter(([, o]) => o.confirmDuplicate)
    expect(resent.map(([f, o]) => [f.name, o.batchId])).toEqual([['cv_1.docx', 'BQXZKD']])
    expect(startRun).toHaveBeenCalledTimes(2)
    expect(screen.getByText('All 2 CVs queued')).toBeTruthy()
  })
})

describe('UploadPage held run vs a changed file list', () => {
  it('forgets a blank-template run when its file is removed, and uploads the new file', async () => {
    vi.mocked(uploadFile).mockImplementation(async (file: File) => (
      file.name === 'blank.docx' ? { ...ok('R1'), wcm_template_warning: true } : ok('R2')
    ))
    await renderPage()
    await addFiles([docx('blank.docx')])
    tickAttestation()
    fireEvent.click(button('Start run'))
    await flush()
    expect(button('Process anyway')).toBeTruthy()

    fireEvent.click(button('Remove blank.docx'))
    expect(screen.queryByRole('alert', { name: 'Blank template warning' })).toBeNull()
    await addFiles([docx('real.docx')])
    fireEvent.click(button('Start run'))
    await flush()
    expect(vi.mocked(uploadFile).mock.calls.map(([f]) => f.name)).toEqual(['blank.docx', 'real.docx'])
    expect(vi.mocked(startRun).mock.calls.map(([id]) => id)).toEqual(['R2'])
    expect(onUploadSuccess).toHaveBeenCalledWith('R2')
  })

  it('stops offering Retry for a failed one-file start once another file is added', async () => {
    uploadsSucceed()
    vi.mocked(startRun).mockRejectedValueOnce({ status: 503, message: 'Service unavailable' })
    await renderPage()
    await addFiles([docx('a.docx')])
    tickAttestation()
    fireEvent.click(button('Start run'))
    await flush()
    expect(button('Retry')).toBeTruthy()

    await addFiles([docx('b.docx')])
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(button('Submit 2 CVs').disabled).toBe(false)
  })
})

describe('UploadPage batch options and failures', () => {
  it('sends the strip choice with every file of the batch', async () => {
    uploadsSucceed()
    await renderPage()
    await addFiles(docxSet(2))
    fireEvent.click(screen.getByRole('checkbox', { name: /Strip WCM template instructions/ }))
    tickAttestation()
    fireEvent.click(button('Submit 2 CVs'))
    await flush()
    expect(vi.mocked(uploadFile).mock.calls.map(([, opts]) => opts.stripWcmInstructions)).toEqual([false, false])
  })

  it('unticks the agreement when the role changes, since the statement changes', async () => {
    await renderPage()
    tickAttestation()
    expect(attestationBox().checked).toBe(true)
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    expect(attestationBox().checked).toBe(false)
  })

  it('carries only a file that can be submitted across to My own CV', async () => {
    await renderPage()
    await addFiles([new File(['x'], 'notes.txt'), docx('a.docx')])
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    expect(screen.getByRole('button', { name: 'Remove a.docx' })).toBeTruthy()
    expect(screen.queryByText('notes.txt')).toBeNull()
  })

  it('retries only the failures that can be retried', async () => {
    let n = 0
    vi.mocked(uploadFile).mockImplementation(async () => {
      n += 1
      if (n === 1) throw { status: 400, message: "Couldn't open the file" }
      if (n === 2) throw new TypeError('Failed to fetch')
      return ok(`R${n}`)
    })
    await renderPage()
    await submitBatch(docxSet(3))
    fireEvent.click(button('Retry 1 failed upload'))
    await flush()
    expect(vi.mocked(uploadFile).mock.calls.map(([f]) => f.name)).toEqual(['cv_1.docx', 'cv_2.docx', 'cv_3.docx', 'cv_2.docx'])
  })

  it('returns to the file list with the reason when the batch cannot be created', async () => {
    vi.mocked(createBatch).mockRejectedValue({ status: 429, message: 'You have 1 run left today.' })
    await renderPage()
    await submitBatch(docxSet(2))
    expect(uploadFile).not.toHaveBeenCalled()
    expect(screen.getByText('You have 1 run left today.')).toBeTruthy()
    expect(button('Submit 2 CVs').disabled).toBe(false)
  })

  it('counts a start refused because an earlier, unanswered start got through as queued', async () => {
    uploadsSucceed()
    let r2Starts = 0
    vi.mocked(startRun).mockImplementation(async (id) => {
      if (id !== 'R2') return
      r2Starts += 1
      if (r2Starts === 1) throw new TypeError('Failed to fetch')
      throw { status: 400, message: 'Cannot start run in status: running' }
    })
    vi.mocked(getRunStatus).mockResolvedValue(runStatus('running'))
    await renderPage()
    await submitBatch(docxSet(2))
    fireEvent.click(button('Retry 1 failed upload'))
    await flush()
    expect(getRunStatus).toHaveBeenCalledWith('R2')
    expect(uploadFile).toHaveBeenCalledTimes(2)
    expect(screen.getByText('All 2 CVs queued')).toBeTruthy()
    expect(screen.queryByText(/couldn't be opened/)).toBeNull()
  })

  it('keeps a refused start failed when the run never left created', async () => {
    uploadsSucceed()
    vi.mocked(startRun).mockImplementation(async (id) => {
      if (id === 'R2') throw { status: 409, message: 'This run was already started by another request.' }
    })
    vi.mocked(getRunStatus).mockResolvedValue(runStatus('created'))
    await renderPage()
    await submitBatch(docxSet(2))
    expect(screen.getByText('1 of 2 CVs queued')).toBeTruthy()
  })
})

describe('UploadPage cost is for admins only', () => {
  it('shows each file\'s cost and the total to an admin', async () => {
    await renderPage()
    await addFiles(docxSet(2))
    expect(screen.getAllByText('$1.50')).toHaveLength(2)
    expect(screen.getByText('2 CVs · about 10 min of processing · ~$3.00')).toBeTruthy()
  })

  it('hides every cost from a non-admin, even if an estimate carries one', async () => {
    currentUser = MEMBER
    vi.mocked(getCurrentUser).mockResolvedValue({ ...MEMBER, quota: QUOTA_LEFT })
    await renderPage()
    await addFiles(docxSet(2))
    expect(screen.getByText('2 CVs · about 10 min of processing')).toBeTruthy()
    expect(screen.queryByText(/\$/)).toBeNull()
  })

  it('hides the single-run cost from a non-admin', async () => {
    currentUser = MEMBER
    vi.mocked(getCurrentUser).mockResolvedValue({ ...MEMBER, quota: QUOTA_LEFT })
    await renderPage()
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    await addFiles([docx('mine.docx')])
    expect(screen.getByLabelText('Processing estimate').textContent).toContain('Estimated time')
    expect(screen.queryByText(/\$/)).toBeNull()
  })

  it('shows the single-run cost to an admin', async () => {
    await renderPage()
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    await addFiles([docx('mine.docx')])
    expect(screen.getByLabelText('Processing estimate').textContent).toContain('Estimated cost: $1.00 - $2.00')
  })
})

describe('UploadPage quota', () => {
  it('re-reads the runs left before another batch', async () => {
    currentUser = MEMBER
    vi.mocked(getCurrentUser).mockResolvedValue({ ...MEMBER, quota: QUOTA_LEFT })
    uploadsSucceed()
    await renderPage()
    expect(getCurrentUser).toHaveBeenCalledTimes(1)
    await submitBatch(docxSet(2))
    vi.mocked(getCurrentUser).mockResolvedValue({ ...MEMBER, quota: { ...QUOTA_LEFT, daily_remaining: 8 } })
    fireEvent.click(button('Start another batch'))
    await flush()
    expect(getCurrentUser).toHaveBeenCalledTimes(2)
    expect(screen.getByText('8 of 10 runs left today · 50 of 50 this month')).toBeTruthy()
  })
})

describe('SingleEstimate scanned pages (#1282)', () => {
  it('names the PDF pages whose text cannot be read', () => {
    render(<SingleEstimate estimate={{ ...EST, scanned_pages: [3, 5] }} showCost={false} />)
    expect(screen.getByText(/Pages 3, 5 of this PDF are scanned images/)).toBeTruthy()
  })

  it('says nothing when no page is scanned', () => {
    render(<SingleEstimate estimate={EST} showCost={false} />)
    expect(screen.queryByText(/scanned image/)).toBeNull()
  })
})

// Invented emailed-CV fixtures (#1298).
const heldItem = (id: number, filename: string, duplicate: InboxItem['duplicate'] = null): InboxItem => (
  { id, filename, size_bytes: 2048, received_at: '2026-10-01T12:00:00Z', duplicate }
)
const INBOX_ITEMS = [
  heldItem(11, 'emailed_one.docx'),
  heldItem(12, 'emailed_two.pdf', { last_processed_on: 'September 3, 2026', run_id: null }),
]
const submitted = (id: number, runId: string): InboxSubmitResult =>
  ({ id, status: 'submitted', run_id: runId, error: null, message: null, last_processed_on: null })
const refused = (id: number): InboxSubmitResult => (
  { id, status: 'failed', run_id: null, error: 'duplicate_file', message: DUPLICATE_MESSAGE, last_processed_on: 'September 3, 2026' }
)

describe('UploadPage emailed CV inbox (#1298)', () => {
  it('lists held emailed CVs above the drop zone with a duplicate note, and nothing when there are none', async () => {
    await renderPage()
    expect(screen.queryByText(/Emailed to CViche/)).toBeNull()
    cleanup()
    vi.mocked(listInbox).mockResolvedValue(INBOX_ITEMS)
    await renderPage()
    expect(screen.getByRole('heading', { name: /Emailed to CViche \(2\)/ })).toBeTruthy()
    const list = within(screen.getByRole('list', { name: 'Emailed CVs' }))
    expect(list.getByTitle('emailed_one.docx')).toBeTruthy()
    expect(list.getByText('Already processed on September 3, 2026')).toBeTruthy()
    expect(list.getAllByText(/Already processed/)).toHaveLength(1)
  })

  it('discards one item and re-reads the list', async () => {
    vi.mocked(listInbox).mockResolvedValueOnce(INBOX_ITEMS).mockResolvedValue([INBOX_ITEMS[1]])
    vi.mocked(discardInboxItem).mockResolvedValue(undefined)
    await renderPage()
    fireEvent.click(button('Discard emailed_one.docx'))
    await flush()
    expect(discardInboxItem).toHaveBeenCalledWith(11)
    expect(screen.getByRole('heading', { name: /Emailed to CViche \(1\)/ })).toBeTruthy()
  })

  it('loads items into the batch table, and submits them through /inbox/submit, never /upload', async () => {
    vi.mocked(listInbox).mockResolvedValue([INBOX_ITEMS[0]])
    vi.mocked(submitInboxItem).mockResolvedValue(submitted(11, 'RM1'))
    await renderPage()
    fireEvent.click(button('Add emailed_one.docx'))
    await flush()
    expect(screen.queryByRole('heading', { name: /Emailed to CViche/ })).toBeNull()
    expect(screen.getAllByTestId('batch-row')).toHaveLength(1)
    tickAttestation()
    fireEvent.click(button('Start run'))
    await flush()
    expect(createBatch).toHaveBeenCalledWith(1, { notifyOnComplete: false })
    expect(submitInboxItem).toHaveBeenCalledWith(11, expect.objectContaining({ batchId: 'BQXZKD', submissionType: 'authorized_admin', stripWcmInstructions: true }))
    expect(uploadFile).not.toHaveBeenCalled()
    expect(startRun).toHaveBeenCalledWith('RM1')
    expect(screen.getByText('All 1 CVs queued')).toBeTruthy()
  })

  it('holds a refused duplicate for Run it again, which resends that item with confirmDuplicate', async () => {
    vi.mocked(listInbox).mockResolvedValue(INBOX_ITEMS)
    vi.mocked(submitInboxItem).mockImplementation(async (id, opts) => (
      id === 12 && !opts.confirmDuplicate ? refused(12) : submitted(id, `RM${id}`)
    ))
    await renderPage()
    fireEvent.click(button('Add all 2 to this batch'))
    await flush()
    tickAttestation()
    fireEvent.click(button('Submit 2 CVs'))
    await flush()
    expect(screen.getByText(DUPLICATE_MESSAGE)).toBeTruthy()
    expect(startRun).toHaveBeenCalledTimes(1)
    fireEvent.click(button('Run it again'))
    await flush()
    const resent = vi.mocked(submitInboxItem).mock.calls.filter(([, o]) => o.confirmDuplicate)
    expect(resent.map(([id]) => id)).toEqual([12])
    expect(startRun).toHaveBeenCalledTimes(2)
    expect(uploadFile).not.toHaveBeenCalled()
  })

  it('refreshes the inbox after a submit', async () => {
    vi.mocked(listInbox).mockResolvedValue([INBOX_ITEMS[0]])
    vi.mocked(submitInboxItem).mockResolvedValue(submitted(11, 'RM1'))
    await renderPage()
    const before = vi.mocked(listInbox).mock.calls.length
    fireEvent.click(button('Add emailed_one.docx'))
    await flush()
    tickAttestation()
    fireEvent.click(button('Start run'))
    await flush()
    expect(vi.mocked(listInbox).mock.calls.length).toBeGreaterThan(before)
  })

  it('on My own CV loads one item into the single-file flow and submits it as own_cv, with no batch', async () => {
    vi.mocked(listInbox).mockResolvedValue(INBOX_ITEMS)
    vi.mocked(submitInboxItem).mockResolvedValue(submitted(11, 'RM1'))
    await renderPage()
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    expect(screen.queryByRole('button', { name: /Add all/ })).toBeNull()
    fireEvent.click(button('Add emailed_one.docx'))
    await flush()
    // The own-CV attestation stays; the on-behalf wording and the batch table never appear.
    expect(screen.getByText(ROLE_A_STATEMENT)).toBeTruthy()
    expect(screen.queryByText(ROLE_B_STATEMENT)).toBeNull()
    expect(screen.queryByText(BATCH_LEAD_IN)).toBeNull()
    expect(screen.queryByTestId('batch-row')).toBeNull()
    expect(screen.getByRole('heading', { name: /Emailed to CViche \(1\)/ })).toBeTruthy()
    tickAttestation()
    fireEvent.click(button('Start run'))
    await flush()
    expect(createBatch).not.toHaveBeenCalled()
    expect(uploadFile).not.toHaveBeenCalled()
    expect(submitInboxItem).toHaveBeenCalledWith(11, { stripWcmInstructions: true, submissionType: 'own_cv', confirmDuplicate: false })
    expect(startRun).toHaveBeenCalledWith('RM1')
    expect(onUploadSuccess).toHaveBeenCalledWith('RM1')
  })

  it('on My own CV holds a refused duplicate for Run it again, then confirms it', async () => {
    vi.mocked(listInbox).mockResolvedValue(INBOX_ITEMS)
    vi.mocked(submitInboxItem).mockResolvedValueOnce(refused(12)).mockResolvedValueOnce(submitted(12, 'RM2'))
    await renderPage()
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    fireEvent.click(button('Add emailed_two.pdf'))
    await flush()
    tickAttestation()
    fireEvent.click(button('Start run'))
    await flush()
    expect(screen.getByRole('alert', { name: 'Duplicate file notice' }).textContent).toContain(DUPLICATE_MESSAGE)
    fireEvent.click(button('Run it again'))
    await flush()
    expect(vi.mocked(submitInboxItem).mock.calls[1][1].confirmDuplicate).toBe(true)
    expect(onUploadSuccess).toHaveBeenCalledWith('RM2')
  })

  it('returns a removed single item to the list, and a role switch carries it into the batch', async () => {
    vi.mocked(listInbox).mockResolvedValue(INBOX_ITEMS)
    await renderPage()
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    fireEvent.click(button('Add emailed_one.docx'))
    await flush()
    fireEvent.click(screen.getByLabelText(ROLE_B_LABEL))
    await flush()
    expect(screen.getAllByTestId('batch-row')).toHaveLength(1)
    fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
    await flush()
    expect(screen.queryByTestId('batch-row')).toBeNull()
    fireEvent.click(button('Remove emailed_one.docx'))
    expect(screen.getByRole('heading', { name: /Emailed to CViche \(2\)/ })).toBeTruthy()
  })
})

describe('UploadPage "Email me when job completes" (#1335)', () => {
  const EMAIL_LABEL = 'Email me when job completes'
  const emailBox = () => screen.getByRole('checkbox', { name: EMAIL_LABEL }) as HTMLInputElement
  const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

  it('sits unticked below the attestation, in the batch and the single-file layout, when mail can be sent', async () => {
    await renderPage({ ...QUEUE, completion_email_available: true })
    for (const layout of ['batch', 'single']) {
      if (layout === 'single') fireEvent.click(screen.getByLabelText(ROLE_A_LABEL))
      expect(emailBox().checked).toBe(false)
      expect(follows(screen.getByTestId('retention-summary'), emailBox())).toBe(true)
      expect(follows(attestationBox(), emailBox())).toBe(true)
      expect(follows(emailBox(), button('Start run'))).toBe(true)
    }
  })

  it('is absent when the server cannot send mail', async () => {
    await renderPage(QUEUE)
    expect(screen.queryByRole('checkbox', { name: EMAIL_LABEL })).toBeNull()
    cleanup()
    await renderPage(IN_PROCESS)
    expect(screen.queryByRole('checkbox', { name: EMAIL_LABEL })).toBeNull()
  })

  it('creates the batch with the box as ticked', async () => {
    uploadsSucceed()
    await renderPage({ ...QUEUE, completion_email_available: true })
    fireEvent.click(emailBox())
    await submitBatch([docx('a.docx'), docx('b.docx')])
    expect(createBatch).toHaveBeenCalledWith(2, { notifyOnComplete: true })
    expect(vi.mocked(uploadFile).mock.calls.map(([, opts]) => opts.batchId)).toEqual(['BQXZKD', 'BQXZKD'])
  })

  it('creates the batch without the email when the box is left unticked', async () => {
    uploadsSucceed()
    await renderPage({ ...QUEUE, completion_email_available: true })
    await submitBatch([docx('a.docx'), docx('b.docx')])
    expect(createBatch).toHaveBeenCalledWith(2, { notifyOnComplete: false })
  })

  it('puts a ticked single upload in a one-file batch that asks for the email', async () => {
    uploadsSucceed()
    await renderPage({ ...IN_PROCESS, completion_email_available: true })
    await addFiles([docx('mine.docx')])
    fireEvent.click(emailBox())
    tickAttestation()
    fireEvent.click(button('Start run'))
    await flush()
    expect(createBatch).toHaveBeenCalledWith(1, { notifyOnComplete: true })
    expect(vi.mocked(uploadFile).mock.calls[0][1].batchId).toBe('BQXZKD')
    expect(onUploadSuccess).toHaveBeenCalledWith('R1')
  })
})
