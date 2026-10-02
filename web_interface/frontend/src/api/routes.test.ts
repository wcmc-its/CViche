import { describe, expect, it } from 'vitest'
import { batchRoutes } from './routes'

describe('batchRoutes', () => {
  it('encodes the batch id taken from the URL into one path segment', () => {
    expect(batchRoutes.detail('BQXZKD')).toBe('/api/batches/BQXZKD')
    expect(batchRoutes.detail('../runs')).toBe('/api/batches/..%2Fruns')
  })
})
