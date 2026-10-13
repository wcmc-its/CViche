import { describe, expect, it } from 'vitest'
import type { RunSummary } from '../../types'
import { correctedCopiesPath, correctedLabel, correctedTitle, feedbackRank, feedbackState } from './runFeedback'

const ME = 1
const OTHER = 2

function run(overrides: Partial<RunSummary> = {}, count = 0, runBy = ME): RunSummary {
  return {
    run_id: 'R1', filename: 'cv.docx', status: 'complete', started_at: '2026-09-01T00:00:00Z',
    completed_at: null, total_cost: null, total_duration_seconds: null,
    cv_owner_name: 'Jane Testperson', submission_type: 'authorized_admin',
    run_by: { id: runBy, display_name: 'X', cwid: 'abc1234', email: 'x@example.com', department: null },
    feedback: { count, given_by_me: false, last_at: null },
    ...overrides,
  } as RunSummary
}

describe('feedbackState', () => {
  it('is given whenever any feedback exists, whoever ran it', () => {
    expect(feedbackState(run({}, 1, OTHER), ME, true)).toBe('given')
  })
  it('asks for feedback only on a run the viewer can review', () => {
    expect(feedbackState(run(), ME, true)).toBe('needed')
    expect(feedbackState(run({}, 0, OTHER), ME, false)).toBe('needed') // non-admin: every listed run is theirs
  })
  it("shows an admin someone else's unreviewed run as awaiting, not a call-to-action", () => {
    expect(feedbackState(run({}, 0, OTHER), ME, true)).toBe('awaiting')
  })
  it('rolls up feedback from an earlier rerun for any viewer', () => {
    expect(feedbackState(run(), ME, true, true)).toBe('earlier')
    expect(feedbackState(run({}, 0, OTHER), ME, true, true)).toBe('earlier')
  })
  it('is none for a run that has not finished', () => {
    expect(feedbackState(run({ status: 'running' }), ME, true)).toBe('none')
  })
  it('sorts none < awaiting < needed < earlier < given', () => {
    const order = (['given', 'none', 'earlier', 'awaiting', 'needed'] as const).slice().sort((a, b) => feedbackRank(a) - feedbackRank(b))
    expect(order).toEqual(['none', 'awaiting', 'needed', 'earlier', 'given'])
  })
})

describe('the Corrected line', () => {
  const copy = (uploader: string | null, role: string | null) => ({ uploader, role, uploaded_at: '2026-10-13T14:00:00Z' })

  it('reads nothing for no copy, Corrected for one, Corrected ×N for more', () => {
    expect(correctedLabel(undefined)).toBe('')
    expect(correctedLabel([])).toBe('')
    expect(correctedLabel([copy('Pat Example', 'owner')])).toBe('Corrected')
    expect(correctedLabel([copy('Pat Example', 'owner'), copy('Sam Staff', 'staff')])).toBe('Corrected ×2')
  })

  it('titles each copy with its uploader, role and date, and names an unknown uploader', () => {
    const title = correctedTitle([copy('Sam Staff', 'staff'), copy(null, null)]).split('\n')
    expect(title).toHaveLength(2)
    expect(title[0]).toMatch(/^Sam Staff \(staff\) · .*Oct 13/)
    expect(title[1]).toMatch(/^unknown uploader · /)
  })

  it('links to the run page at its corrected copies', () => {
    expect(correctedCopiesPath('RUN 1')).toBe('/run/RUN%201#corrected-copies')
  })
})
