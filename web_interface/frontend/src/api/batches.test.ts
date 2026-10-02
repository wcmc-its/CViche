import { afterEach, describe, expect, it, vi } from 'vitest'
import { createBatch } from './batches'
import { api } from './client'

vi.mock('./client', () => ({ api: { post: vi.fn() } }))

const sentBody = () => vi.mocked(api.post).mock.calls[0][1]

afterEach(() => { vi.resetAllMocks() })

describe('createBatch', () => {
  it('asks for the completion email when "Email me when job completes" is ticked (#1335)', async () => {
    await createBatch(3, { notifyOnComplete: true })
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe('/api/batches')
    expect(sentBody()).toEqual({ files_submitted: 3, notify_on_complete: true })
  })

  it('sends notify_on_complete false by default', async () => {
    await createBatch(2)
    expect(sentBody()).toEqual({ files_submitted: 2, notify_on_complete: false })
  })
})
