import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { RunFeedbackFilter, RunInputFormatFilter, RunListParams, RunStatusFilter } from '../../types'

/** Literal run_by value for runs the faculty member uploaded themselves. */
export const RUN_BY_SELF = 'self'

/** Literal run_by value for runs an authorized admin submitted for the faculty member. */
export const RUN_BY_ON_BEHALF = 'on_behalf'

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

/** The status filter values the API accepts; 'red' is admin only. */
export const STATUS_FILTER_VALUES: readonly RunStatusFilter[] = ['running', 'failed', 'red']

export function isStatusFilter(value: string): value is RunStatusFilter {
  return (STATUS_FILTER_VALUES as readonly string[]).includes(value)
}

/** Active filters; '' means unset. runBy is a user id (as text), RUN_BY_SELF or RUN_BY_ON_BEHALF;
 *  feedback is '' or a RunFeedbackFilter; inputFormat is '' or a RunInputFormatFilter;
 *  status is '' or a RunStatusFilter. Only feedback and status apply to members. */
export interface RunFilters {
  department: string
  faculty: string
  runBy: string
  feedback: string
  inputFormat: string
  status: string
}

export type RunFilterKey = keyof RunFilters

export const EMPTY_FILTERS: RunFilters = { department: '', faculty: '', runBy: '', feedback: '', inputFormat: '', status: '' }

/** URL search param behind each filter; the URL is the single source of filter state. */
const FILTER_PARAM: Record<RunFilterKey, string> = {
  department: 'department',
  faculty: 'faculty',
  runBy: 'run_by',
  feedback: 'feedback',
  inputFormat: 'input_format',
  status: 'status',
}

const FILTER_KEYS = Object.keys(FILTER_PARAM) as RunFilterKey[]

/** The filters members have too (scope=mine); the rest are admin-only. */
const MEMBER_FILTER_KEYS: readonly RunFilterKey[] = ['feedback', 'status']

/** URL search param for the Batch filter. Unlike the admin filters it applies to
 *  everyone: a batch is visible to its submitter as well as to admins. */
export const BATCH_PARAM = 'batch'

export function hasActiveFilters(filters: RunFilters): boolean {
  return FILTER_KEYS.some((key) => filters[key] !== '')
}

/** Active filters behind the narrow-screen "Filters" button: the five admin combos plus the Batch filter.
 *  The status pills are always on screen and are not counted. */
export function countPanelFilters(filters: RunFilters, batchId: string): number {
  const combos = [filters.department, filters.faculty, filters.runBy, filters.feedback, filters.inputFormat, batchId]
  return combos.filter((value) => value !== '').length
}

/** Filters to send to the API; run_by must be 'self' or a numeric id, anything else is dropped. */
export function toListParams(filters: RunFilters): RunListParams {
  const params: RunListParams = { scope: 'all' }
  if (filters.department) params.department = filters.department
  if (filters.faculty) params.faculty = filters.faculty
  if (filters.runBy === RUN_BY_SELF) params.run_by = RUN_BY_SELF
  else if (filters.runBy === RUN_BY_ON_BEHALF) params.run_by = RUN_BY_ON_BEHALF
  else if (/^\d+$/.test(filters.runBy)) params.run_by = Number(filters.runBy)
  if (isFeedbackFilter(filters.feedback)) params.feedback = filters.feedback
  if (isInputFormatFilter(filters.inputFormat)) params.input_format = filters.inputFormat
  if (isStatusFilter(filters.status)) params.status = filters.status
  return params
}

/** Filters to send for a member's own runs (scope 'mine'): only feedback and the non-admin statuses. */
export function toMemberListParams(filters: RunFilters): RunListParams {
  const params: RunListParams = {}
  if (isFeedbackFilter(filters.feedback)) params.feedback = filters.feedback
  if (isStatusFilter(filters.status) && filters.status !== 'red') params.status = filters.status
  return params
}

/** One status pill. 'awaiting_feedback' is not a status: it is the feedback=needed filter. */
export type StatusPillId = 'all' | 'running' | 'awaiting_feedback' | 'failed' | 'red'

export const STATUS_PILLS: readonly { id: StatusPillId; label: string; adminOnly?: true }[] = [
  { id: 'all', label: 'All faculty' },
  { id: 'running', label: 'Running' },
  { id: 'awaiting_feedback', label: 'Awaiting feedback' },
  { id: 'failed', label: 'Had failures' },
  { id: 'red', label: 'Red score', adminOnly: true },
]

/** The lit pill: a status wins, else feedback=needed lights Awaiting feedback, else All. */
export function activeStatusPill(filters: RunFilters): StatusPillId {
  if (isStatusFilter(filters.status)) return filters.status
  return filters.feedback === NEEDED_FEEDBACK ? 'awaiting_feedback' : 'all'
}

const NEEDED_FEEDBACK: RunFeedbackFilter = 'needed'

/** Point the status/feedback params at one pill. The pills are single-select, so
 *  picking one clears the other pills' filters; feedback=given, set from the
 *  Feedback menu, is left alone. */
export function applyStatusPill(params: URLSearchParams, pill: StatusPillId): void {
  const clearNeeded = () => {
    if (params.get(FILTER_PARAM.feedback) === NEEDED_FEEDBACK) params.delete(FILTER_PARAM.feedback)
  }
  params.delete(FILTER_PARAM.status)
  if (pill === 'awaiting_feedback') params.set(FILTER_PARAM.feedback, NEEDED_FEEDBACK)
  else if (pill === 'all') clearNeeded()
  else {
    clearNeeded()
    params.set(FILTER_PARAM.status, pill)
  }
}

export interface RunFilterControls {
  filters: RunFilters
  setFilter: (key: RunFilterKey, value: string) => void
  /** Light one status pill (see applyStatusPill); allowed for members too. */
  setStatusPill: (pill: StatusPillId) => void
  clearAll: () => void
}

/** Filter state held in the URL (?department=&faculty=&run_by=&feedback=&input_format=&status=) so it
 *  survives reload and back. ``isAdmin`` unlocks the admin-only filters; feedback and status are
 *  read for everyone, except status=red, which is admin only. */
export function useRunFilters(isAdmin: boolean): RunFilterControls {
  const [searchParams, setSearchParams] = useSearchParams()
  const department = searchParams.get(FILTER_PARAM.department) ?? ''
  const faculty = searchParams.get(FILTER_PARAM.faculty) ?? ''
  const runBy = searchParams.get(FILTER_PARAM.runBy) ?? ''
  const rawFeedback = searchParams.get(FILTER_PARAM.feedback) ?? ''
  const feedback = isFeedbackFilter(rawFeedback) ? rawFeedback : ''
  const rawInputFormat = searchParams.get(FILTER_PARAM.inputFormat) ?? ''
  const inputFormat = isInputFormatFilter(rawInputFormat) ? rawInputFormat : ''
  const rawStatus = searchParams.get(FILTER_PARAM.status) ?? ''
  const status = isStatusFilter(rawStatus) && (isAdmin || rawStatus !== 'red') ? rawStatus : ''

  const filters = useMemo<RunFilters>(
    () => (isAdmin
      ? { department, faculty, runBy, feedback, inputFormat, status }
      : { ...EMPTY_FILTERS, feedback, status }),
    [isAdmin, department, faculty, runBy, feedback, inputFormat, status],
  )

  const setFilter = useCallback(
    (key: RunFilterKey, value: string) => {
      if (!isAdmin && !MEMBER_FILTER_KEYS.includes(key)) return
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(FILTER_PARAM[key], value)
        else next.delete(FILTER_PARAM[key])
        return next
      })
    },
    [isAdmin, setSearchParams],
  )

  const setStatusPill = useCallback(
    (pill: StatusPillId) => {
      if (pill === 'red' && !isAdmin) return
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        applyStatusPill(next, pill)
        return next
      })
    },
    [isAdmin, setSearchParams],
  )

  const clearAll = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      FILTER_KEYS.forEach((key) => next.delete(FILTER_PARAM[key]))
      next.delete(BATCH_PARAM)
      return next
    })
  }, [setSearchParams])

  return { filters, setFilter, setStatusPill, clearAll }
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
