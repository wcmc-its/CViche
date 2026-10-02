import type { DepartmentSubmissions, SubmissionSplit } from '../types'
import { RUN_BY_ON_BEHALF, runsUrlWith } from './runs/runFilters'

/** Label for submitters whose ED record carries no department. */
export const UNKNOWN_DEPARTMENT = 'Unknown'

export interface DepartmentBar {
  name: string
  own: number
  total: number
  /** Whole percent of this department's runs that faculty submitted themselves. */
  pct: number
  /** Runs page filtered to this department's on-behalf runs; null for Unknown, which has no department filter value. */
  href: string | null
}

/** Whole percent of ``part`` in ``whole``; 0 when there are no runs. */
export function percent(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0
}

export function departmentBar(d: DepartmentSubmissions): DepartmentBar {
  const total = d.own_cv + d.on_behalf
  return {
    name: d.department ?? UNKNOWN_DEPARTMENT,
    own: d.own_cv,
    total,
    pct: percent(d.own_cv, total),
    href: d.department ? runsUrlWith({ department: d.department, runBy: RUN_BY_ON_BEHALF }) : null,
  }
}

export function departmentBars(split: SubmissionSplit): DepartmentBar[] {
  return split.departments.map(departmentBar)
}
