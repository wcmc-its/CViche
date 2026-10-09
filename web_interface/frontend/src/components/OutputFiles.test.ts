import { describe, expect, it } from 'vitest'
import { isFinalDocx, visibleOutputFiles } from './OutputFiles'

const STEP = {
  output_files: JSON.stringify([
    '/o/stage_6_wcm_documents/ABC_wcm.docx',
    '/o/stage_7_doctor/ABC_doctor.json',
    '/o/stage_6_wcm_documents/ABC_wcm_review.docx',
  ]),
}

describe('visibleOutputFiles', () => {
  it('hides stage JSON from a run owner, but lists the review copy (#1591)', () => {
    expect(visibleOutputFiles(STEP, false)).toEqual([
      '/o/stage_6_wcm_documents/ABC_wcm.docx',
      '/o/stage_6_wcm_documents/ABC_wcm_review.docx',
    ])
  })

  it('lists both for admins and staff', () => {
    expect(visibleOutputFiles(STEP, true)).toHaveLength(3)
  })
})

describe('isFinalDocx', () => {
  it('is the finished document, never its review copy, whatever the order', () => {
    const files = ['/o/ABC_wcm_review.docx', '/o/ABC_wcm.docx']
    expect(files.find(isFinalDocx)).toBe('/o/ABC_wcm.docx')
  })
})
