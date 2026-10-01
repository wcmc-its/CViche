import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { RunFeedbackFilter, RunListParams } from '../../types'

/** Literal run_by value for runs the faculty member uploaded themselves. */
export const RUN_BY_SELF = 'self'

/** The feedback filter values the API accepts. */
export const FEEDBACK_FILTER_VALUES: readonly RunFeedbackFilter[] = ['given', 'needed']

export function isFeedbackFilter(value: string): value is RunFeedbackFilter {
  return (FEEDBACK_FILTER_VALUES as readonly string[]).includes(value)
}

/** Active admin filters; '' means unset. runBy is a user id (as text) or RUN_BY_SELF;
 *  feedback is '' or a RunFeedbackFilter. */
export interface RunFilters {
  department: string
  faculty: string
  runBy: string
  feedback: string
}

export type RunFilterKey = keyof RunFilters

export const EMPTY_FILTERS: RunFilters = { department: '', faculty: '', runBy: '', feedback: '' }

/** URL search param behind each filter; the URL is the single source of filter state. */
const FILTER_PARAM: Record<RunFilterKey, string> = {
  department: 'department',
  faculty: 'faculty',
  runBy: 'run_by',
  feedback: 'feedback',
}

const FILTER_KEYS = Object.keys(FILTER_PARAM) as RunFilterKey[]

export function hasActiveFilters(filters: RunFilters): boolean {
  return FILTER_KEYS.some((key) => filters[key] !== '')
}

/** Filters to send to the API; run_by must be 'self' or a numeric id, anything else is dropped. */
export function toListParams(filters: RunFilters): RunListParams {
  const params: RunListParams = { scope: 'all' }
  if (filters.department) params.department = filters.department
  if (filters.faculty) params.faculty = filters.faculty
  if (filters.runBy === RUN_BY_SELF) params.run_by = RUN_BY_SELF
  else if (/^\d+$/.test(filters.runBy)) params.run_by = Number(filters.runBy)
  if (isFeedbackFilter(filters.feedback)) params.feedback = filters.feedback
  return params
}

export interface RunFilterControls {
  filters: RunFilters
  setFilter: (key: RunFilterKey, value: string) => void
  clearAll: () => void
}

/** Filter state held in the URL (?department=&faculty=&run_by=&feedback=) so it survives reload and back. */
export function useRunFilters(enabled: boolean): RunFilterControls {
  const [searchParams, setSearchParams] = useSearchParams()
  const department = searchParams.get(FILTER_PARAM.department) ?? ''
  const faculty = searchParams.get(FILTER_PARAM.faculty) ?? ''
  const runBy = searchParams.get(FILTER_PARAM.runBy) ?? ''
  const rawFeedback = searchParams.get(FILTER_PARAM.feedback) ?? ''
  const feedback = isFeedbackFilter(rawFeedback) ? rawFeedback : ''

  const filters = useMemo<RunFilters>(
    () => (enabled ? { department, faculty, runBy, feedback } : EMPTY_FILTERS),
    [enabled, department, faculty, runBy, feedback],
  )

  const setFilter = useCallback(
    (key: RunFilterKey, value: string) => {
      if (!enabled) return
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(FILTER_PARAM[key], value)
        else next.delete(FILTER_PARAM[key])
        return next
      })
    },
    [enabled, setSearchParams],
  )

  const clearAll = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      FILTER_KEYS.forEach((key) => next.delete(FILTER_PARAM[key]))
      return next
    })
  }, [setSearchParams])

  return { filters, setFilter, clearAll }
}
