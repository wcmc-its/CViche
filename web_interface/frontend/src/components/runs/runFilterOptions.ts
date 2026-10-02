import type { RunFilterOptions, RunSummary } from '../../types'
import { formatRelativeDate } from '../../utils'
import { INPUT_FORMAT_VALUE_LABEL, RUN_BY_ON_BEHALF, RUN_BY_SELF } from './runFilters'
import { FEEDBACK_VALUE_LABEL } from './runFeedback'
import { ON_BEHALF_RUN_BY_LABEL, SELF_RUN_BY_LABEL } from './runGroups'

/** One selectable entry in a filter popover. id '' is the "any" entry. */
export interface ComboOption {
  id: string
  label: string
  meta?: string
  count?: number
  /** Lower-case text the search box matches against. */
  search: string
}

export interface ComboModel {
  /** Always-on entries shown above the list while the search box is empty. */
  pinned: ComboOption[]
  items: ComboOption[]
}

const EMAIL_DOMAIN = '@med.cornell.edu'
const RECENT_RUN_BY_LIMIT = 3

export function matchesQuery(option: ComboOption, query: string): boolean {
  return !query || option.search.includes(query)
}

function joinMeta(...parts: (string | null | undefined)[]): string {
  return parts.filter(Boolean).join(' · ')
}

export function buildDepartmentModel(options: RunFilterOptions): ComboModel {
  return {
    pinned: [{ id: '', label: 'All departments', search: '' }],
    items: options.departments.map((d) => ({
      id: d.value,
      label: d.value,
      count: d.count,
      search: d.value.toLowerCase(),
    })),
  }
}

/** Faculty options carry only a name from the API, so name is all the search can match.
 *  Kept in the API's order: most recently run first. */
export function buildFacultyModel(options: RunFilterOptions): ComboModel {
  return {
    pinned: [{ id: '', label: 'All faculty', search: '' }],
    items: options.faculty.map((f) => ({
      id: f.value,
      label: f.value,
      meta: f.last_run_at ? `Last run ${formatRelativeDate(f.last_run_at).display}` : undefined,
      count: f.count,
      search: f.value.toLowerCase(),
    })),
  }
}

/** Any / Feedback given / Needs feedback, with counts from filter-options; all pinned, no search. */
export function buildFeedbackModel(options: RunFilterOptions): ComboModel {
  return {
    pinned: [
      { id: '', label: 'Any', search: '' },
      { id: 'given', label: FEEDBACK_VALUE_LABEL.given, count: options.feedback.given, search: '' },
      { id: 'needed', label: FEEDBACK_VALUE_LABEL.needed, count: options.feedback.needed, search: '' },
    ],
    items: [],
  }
}

/** Any / WCM template / Other format / Not classified, with counts from filter-options; all pinned, no search. */
export function buildInputFormatModel(options: RunFilterOptions): ComboModel {
  const { wcm, other, unknown } = options.input_format
  return {
    pinned: [
      { id: '', label: 'Any', search: '' },
      { id: 'wcm', label: INPUT_FORMAT_VALUE_LABEL.wcm, count: wcm, search: '' },
      { id: 'other', label: INPUT_FORMAT_VALUE_LABEL.other, count: other, search: '' },
      { id: 'unknown', label: INPUT_FORMAT_VALUE_LABEL.unknown, count: unknown, search: '' },
    ],
    items: [],
  }
}

/** Distinct run-by user ids from the loaded runs, newest first, excluding the current user. */
export function recentRunByIds(runs: RunSummary[], currentUserId: number | undefined): number[] {
  const ids: number[] = []
  for (const run of runs) {
    const id = run.run_by?.id
    if (id !== undefined && id !== currentUserId && !ids.includes(id)) ids.push(id)
    if (ids.length === RECENT_RUN_BY_LIMIT) break
  }
  return ids
}

export function buildRunByModel(
  options: RunFilterOptions,
  currentUserId: number | undefined,
  currentUserEmail: string | undefined,
  recentIds: number[],
): ComboModel {
  const byId = new Map(options.run_by.map((p) => [p.id, p]))
  const pinned: ComboOption[] = [{ id: '', label: 'Anyone', search: '' }]
  const me = currentUserId === undefined ? undefined : byId.get(currentUserId)
  if (me) {
    pinned.push({ id: String(me.id), label: 'Me', meta: currentUserEmail, count: me.count, search: '' })
  }
  if (options.self_count > 0) {
    pinned.push({
      id: RUN_BY_SELF,
      label: SELF_RUN_BY_LABEL,
      meta: 'Uploaded their own CV',
      count: options.self_count,
      search: '',
    })
  }
  if (options.on_behalf_count > 0) {
    pinned.push({
      id: RUN_BY_ON_BEHALF,
      label: ON_BEHALF_RUN_BY_LABEL,
      meta: 'Submitted by someone else',
      count: options.on_behalf_count,
      search: '',
    })
  }
  for (const id of recentIds) {
    const person = byId.get(id)
    if (person) {
      pinned.push({ id: String(id), label: person.display_name, meta: 'Recent', count: person.count, search: '' })
    }
  }
  const pinnedIds = new Set(pinned.map((p) => p.id))
  const items = options.run_by
    .filter((p) => !pinnedIds.has(String(p.id)))
    .map((p) => ({
      id: String(p.id),
      label: p.display_name,
      meta: joinMeta(p.cwid, p.department),
      count: p.count,
      search: [p.display_name, p.cwid, p.email, p.cwid ? `${p.cwid}${EMAIL_DOMAIN}` : null]
        .filter(Boolean)
        .join(' ')
        .toLowerCase(),
    }))
  return { pinned, items }
}
