import { afterEach, describe, expect, it, vi } from 'vitest'
import { estimateInboxItems } from './inbox'
import { api } from './client'

vi.mock('./client', () => ({ api: { post: vi.fn() } }))

afterEach(() => { vi.resetAllMocks() })

describe('estimateInboxItems (#1626)', () => {
  it('sends every held item id in one request, in order', async () => {
    await estimateInboxItems([7, 3])
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.post).mock.calls[0]).toEqual(['/api/inbox/estimate', { item_ids: [7, 3] }])
  })
})
