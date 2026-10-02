import { Mail } from 'lucide-react'
import type { InboxItem } from '../../types'
import { formatDate } from '../../utils'
import { H2 } from './UploadSections'
import { formatFileSize } from './SingleFileRow'

interface InboxSectionProps {
  /** Held emailed CVs not yet in the batch table. */
  items: InboxItem[]
  onAdd: (items: InboxItem[]) => void
  onDiscard: (id: number) => void
  /** Add is only offered where the batch table exists (queue mode). */
  canAdd: boolean
}

const BUTTON = 'rounded-md px-2.5 py-1 text-[13px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500'

function InboxRow({ item, onAdd, onDiscard, canAdd }: { item: InboxItem } & Omit<InboxSectionProps, 'items'>) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-sand-200 px-3.5 py-2.5 last:border-b-0">
      <span className="flex min-w-0 flex-1 basis-48 flex-col">
        <span title={item.filename} className="truncate font-medium text-gray-900">{item.filename}</span>
        <span className="text-xs text-gray-500">
          {formatFileSize(item.size_bytes)} &middot; received {formatDate(item.received_at)}
        </span>
        {item.duplicate && (
          <span className="text-xs text-warning-800">Already processed on {item.duplicate.last_processed_on}</span>
        )}
      </span>
      <span className="flex items-center gap-1">
        {canAdd && (
          <button type="button" onClick={() => onAdd([item])} aria-label={`Add ${item.filename} to this batch`}
            className={`${BUTTON} text-primary-700 hover:bg-primary-50`}>Add</button>
        )}
        <button type="button" onClick={() => onDiscard(item.id)} aria-label={`Discard ${item.filename}`}
          className={`${BUTTON} text-gray-600 hover:bg-sand-100`}>Discard</button>
      </span>
    </li>
  )
}

/** CVs that arrived by email and were held for confirmation (duplicates, over quota, outdated consent). */
export default function InboxSection({ items, onAdd, onDiscard, canAdd }: InboxSectionProps) {
  if (!items.length) return null
  return (
    <section aria-labelledby="inbox-heading" className="mb-5 rounded-xl border border-sand-300 bg-white p-5 shadow-[0_1px_2px_rgba(60,40,10,0.05)] sm:p-6">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="inbox-heading" className={`${H2} flex items-center gap-2`}>
          <Mail className="h-4 w-4 text-gray-500" aria-hidden="true" />
          Emailed to CViche ({items.length})
        </h2>
        {canAdd && items.length > 1 && (
          <button type="button" onClick={() => onAdd(items)} className="text-[13px] font-medium text-primary-700 hover:underline">
            Add all {items.length} to this batch
          </button>
        )}
      </div>
      <p className="mb-3 text-[13px] text-gray-500">
        These emailed CVs are waiting for you to confirm. Add them to a batch below to process them, or discard the ones you don&apos;t want.
      </p>
      <ul aria-label="Emailed CVs" className="overflow-hidden rounded-[10px] border border-sand-200">
        {items.map((item) => (
          <InboxRow key={item.id} item={item} onAdd={onAdd} onDiscard={onDiscard} canAdd={canAdd} />
        ))}
      </ul>
    </section>
  )
}
