import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { RunFeedbackFilter, RunInputFormatFilter, RunListParams } from '../../types'

/** Literal run_by value for runs the faculty member uploaded themselves. */
export const RUN_BY_SELF = 'self'

/** The feedback filter values the API accepts. */
export const FEEDBACK_FILTER_VALUES: readonly RunFeedbackFilter[] = ['given', 'needed']

export function isFeedbackFilter(value: string): value is RunFeedbackFilter {
  return (FEEDBACK_FILTER_VALUES as readonly string[]).includes(value)
}

/** The input-format filter values the API accepts, with their display text. */
export const INPUT_FORMAT_VALUE_LABEL: Record<RunInputFormatFilter, string> = {
  wcm: 'WCM template',
  other: 'Other format',
  unknown: 'Not classified',
}

export function isInputFormatFilter(value: string): value is RunInputFormatFilter {
  return Object.keys(INPUT_FORMAT_VALUE_LABEL).includes(value)
}

/** Active admin filters; '' means unset. runBy is a user id (as text) or RUN_BY_SELF;
 *  feedback is '' or a RunFeedbackFilter; inputFormat is '' or a RunInputFormatFilter. */
export interface RunFilters {
  department: string
  faculty: string
  runBy: string
  feedback: string
  inputFormat: string
}

export type RunFilterKey = keyof RunFilters

export const EMPTY_FILTERS: RunFilters = { department: '', faculty: '', runBy: '', feedback: '', inputFormat: '' }

/** URL search param behind each filter; the URL is the single source of filter state. */
const FILTER_PARAM: Record<RunFilterKey, string> = {
  department: 'department',
  faculty: 'faculty',
  runBy: 'run_by',
  feedback: 'feedback',
  inputFormat: 'input_format',
}

const FILTER_KEYS = Object.keys(FILTER_PARAM) as RunFilterKey[]

/** URL search param for the Batch filter. Unlike the admin filters it applies to
 *  everyone: a batch is visible to its submitter as well as to admins. */
export const BATCH_PARAM = 'batch'

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
  if (isInputFormatFilter(filters.inputFormat)) params.input_format = filters.inputFormat
  return params
}

export interface RunFilterControls {
  filters: RunFilters
  setFilter: (key: RunFilterKey, value: string) => void
  clearAll: () => void
}

/** Filter state held in the URL (?department=&faculty=&run_by=&feedback=&input_format=) so it survives reload and back. */
export function useRunFilters(enabled: boolean): RunFilterControls {
  const [searchParams, setSearchParams] = useSearchParams()
  const department = searchParams.get(FILTER_PARAM.department) ?? ''
  const faculty = searchParams.get(FILTER_PARAM.faculty) ?? ''
  const runBy = searchParams.get(FILTER_PARAM.runBy) ?? ''
  const rawFeedback = searchParams.get(FILTER_PARAM.feedback) ?? ''
  const feedback = isFeedbackFilter(rawFeedback) ? rawFeedback : ''
  const rawInputFormat = searchParams.get(FILTER_PARAM.inputFormat) ?? ''
  const inputFormat = isInputFormatFilter(rawInputFormat) ? rawInputFormat : ''

  const filters = useMemo<RunFilters>(
    () => (enabled ? { department, faculty, runBy, feedback, inputFormat } : EMPTY_FILTERS),
    [enabled, department, faculty, runBy, feedback, inputFormat],
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
      next.delete(BATCH_PARAM)
      return next
    })
  }, [setSearchParams])

  return { filters, setFilter, clearAll }
}

/** The selected batch id ('' when unset) held in ?batch=, and its setter ('' clears it). */
export function useBatchFilter(): [string, (batchId: string) => void] {
  const [searchParams, setSearchParams] = useSearchParams()
  const batchId = searchParams.get(BATCH_PARAM) ?? ''
  const setBatch = useCallback(
    (value: string) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(BATCH_PARAM, value)
        else next.delete(BATCH_PARAM)
        return next
      })
    },
    [setSearchParams],
  )
  return [batchId, setBatch]
}
