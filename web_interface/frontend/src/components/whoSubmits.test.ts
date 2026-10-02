import { describe, expect, it } from 'vitest'
import { UNKNOWN_DEPARTMENT, departmentBar, percent } from './whoSubmits'

describe('percent', () => {
  it('rounds to a whole percent and is 0 for no runs', () => {
    expect(percent(1, 3)).toBe(33)
    expect(percent(2, 3)).toBe(67)
    expect(percent(0, 0)).toBe(0)
  })
})

describe('departmentBar', () => {
  it('shares own-CV runs over all of the department\'s runs', () => {
    const bar = departmentBar({ department: 'Test Medicine', own_cv: 3, on_behalf: 1 })
    expect(bar).toMatchObject({ name: 'Test Medicine', own: 3, total: 4, pct: 75 })
  })

  it('links to Runs filtered to the department and on-behalf runs, with the URL param names Runs reads', () => {
    const { href } = departmentBar({ department: 'Library & Archives', own_cv: 0, on_behalf: 2 })
    const url = new URL(href as string, 'https://example.org')
    expect(url.pathname).toBe('/runs')
    expect(url.searchParams.get('department')).toBe('Library & Archives')
    expect(url.searchParams.get('run_by')).toBe('on_behalf')
    expect([...url.searchParams.keys()].sort()).toEqual(['department', 'run_by'])
  })

  it('names a department-less submitter Unknown and has no link', () => {
    const bar = departmentBar({ department: null, own_cv: 1, on_behalf: 1 })
    expect(bar.name).toBe(UNKNOWN_DEPARTMENT)
    expect(bar.href).toBeNull()
  })
})
