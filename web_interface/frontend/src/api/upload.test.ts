import { afterEach, describe, expect, it, vi } from 'vitest'
import { getBatchEstimate, uploadFile } from './upload'
import { api } from './client'

vi.mock('./client', () => ({ api: { post: vi.fn() } }))

const sentForm = () => vi.mocked(api.post).mock.calls[0][1] as FormData

afterEach(() => { vi.resetAllMocks() })

describe('uploadFile', () => {
  it('always sends include_track_changes=true, and the batch id when given', async () => {
    await uploadFile(new File(['cv'], 'a.docx'), { stripWcmInstructions: false, submissionType: 'authorized_admin', batchId: 'BQXZKD' })
    const form = sentForm()
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe('/api/upload')
    expect(form.get('include_track_changes')).toBe('true')
    expect(form.get('batch_id')).toBe('BQXZKD')
    expect(form.get('submission_type')).toBe('authorized_admin')
    expect(form.get('strip_wcm_instructions')).toBe('false')
  })

  it('sends no batch id for a single upload', async () => {
    await uploadFile(new File(['cv'], 'a.docx'), { stripWcmInstructions: true, submissionType: 'own_cv' })
    expect(sentForm().has('batch_id')).toBe(false)
    expect(sentForm().get('include_track_changes')).toBe('true')
  })
})

describe('getBatchEstimate', () => {
  it('sends every file as `files` in one request', async () => {
    await getBatchEstimate([new File(['a'], 'a.docx'), new File(['b'], 'b.docx')])
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe('/api/estimate')
    expect(sentForm().getAll('files').map((f) => (f as File).name)).toEqual(['a.docx', 'b.docx'])
    expect(sentForm().has('file')).toBe(false)
  })
})
