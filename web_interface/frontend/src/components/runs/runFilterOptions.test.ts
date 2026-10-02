import { describe, expect, it } from 'vitest'
import type { RunFilterOptions } from '../../types'
import { buildRunByModel } from './runFilterOptions'

const OPTIONS: RunFilterOptions = {
  departments: [], faculty: [], run_by: [], self_count: 3, on_behalf_count: 5,
  status: { all: 8, running: 0, awaiting_feedback: 0, failed: 0, red: 0 },
  feedback: { given: 0, needed: 0 }, input_format: { wcm: 0, other: 0, unknown: 0 },
}

describe('buildRunByModel pins', () => {
  it('pins Faculty themselves, then On their behalf, each with its count', () => {
    const pinned = buildRunByModel(OPTIONS, undefined, undefined, []).pinned
    expect(pinned.map((p) => [p.id, p.label, p.count])).toEqual([
      ['', 'Anyone', undefined],
      ['self', 'Faculty themselves', 3],
      ['on_behalf', 'On their behalf', 5],
    ])
  })

  it('omits a pin with no runs', () => {
    const pinned = buildRunByModel({ ...OPTIONS, on_behalf_count: 0 }, undefined, undefined, []).pinned
    expect(pinned.map((p) => p.id)).toEqual(['', 'self'])
  })
})
