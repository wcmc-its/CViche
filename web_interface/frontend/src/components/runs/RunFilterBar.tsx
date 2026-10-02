import { X } from 'lucide-react'
import type { RunByOption, RunFilterOptions, RunSummary } from '../../types'
import RunFilterCombo from './RunFilterCombo'
import {
  buildDepartmentModel,
  buildFeedbackModel,
  buildFacultyModel,
  buildInputFormatModel,
  buildRunByModel,
  recentRunByIds,
} from './runFilterOptions'
import { FEEDBACK_VALUE_LABEL } from './runFeedback'
import { INPUT_FORMAT_VALUE_LABEL, RUN_BY_SELF, hasActiveFilters, isFeedbackFilter, isInputFormatFilter } from './runFilters'
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
  feedback: { given: 0, needed: 0 }, input_format: { wcm: 0, other: 0, unknown: 0 } }

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

/** Display text for the selected Input format value; '' when unset. */
function inputFormatValueLabel(inputFormat: string): string {
  return isInputFormatFilter(inputFormat) ? INPUT_FORMAT_VALUE_LABEL[inputFormat] : ''
}

function activeChips(filters: RunFilters, runByLabel: string) {
  return [
    { key: 'department' as const, label: 'Department', value: filters.department },
    { key: 'faculty' as const, label: 'Faculty', value: filters.faculty },
    { key: 'runBy' as const, label: 'Run by', value: filters.runBy && runByLabel },
    { key: 'feedback' as const, label: 'Feedback', value: feedbackValueLabel(filters.feedback) },
    { key: 'inputFormat' as const, label: 'Input format', value: inputFormatValueLabel(filters.inputFormat) },
  ].filter((chip) => chip.value)
}

/** The five admin filter comboboxes, shown right-aligned in the filter row; `extra` (the Batch filter) follows them. */
export function RunFilterCombos({ controls, options, runs, currentUserId, currentUserEmail, extra }: RunFilterBarProps & { extra?: React.ReactNode }) {
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
      <RunFilterCombo
        label="Input format"
        valueLabel={inputFormatValueLabel(filters.inputFormat) || 'Any'}
        activeId={filters.inputFormat}
        placeholder="Input format"
        model={buildInputFormatModel(data)}
        searchable={false}
        onPick={(id) => setFilter('inputFormat', id)}
      />
      {extra}
    </div>
  )
}

interface ChipProps {
  label: string
  value: string
  onRemove: () => void
}

function FilterChip({ label, value, onRemove }: ChipProps) {
  return (
    <span className="flex items-center gap-1.5 rounded-full bg-ink py-1 pl-2.5 pr-1.5 text-[13px] text-white">
      <span className="opacity-70">{label}:</span>
      <span className="font-medium">{value}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${label} filter`}
        title="Remove filter"
        className="flex h-[18px] w-[18px] items-center justify-center rounded-full hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
      >
        <X className="h-[11px] w-[11px]" aria-hidden="true" />
      </button>
    </span>
  )
}

interface ActiveFilterChipsProps extends Omit<RunFilterBarProps, 'currentUserEmail'> {
  /** The Batch filter's chip, shown first; it applies to every user, not only admins. */
  batchChip?: { value: string; onRemove: () => void }
}

/** Dark chips for the active filters, each removable, plus "Clear all". */
export function ActiveFilterChips({ controls, options, runs, currentUserId, batchChip }: ActiveFilterChipsProps) {
  const { filters, setFilter, clearAll } = controls
  if (!hasActiveFilters(filters) && !batchChip) return null
  const runByLabel = filters.runBy ? runByValueLabel(filters.runBy, options?.run_by ?? [], runs, currentUserId) : ''
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {batchChip && <FilterChip label="Batch" value={batchChip.value} onRemove={batchChip.onRemove} />}
      {activeChips(filters, runByLabel).map((chip) => (
        <FilterChip key={chip.key} label={chip.label} value={chip.value} onRemove={() => setFilter(chip.key, '')} />
      ))}
      <button type="button" onClick={clearAll} className="text-[13px] text-primary-700 hover:underline">
        Clear all
      </button>
    </div>
  )
}
