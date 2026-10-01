import { X } from 'lucide-react'
import type { RunByOption, RunFilterOptions, RunSummary } from '../../types'
import RunFilterCombo from './RunFilterCombo'
import {
  buildDepartmentModel,
  buildFeedbackModel,
  buildFacultyModel,
  buildRunByModel,
  recentRunByIds,
} from './runFilterOptions'
import { FEEDBACK_VALUE_LABEL } from './runFeedback'
import { RUN_BY_SELF, hasActiveFilters, isFeedbackFilter } from './runFilters'
import type { RunFilterControls, RunFilters } from './runFilters'
import { SELF_RUN_BY_LABEL } from './runGroups'

interface RunFilterBarProps {
  controls: RunFilterControls
  options: RunFilterOptions | null
  /** Loaded runs; the source of the "recent" Run by entries and of names missing from options. */
  runs: RunSummary[]
  currentUserId: number | undefined
  currentUserEmail: string | undefined
}

const EMPTY_OPTIONS: RunFilterOptions = { departments: [], faculty: [], run_by: [], self_count: 0,
  feedback: { given: 0, needed: 0 } }

/** Display text for the selected Run by value. */
export function runByValueLabel(
  runBy: string,
  people: RunByOption[],
  runs: RunSummary[],
  currentUserId: number | undefined,
): string {
  if (runBy === RUN_BY_SELF) return SELF_RUN_BY_LABEL
  const id = Number(runBy)
  const name =
    people.find((p) => p.id === id)?.display_name ?? runs.find((r) => r.run_by?.id === id)?.run_by?.display_name
  if (!name) return `User ${runBy}`
  return id === currentUserId ? `${name} (you)` : name
}

/** Display text for the selected Feedback value; '' when unset. */
function feedbackValueLabel(feedback: string): string {
  return isFeedbackFilter(feedback) ? FEEDBACK_VALUE_LABEL[feedback] : ''
}

function activeChips(filters: RunFilters, runByLabel: string) {
  return [
    { key: 'department' as const, label: 'Department', value: filters.department },
    { key: 'faculty' as const, label: 'Faculty', value: filters.faculty },
    { key: 'runBy' as const, label: 'Run by', value: filters.runBy && runByLabel },
    { key: 'feedback' as const, label: 'Feedback', value: feedbackValueLabel(filters.feedback) },
  ].filter((chip) => chip.value)
}

/** The four admin filter comboboxes, shown right-aligned in the filter row. */
export function RunFilterCombos({ controls, options, runs, currentUserId, currentUserEmail }: RunFilterBarProps) {
  const { filters, setFilter } = controls
  const data = options ?? EMPTY_OPTIONS
  const runByLabel = filters.runBy ? runByValueLabel(filters.runBy, data.run_by, runs, currentUserId) : 'Anyone'
  return (
    <div className="flex flex-wrap gap-2 sm:ml-auto">
      <RunFilterCombo
        label="Department"
        valueLabel={filters.department || 'All'}
        activeId={filters.department}
        placeholder="Search departments"
        model={buildDepartmentModel(data)}
        onPick={(id) => setFilter('department', id)}
      />
      <RunFilterCombo
        label="Faculty"
        valueLabel={filters.faculty || 'All'}
        activeId={filters.faculty}
        placeholder="Name, CWID or email"
        model={buildFacultyModel(data)}
        onPick={(id) => setFilter('faculty', id)}
      />
      <RunFilterCombo
        label="Run by"
        valueLabel={runByLabel}
        activeId={filters.runBy}
        placeholder="Name, CWID or email"
        model={buildRunByModel(data, currentUserId, currentUserEmail, recentRunByIds(runs, currentUserId))}
        onPick={(id) => setFilter('runBy', id)}
      />
      <RunFilterCombo
        label="Feedback"
        valueLabel={feedbackValueLabel(filters.feedback) || 'Any'}
        activeId={filters.feedback}
        placeholder="Feedback status"
        model={buildFeedbackModel(data)}
        searchable={false}
        onPick={(id) => setFilter('feedback', id)}
      />
    </div>
  )
}

/** Dark chips for the active filters, each removable, plus "Clear all". */
export function ActiveFilterChips({ controls, options, runs, currentUserId }: Omit<RunFilterBarProps, 'currentUserEmail'>) {
  const { filters, setFilter, clearAll } = controls
  if (!hasActiveFilters(filters)) return null
  const runByLabel = filters.runBy ? runByValueLabel(filters.runBy, options?.run_by ?? [], runs, currentUserId) : ''
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {activeChips(filters, runByLabel).map((chip) => (
        <span key={chip.key} className="flex items-center gap-1.5 rounded-full bg-ink py-1 pl-2.5 pr-1.5 text-[13px] text-white">
          <span className="opacity-70">{chip.label}:</span>
          <span className="font-medium">{chip.value}</span>
          <button
            type="button"
            onClick={() => setFilter(chip.key, '')}
            aria-label={`Remove ${chip.label} filter`}
            title="Remove filter"
            className="flex h-[18px] w-[18px] items-center justify-center rounded-full hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
          >
            <X className="h-[11px] w-[11px]" aria-hidden="true" />
          </button>
        </span>
      ))}
      <button type="button" onClick={clearAll} className="text-[13px] text-primary-700 hover:underline">
        Clear all
      </button>
    </div>
  )
}
