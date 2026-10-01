import { AlertCircle, CheckCircle2, Circle, Loader2, X, XCircle } from 'lucide-react'
import { formatCost, formatMinutes } from '../../utils'
import { estimateCost, estimateMinutes, rowNote } from './batchRows'
import type { BatchRow, RowState } from './batchRows'
import { formatFileSize } from './SingleFileRow'

const GRID = 'grid grid-cols-[28px_minmax(0,1fr)_64px_76px_minmax(130px,190px)_28px] gap-3'
/** Past this many rows the list scrolls inside its box. */
const SCROLL_AFTER_ROWS = 12

type Shown = RowState | 'invalid'

const STATUS: Record<Shown, { label: string; text: string; icon: React.ReactNode }> = {
  ready: { label: 'Ready', text: 'text-gray-500', icon: <Circle className="h-4 w-4 text-sand-400" aria-hidden="true" /> },
  waiting: { label: 'Waiting', text: 'text-gray-500', icon: <Circle className="h-4 w-4 text-sand-400" aria-hidden="true" /> },
  uploading: { label: 'Uploading', text: 'text-primary-700', icon: <Loader2 className="h-4 w-4 animate-spin text-primary-600" aria-hidden="true" /> },
  queued: { label: 'Queued', text: 'text-success-700', icon: <CheckCircle2 className="h-4 w-4 text-success-700" aria-hidden="true" /> },
  failed: { label: 'Failed', text: 'text-error-600', icon: <XCircle className="h-4 w-4 text-error-600" aria-hidden="true" /> },
  invalid: { label: "Won't be submitted", text: 'text-amber-700', icon: <AlertCircle className="h-4 w-4 text-amber-700" aria-hidden="true" /> },
}

function EstimateCell({ row, showCost }: { row: BatchRow; showCost: boolean }) {
  if (row.invalidReason) return <span className="text-gray-400">{'—'}</span>
  if (row.estimate === undefined) return <span className="text-gray-400">{'…'}</span>
  if (row.estimate === null) return <span className="text-gray-400">{'—'}</span>
  const cost = estimateCost(row.estimate)
  return (
    <span className="flex flex-col leading-tight">
      <span className="text-gray-700">{formatMinutes(estimateMinutes(row.estimate))}</span>
      {showCost && cost !== null && <span className="text-xs text-gray-400">{formatCost(cost)}</span>}
    </span>
  )
}

interface RowProps {
  row: BatchRow
  index: number
  showCost: boolean
  editable: boolean
  onRemove: (key: string) => void
}

function FileRow({ row, index, showCost, editable, onRemove }: RowProps) {
  const status = STATUS[row.invalidReason ? 'invalid' : row.state]
  const note = rowNote(row)
  const bg = row.state === 'failed' ? 'bg-error-50' : row.state === 'uploading' ? 'bg-primary-50' : 'bg-white'
  return (
    <li className={`${GRID} items-center border-b border-sand-200 px-3.5 py-2.5 last:border-b-0 ${bg}`} data-testid="batch-row">
      <span className="text-right text-xs tabular-nums text-gray-400">{index + 1}</span>
      <span title={row.file.name} className={`truncate font-medium text-gray-900 ${row.invalidReason ? 'opacity-60' : ''}`}>{row.file.name}</span>
      <span className="text-right text-[13px] tabular-nums text-gray-500">{formatFileSize(row.file.size)}</span>
      <span className="text-right text-[13px] tabular-nums"><EstimateCell row={row} showCost={showCost} /></span>
      <span className="flex min-w-0 flex-col gap-px">
        <span className={`flex items-center gap-1.5 text-[13px] font-medium ${status.text}`}>{status.icon}{status.label}</span>
        {note && <span className={`pl-[22px] text-xs ${row.state === 'failed' ? 'text-error-700' : 'text-warning-800'}`}>{note}</span>}
      </span>
      <span className="flex justify-end">
        {editable && (
          <button
            type="button"
            onClick={() => onRemove(row.key)}
            aria-label={`Remove ${row.file.name}`}
            title="Remove"
            className="flex h-7 w-7 items-center justify-center rounded-md text-gray-500 hover:bg-sand-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          >
            <X className="h-3.5 w-3.5" strokeWidth={2.2} aria-hidden="true" />
          </button>
        )}
      </span>
    </li>
  )
}

interface BatchFileTableProps {
  rows: BatchRow[]
  showCost: boolean
  editable: boolean
  onRemove: (key: string) => void
}

/** Step 2's file table: #, File, Size, Estimate (+ cost for admins), Status, remove. */
export default function BatchFileTable({ rows, showCost, editable, onRemove }: BatchFileTableProps) {
  return (
    <div className="overflow-x-auto rounded-[10px] border border-sand-200">
      <div className="min-w-[520px]">
        <div className={`${GRID} border-b border-sand-200 bg-sand-50 px-3.5 py-2 text-xs font-medium text-gray-500`}>
          <span className="text-right">#</span><span>File</span><span className="text-right">Size</span>
          <span className="text-right">Estimate</span><span>Status</span><span />
        </div>
        <ul className={rows.length > SCROLL_AFTER_ROWS ? 'max-h-[520px] overflow-y-auto' : ''} aria-label="Files">
          {rows.map((row, i) => (
            <FileRow key={row.key} row={row} index={i} showCost={showCost} editable={editable} onRemove={onRemove} />
          ))}
        </ul>
      </div>
    </div>
  )
}
