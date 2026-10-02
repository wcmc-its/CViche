import { describe, expect, it } from 'vitest'
import { stepRowClass } from './StepSidebar'

describe('stepRowClass', () => {
  it('tints the running step and rings the selected one, so they read differently', () => {
    expect(stepRowClass(false, true)).toContain('bg-primary-50')
    expect(stepRowClass(true, false)).toContain('border-ink')
    expect(stepRowClass(true, false)).not.toContain('bg-primary-50')
    expect(stepRowClass(true, true)).toContain('bg-primary-50')
    expect(stepRowClass(true, true)).toContain('border-ink')
  })
})
