import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, CheckCircle2, Check, Lock, Loader2, XCircle, AlertCircle, ChevronDown, ChevronUp, ChevronRight, MessageSquare } from 'lucide-react'
import { formatRelativeDate } from '../../utils'
import type { RunSummary } from '../../types'
import { OWNER_UNKNOWN_LABEL, earlierRunHasFeedback, runByFilterValue, runByLabel } from './runGroups'
import { FEEDBACK_VALUE_LABEL, feedbackGivenTitle, feedbackState } from './runFeedback'
import type { RunGroup, SortDir, SortField } from './runGroups'
import type { RunFilterKey } from './runFilters'
import { BAND_STYLE, NO_SCORE_TEXT, scoreTitle } from './runQuality'

const CELL = 'px-2 first:pl-5 last:pr-5 py-3'
const WRAP = 'break-words [overflow-wrap:anywhere]'
const LINK_HOVER = 'hover:underline underline-offset-[3px] cursor-pointer'

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'complete':
      return <CheckCircle2 className="w-4 h-4 text-green-600" aria-hidden="true" />
    case 'running':
      return <Loader2 className="w-4 h-4 text-blue-600 animate-spin" aria-hidden="true" />
    case 'failed':
      return <XCircle className="w-4 h-4 text-red-600" aria-hidden="true" />
    case 'cancelled':
      return <AlertCircle className="w-4 h-4 text-orange-600" aria-hidden="true" />
    default:
      return <Clock className="w-4 h-4 text-gray-400" aria-hidden="true" />
  }
}

function statusLabelColor(status: string): string {
  switch (status) {
    case 'complete':
      return 'text-green-600'
    case 'running':
      return 'text-blue-600'
    case 'failed':
      return 'text-red-600'
    case 'cancelled':
      return 'text-orange-600'
    default:
      return 'text-gray-500'
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case 'complete':
      return 'Complete'
    case 'running':
      return 'Running'
    case 'queued':
      return 'Queued'
    case 'failed':
      return 'Failed'
    case 'cancelled':
      return 'Cancelled'
    default:
      return 'Pending'
  }
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—'
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  if (mins > 0) return `${mins}m ${secs}s`
  return `${secs}s`
}

interface RunTableProps {
  groups: RunGroup[]
  isAdmin: boolean
  showCost: boolean
  currentUserId: number | undefined
  sortField: SortField
  sortDir: SortDir
  onSort: (field: SortField) => void
  onSelectRun: (runId: string) => void
  onFilter: (key: RunFilterKey, value: string) => void
  /** Open the batch view for a run's batch (the Batch tag). */
  onOpenBatch: (batchId: string) => void
}

/** "Batch" tag on a run that belongs to a batch upload; opens that batch's view. */
export function BatchTag({ batchId, onOpenBatch }: { batchId: string | null | undefined; onOpenBatch: (batchId: string) => void }) {
  if (!batchId) return null
  return (
    <button
      type="button"
      title="Show the whole batch"
      onClick={(e) => { e.stopPropagation(); onOpenBatch(batchId) }}
      className="flex-none rounded px-1.5 py-px text-[11px] font-semibold text-[#6B5E45] bg-[#F1E8D6] hover:bg-[#E6D9BE] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
    >
      Batch
    </button>
  )
}

interface SortHeaderProps {
  field: SortField
  label: string
  align?: 'left' | 'right' | 'center'
  width?: string
  sortField: SortField
  sortDir: SortDir
  onSort: (field: SortField) => void
}

function SortHeader({ field, label, align = 'left', width = '', sortField, sortDir, onSort }: SortHeaderProps) {
  const active = sortField === field
  const ariaSort = active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'
  const justify = align === 'right' ? 'justify-end' : align === 'center' ? 'justify-center' : ''
  return (
    <th className={`${CELL} text-${align} ${width}`} aria-sort={ariaSort}>
      <button
        type="button"
        onClick={() => onSort(field)}
        className={`group flex items-center gap-1 ${justify} text-xs font-medium text-gray-500 cursor-pointer select-none hover:text-gray-700 w-full`}
      >
        {label}
        {active ? (
          sortDir === 'asc' ? (
            <ChevronUp className="w-3 h-3 text-primary-600" aria-hidden="true" />
          ) : (
            <ChevronDown className="w-3 h-3 text-primary-600" aria-hidden="true" />
          )
        ) : (
          <ChevronDown className="w-3 h-3 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden="true" />
        )}
      </button>
    </th>
  )
}

function ScoreCell({ run, earlier }: { run: RunSummary; earlier: boolean }) {
  const band = run.quality_score != null ? run.quality_band : null
  return (
    <span title={scoreTitle(run)} className="flex items-center gap-1.5 tabular-nums">
      <span aria-hidden="true" className={`h-2 w-2 flex-none rounded-full ${band ? BAND_STYLE[band].dot : 'bg-sand-400'}`} />
      <span className={`font-semibold ${band ? (earlier ? 'text-gray-700' : 'text-gray-900') : 'text-gray-400'}`}>
        {run.quality_score ?? NO_SCORE_TEXT}
      </span>
      {run.quality_cap != null && <Lock className="h-3 w-3 flex-none text-error-700" aria-label="Score capped" />}
    </span>
  )
}

interface FeedbackCellProps {
  run: RunSummary
  isAdmin: boolean
  currentUserId: number | undefined
  /** An older run of the same faculty member has feedback. */
  earlierGiven: boolean
}

function FeedbackCell({ run, isAdmin, currentUserId, earlierGiven }: FeedbackCellProps) {
  const navigate = useNavigate()
  switch (feedbackState(run, currentUserId, isAdmin, earlierGiven)) {
    case 'given':
      return (
        <span title={feedbackGivenTitle(run, isAdmin)} className="inline-flex items-center gap-1 text-xs font-medium text-gray-700">
          <Check className="w-3.5 h-3.5 flex-none text-green-600" aria-hidden="true" />
          Feedback given
        </span>
      )
    case 'earlier':
      return (
        <span className="inline-flex items-center gap-1 text-xs text-gray-500">
          <Check className="w-3.5 h-3.5 flex-none text-gray-400" aria-hidden="true" />
          Given on an earlier run
        </span>
      )
    case 'needed':
      return (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); navigate(`/run/${run.run_id}#feedback`) }}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 text-[11px] font-medium hover:bg-amber-200 transition-colors cursor-pointer"
          title="Go to feedback form"
        >
          <MessageSquare className="w-3 h-3" aria-hidden="true" />
          Needs feedback
        </button>
      )
    case 'awaiting':
      return <span className="text-xs text-gray-500">{FEEDBACK_VALUE_LABEL.needed}</span>
    case 'none':
      return <span className="text-gray-400">{'—'}</span>
  }
}

interface RowProps extends Omit<RunTableProps, 'groups' | 'sortField' | 'sortDir' | 'onSort' | 'onOpenBatch'> {
  run: RunSummary
  /** The cell shown first: faculty name + filename for a group row, filename for an earlier run. */
  firstCell: React.ReactNode
  earlier?: boolean
  /** Latest run of a group whose earlier runs have feedback. */
  earlierGiven?: boolean
}

function RunRow({ run, firstCell, earlier = false, earlierGiven = false, isAdmin, showCost, currentUserId, onSelectRun, onFilter }: RowProps) {
  const { display, tooltip } = formatRelativeDate(run.started_at)
  const muted = earlier ? 'text-gray-500' : ''
  const runBy = runByLabel(run, currentUserId)
  const runByValue = runByFilterValue(run)
  const bg = run.status === 'running' ? 'bg-primary-50' : earlier ? 'bg-sand-50' : 'bg-white'
  return (
    <tr
      onClick={() => onSelectRun(run.run_id)}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onSelectRun(run.run_id) } }}
      tabIndex={0}
      role="link"
      className={`cursor-pointer transition-colors border-b border-sand-200 last:border-b-0 hover:bg-sand-50 ${bg}`}
    >
      <td className={`${CELL} text-left`}>{firstCell}</td>
      {isAdmin && (
        <td className={`${CELL} text-left text-sm ${earlier ? 'text-gray-500' : 'text-gray-700'}`}>
          {runBy && runByValue ? (
            <button
              type="button"
              title="Show only runs by this person"
              onClick={(e) => { e.stopPropagation(); onFilter('runBy', runByValue) }}
              className={`text-left ${WRAP} ${LINK_HOVER}`}
            >
              {runBy}
            </button>
          ) : (
            <span className="text-gray-400">{'—'}</span>
          )}
        </td>
      )}
      <td className={`${CELL} text-left`}>
        <span className="inline-flex items-center gap-1">
          <StatusIcon status={run.status} />
          <span className={`text-sm ${statusLabelColor(run.status)}`}>{statusLabel(run.status)}</span>
        </span>
      </td>
      {isAdmin && (
        <td className={`${CELL} text-left text-sm`}>
          <ScoreCell run={run} earlier={earlier} />
        </td>
      )}
      <td className={`${CELL} text-left text-sm text-gray-500`}>
        <span title={tooltip}>{display}</span>
      </td>
      <td className={`${CELL} text-right text-sm text-gray-500`}>{formatDuration(run.total_duration_seconds)}</td>
      {showCost && (
        <td className={`${CELL} text-right text-sm ${earlier ? 'text-gray-500' : 'text-gray-700'}`}>
          {run.total_cost ? `$${run.total_cost.toFixed(2)}` : '—'}
        </td>
      )}
      <td className={`${CELL} text-center ${muted}`}>
        <FeedbackCell run={run} isAdmin={isAdmin} currentUserId={currentUserId} earlierGiven={earlierGiven} />
      </td>
    </tr>
  )
}

interface GroupCellProps {
  group: RunGroup
  expanded: boolean
  isAdmin: boolean
  onToggle: () => void
  onFilter: (key: RunFilterKey, value: string) => void
  onOpenBatch: (batchId: string) => void
}

function GroupCell({ group, expanded, isAdmin, onToggle, onFilter, onOpenBatch }: GroupCellProps) {
  const { owner, latest, older } = group
  const runCount = older.length + 1
  return (
    <div className="flex items-center gap-2.5 min-w-0">
      {older.length > 0 ? (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onToggle() }}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Hide' : 'Show'} ${older.length} earlier ${older.length === 1 ? 'run' : 'runs'}`}
          title="Show earlier runs"
          className="flex h-[22px] w-[22px] flex-none items-center justify-center rounded-md text-gray-500 hover:bg-sand-200"
        >
          <ChevronRight className={`w-3.5 h-3.5 transition-transform ${expanded ? 'rotate-90' : ''}`} aria-hidden="true" />
        </button>
      ) : (
        <span className="w-[22px] flex-none" aria-hidden="true" />
      )}
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          {owner ? (
            isAdmin ? (
              <button
                type="button"
                title="Show only this faculty member"
                onClick={(e) => { e.stopPropagation(); onFilter('faculty', owner) }}
                className={`text-left text-sm font-semibold text-gray-900 ${WRAP} ${LINK_HOVER}`}
              >
                {owner}
              </button>
            ) : (
              <span className={`text-sm font-semibold text-gray-900 ${WRAP}`}>{owner}</span>
            )
          ) : (
            <span title={latest.filename} className={`text-sm text-gray-900 ${WRAP}`}>{latest.filename}</span>
          )}
          {runCount > 1 && (
            <span className="rounded-full bg-sand-100 px-2 py-px text-xs text-gray-500">{runCount} runs</span>
          )}
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <span title={latest.filename} className={`text-[13px] text-gray-500 ${WRAP}`}>{owner ? latest.filename : OWNER_UNKNOWN_LABEL}</span>
          <BatchTag batchId={latest.batch_id} onOpenBatch={onOpenBatch} />
        </div>
      </div>
    </div>
  )
}

/** Runs grouped by faculty member: latest run per row, earlier reruns behind a chevron. */
export default function RunTable(props: RunTableProps) {
  const { groups, isAdmin, showCost, sortField, sortDir, onSort } = props
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  const header = { sortField, sortDir, onSort }
  const rowProps = {
    isAdmin,
    showCost,
    currentUserId: props.currentUserId,
    onSelectRun: props.onSelectRun,
    onFilter: props.onFilter,
  }
  return (
    <div className="overflow-x-auto">
      <table className={`w-full table-fixed ${isAdmin ? 'min-w-[1000px]' : 'min-w-[640px]'}`}>
        <thead className="sticky top-0 z-header bg-sand-50 border-b border-sand-200">
          <tr>
            <SortHeader field="cv" label={isAdmin ? 'Faculty (subject)' : 'CV'} {...header} />
            {isAdmin && <th className={`${CELL} text-left text-xs font-medium text-gray-500 w-[170px]`}>Run by</th>}
            <SortHeader field="status" label="Status" width="w-[120px]" {...header} />
            {isAdmin && <SortHeader field="quality_score" label="Score" width="w-[84px]" {...header} />}
            <SortHeader field="started_at" label="Last run" width="w-[130px]" {...header} />
            <SortHeader field="total_duration_seconds" label="Duration" align="right" width="w-[72px]" {...header} />
            {showCost && <SortHeader field="total_cost" label="Cost" align="right" width="w-[56px]" {...header} />}
            <SortHeader field="feedback" label="Feedback" align="center" width="w-[150px]" {...header} />
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => {
            const isOpen = expanded.has(group.key)
            return [
              <RunRow
                key={group.latest.run_id}
                run={group.latest}
                earlierGiven={earlierRunHasFeedback(group)}
                firstCell={
                  <GroupCell
                    group={group}
                    expanded={isOpen}
                    isAdmin={isAdmin}
                    onToggle={() => toggle(group.key)}
                    onFilter={props.onFilter}
                    onOpenBatch={props.onOpenBatch}
                  />
                }
                {...rowProps}
              />,
              ...(isOpen
                ? group.older.map((run) => (
                    <RunRow
                      key={run.run_id}
                      run={run}
                      earlier
                      firstCell={
                        <div className="flex items-center gap-2 pl-8">
                          <span title={run.filename} className={`text-[13px] text-gray-500 ${WRAP}`}>{run.filename}</span>
                          <BatchTag batchId={run.batch_id} onOpenBatch={props.onOpenBatch} />
                        </div>
                      }
                      {...rowProps}
                    />
                  ))
                : []),
            ]
          })}
        </tbody>
      </table>
    </div>
  )
}
