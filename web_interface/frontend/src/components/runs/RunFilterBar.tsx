import { useEffect, useRef, useState } from 'react'
import { ChevronDown, X } from 'lucide-react'
import type { RunByOption, RunFilterOptions, RunSummary, StatusFilterCounts } from '../../types'
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
import {
  INPUT_FORMAT_VALUE_LABEL, RUN_BY_ON_BEHALF, RUN_BY_SELF, STATUS_PILLS, activeStatusPill, isFeedbackFilter,
  isInputFormatFilter,
} from './runFilters'
import type { StatusPillId } from './runFilters'
import type { RunFilterControls, RunFilters } from './runFilters'
import { ON_BEHALF_RUN_BY_LABEL, SELF_RUN_BY_LABEL } from './runGroups'

interface RunFilterBarProps {
  controls: RunFilterControls
  options: RunFilterOptions | null
  /** Loaded runs; the source of the "recent" Run by entries and of names missing from options. */
  runs: RunSummary[]
  currentUserId: number | undefined
  currentUserEmail: string | undefined
}

const EMPTY_OPTIONS: RunFilterOptions = { departments: [], faculty: [], run_by: [], self_count: 0,
  on_behalf_count: 0, status: { all: 0, running: 0, awaiting_feedback: 0, failed: 0, red: 0 },
  feedback: { given: 0, needed: 0 }, input_format: { wcm: 0, other: 0, unknown: 0 } }

/** Display text for the selected Run by value. */
export function runByValueLabel(
  runBy: string,
  people: RunByOption[],
  runs: RunSummary[],
  currentUserId: number | undefined,
): string {
  if (runBy === RUN_BY_SELF) return SELF_RUN_BY_LABEL
  if (runBy === RUN_BY_ON_BEHALF) return ON_BEHALF_RUN_BY_LABEL
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

interface StatusPillsProps {
  controls: RunFilterControls
  /** The pill counts: filter-options' for admins, the caller's own for members; null until loaded. */
  counts: StatusFilterCounts | null
  isAdmin: boolean
}

/** Width of the right-edge fade, in px. */
const FADE_PX = 28

/** True while the row has more pills hidden off its right edge. */
function useMoreToRight(ref: React.RefObject<HTMLDivElement | null>): boolean {
  const [more, setMore] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setMore(el.scrollLeft + el.clientWidth < el.scrollWidth - 1)
    update()
    el.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update)
    return () => {
      el.removeEventListener('scroll', update)
      window.removeEventListener('resize', update)
    }
  }, [ref])
  return more
}

/** The single-select status pills above the table. Never wrap: they scroll sideways on a narrow
 *  screen, with a fade at the right edge while more are hidden. */
export function StatusPills({ controls, counts, isAdmin }: StatusPillsProps) {
  const active = activeStatusPill(controls.filters)
  const rowRef = useRef<HTMLDivElement>(null)
  const more = useMoreToRight(rowRef)
  const mask = more ? `linear-gradient(to right, #000 calc(100% - ${FADE_PX}px), transparent)` : undefined
  return (
    <div
      ref={rowRef}
      role="group"
      aria-label="Show runs"
      style={{ maskImage: mask, WebkitMaskImage: mask }}
      className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1"
    >
      {STATUS_PILLS.filter((pill) => isAdmin || !pill.adminOnly).map((pill) => {
        const on = pill.id === active
        const count = counts?.[pill.id]
        return (
          <button
            key={pill.id}
            type="button"
            aria-pressed={on}
            onClick={() => controls.setStatusPill(pill.id as StatusPillId)}
            className={`shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-600 ${
              on ? 'border-ink bg-ink text-white' : 'border-sand-300 bg-white text-gray-700 hover:bg-sand-50'
            }`}
          >
            {pill.label}
            {count !== undefined && <span className={`ml-1.5 ${on ? 'opacity-70' : 'text-gray-500'}`}>{count}</span>}
          </button>
        )
      })}
    </div>
  )
}

/** The five admin filter comboboxes, shown right-aligned in the filter row; `extra` (the Batch filter) follows them. */
export function RunFilterCombos({ controls, options, runs, currentUserId, currentUserEmail, extra, stacked = false }: RunFilterBarProps & { extra?: React.ReactNode; stacked?: boolean }) {
  const { filters, setFilter } = controls
  const data = options ?? EMPTY_OPTIONS
  const runByLabel = filters.runBy ? runByValueLabel(filters.runBy, data.run_by, runs, currentUserId) : 'Anyone'
  return (
    <div className={stacked ? 'flex flex-col gap-2' : 'flex flex-wrap gap-2 sm:ml-auto'}>
      <RunFilterCombo
        fullWidth={stacked}
        label="Department"
        valueLabel={filters.department || 'All'}
        activeId={filters.department}
        placeholder="Search departments"
        model={buildDepartmentModel(data)}
        onPick={(id) => setFilter('department', id)}
      />
      <RunFilterCombo
        fullWidth={stacked}
        label="Faculty"
        valueLabel={filters.faculty || 'All'}
        activeId={filters.faculty}
        placeholder="Name, CWID or email"
        model={buildFacultyModel(data)}
        onPick={(id) => setFilter('faculty', id)}
      />
      <RunFilterCombo
        fullWidth={stacked}
        label="Run by"
        valueLabel={runByLabel}
        activeId={filters.runBy}
        placeholder="Name, CWID or email"
        model={buildRunByModel(data, currentUserId, currentUserEmail, recentRunByIds(runs, currentUserId))}
        onPick={(id) => setFilter('runBy', id)}
      />
      <RunFilterCombo
        fullWidth={stacked}
        label="Feedback"
        valueLabel={feedbackValueLabel(filters.feedback) || 'Any'}
        activeId={filters.feedback}
        placeholder="Feedback status"
        model={buildFeedbackModel(data)}
        searchable={false}
        onPick={(id) => setFilter('feedback', id)}
      />
      <RunFilterCombo
        fullWidth={stacked}
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

interface FiltersPanelProps {
  /** How many filters inside the panel are on; shown on the button. */
  activeCount: number
  /** The combos, stacked. */
  children: React.ReactNode
}

/** Narrow screens: one "Filters (n)" button that opens the combos in a panel under it.
 *  Escape closes the panel (unless it closed an open combo) and returns focus to the button. */
export function FiltersPanel({ activeCount, children }: FiltersPanelProps) {
  const [open, setOpen] = useState(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  return (
    <div
      className="w-full"
      onKeyDown={(e) => {
        if (e.key !== 'Escape' || e.defaultPrevented || !open) return
        setOpen(false)
        buttonRef.current?.focus()
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls="run-filters-panel"
        onClick={() => setOpen((o) => !o)}
        className={`flex h-9 items-center gap-1.5 rounded-lg border bg-white pl-3 pr-2.5 text-[13px] font-medium text-gray-900 hover:border-sand-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 ${
          activeCount > 0 ? 'border-ink' : 'border-sand-400'
        }`}
      >
        {activeCount > 0 ? `Filters (${activeCount})` : 'Filters'}
        <ChevronDown className={`h-3.5 w-3.5 text-gray-500 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>
      {open && (
        <div id="run-filters-panel" role="group" aria-label="Filters" className="mt-2 rounded-xl border border-sand-300 bg-sand-50 p-3">
          {children}
        </div>
      )}
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
  const runByLabel = filters.runBy ? runByValueLabel(filters.runBy, options?.run_by ?? [], runs, currentUserId) : ''
  // The status pills show their own state, so they get no chip; "All faculty" clears them.
  const chips = activeChips(filters, runByLabel)
  if (chips.length === 0 && !batchChip) return null
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {batchChip && <FilterChip label="Batch" value={batchChip.value} onRemove={batchChip.onRemove} />}
      {chips.map((chip) => (
        <FilterChip key={chip.key} label={chip.label} value={chip.value} onRemove={() => setFilter(chip.key, '')} />
      ))}
      <button type="button" onClick={clearAll} className="text-[13px] text-primary-700 hover:underline">
        Clear all
      </button>
    </div>
  )
}
