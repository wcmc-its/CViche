import { afterEach, describe, expect, it, vi } from 'vitest'
import { getInputFileUrl } from './runs'
import { api } from './client'

vi.mock('./client', () => ({ api: { get: vi.fn() } }))

afterEach(() => { vi.resetAllMocks() })

describe('getInputFileUrl', () => {
  it('asks for the URL instead of the redirect and returns it (#1333)', async () => {
    vi.mocked(api.get).mockResolvedValue({ url: 'https://s3.example/x?sig=abc' })
    await expect(getInputFileUrl('ABCDEF')).resolves.toBe('https://s3.example/x?sig=abc')
    expect(vi.mocked(api.get).mock.calls[0][0]).toBe('/api/run/ABCDEF/input?as_url=true')
  })

  it('returns null when storage has no URL', async () => {
    vi.mocked(api.get).mockResolvedValue({ url: null })
    await expect(getInputFileUrl('ABCDEF')).resolves.toBeNull()
  })
})
