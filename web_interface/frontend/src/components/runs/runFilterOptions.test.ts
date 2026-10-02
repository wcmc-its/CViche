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

  it('always pins Me for a signed-in admin, even with no runs of their own', () => {
    const pinned = buildRunByModel(OPTIONS, 1, 'admin@example.org', []).pinned
    expect(pinned.map((p) => [p.id, p.label, p.count])).toEqual([
      ['', 'Anyone', undefined],
      ['1', 'Me', 0],
      ['self', 'Faculty themselves', 3],
      ['on_behalf', 'On their behalf', 5],
    ])
  })

  it('lists recent people in their own group, not as pins', () => {
    const person = { id: 9, display_name: 'Rae Recent', cwid: null, email: null, department: null, count: 2 }
    const model = buildRunByModel({ ...OPTIONS, run_by: [person] }, 1, undefined, [9])
    expect(model.recent!.map((r) => [r.id, r.label, r.meta])).toEqual([['9', 'Rae Recent', undefined]])
    expect(model.pinned.map((p) => p.id)).not.toContain('9')
    expect(model.items).toEqual([])
  })

  it('omits a pin with no runs', () => {
    const pinned = buildRunByModel({ ...OPTIONS, on_behalf_count: 0 }, undefined, undefined, []).pinned
    expect(pinned.map((p) => p.id)).toEqual(['', 'self'])
  })
})
