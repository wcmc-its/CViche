import { describe, expect, it } from 'vitest'
import { adminRoutes, batchRoutes, feedbackRoutes, runRoutes, wsRoutes } from './routes'

describe('batchRoutes', () => {
  it('encodes the batch id taken from the URL into one path segment', () => {
    expect(batchRoutes.detail('BQXZKD')).toBe('/api/batches/BQXZKD')
    expect(batchRoutes.detail('../runs')).toBe('/api/batches/..%2Fruns')
  })
})

describe('path parameters are encoded (#298)', () => {
  it('leaves an ordinary run id unchanged', () => {
    expect(runRoutes.status('A1B2C3')).toBe('/api/run/A1B2C3/status')
    expect(runRoutes.inputFile('A1B2C3')).toBe('/api/run/A1B2C3/input')
    expect(wsRoutes.runStream('A1B2C3')).toBe('/ws/run/A1B2C3/stream')
  })

  it('keeps a run id inside one path segment', () => {
    expect(runRoutes.status('../admin')).toBe('/api/run/..%2Fadmin/status')
    expect(runRoutes.inputFileUrl('a?b')).toBe('/api/run/a%3Fb/input?as_url=true')
    expect(feedbackRoutes.get('a#b')).toBe('/api/run/a%23b/feedback')
    expect(adminRoutes.runScore('a/b')).toBe('/api/admin/run/a%2Fb/score')
    expect(wsRoutes.runStream('a/b')).toBe('/ws/run/a%2Fb/stream')
  })

  it('encodes each segment of a data file name but keeps its sub-directories', () => {
    expect(runRoutes.dataFile('A1B2C3', 'CV #2 final?.docx')).toBe(
      '/api/run/A1B2C3/data/CV%20%232%20final%3F.docx',
    )
    expect(runRoutes.dataJson('A1B2C3', 'steps/3a/output.json')).toBe(
      '/api/run/A1B2C3/json/steps/3a/output.json',
    )
    expect(runRoutes.dataFile('A1B2C3', '100%.json')).toBe('/api/run/A1B2C3/data/100%25.json')
  })

  it('keeps the export type inside one path segment', () => {
    expect(adminRoutes.export('runs')).toBe('/api/admin/export/runs')
    expect(adminRoutes.export('../users')).toBe('/api/admin/export/..%2Fusers')
  })
})
