import { FileText, X } from 'lucide-react'

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** The chosen file of a single run, with its remove button. */
export default function SingleFileRow({ file, disabled, onRemove }: { file: File; disabled: boolean; onRemove: () => void }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-sand-200 px-3.5 py-3">
      <FileText className="h-5 w-5 flex-none text-primary-600" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium text-gray-900">{file.name}</div>
        <div className="text-xs text-gray-500">{formatFileSize(file.size)}</div>
      </div>
      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`Remove ${file.name}`}
        title="Remove"
        className="flex h-7 w-7 flex-none items-center justify-center rounded-md text-gray-500 hover:bg-sand-100 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        <X className="h-3.5 w-3.5" strokeWidth={2.2} aria-hidden="true" />
      </button>
    </div>
  )
}
