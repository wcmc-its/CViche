import { Loader2 } from 'lucide-react'

interface UploadFooterProps {
  /** The estimate block, when there is one. */
  summary: React.ReactNode
  /** Finish-time line from GET /api/queue; '' when not in queue mode. */
  finish: string
  /** Non-admin quota line; '' for admins or unlimited users. */
  quota: string
  missing: string[]
  label: string
  busy: boolean
  disabled: boolean
  onStart: () => void
}

/** The page footer: estimate, finish time, quota, what's missing, and the start button. */
export default function UploadFooter({ summary, finish, quota, missing, label, busy, disabled, onStart }: UploadFooterProps) {
  return (
    <div className="mt-1 flex flex-wrap items-center justify-between gap-4 border-t border-sand-200 pt-[18px]">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5 text-[13px] text-gray-500">
        {summary}
        {finish && <span>{finish}</span>}
        {quota && <span>{quota}</span>}
        {missing.length > 0 && (
          <ul className="flex flex-col gap-0.5" aria-label="Still needed">
            {missing.map((m) => (
              <li key={m} className="flex items-center gap-1.5 text-[#9A3412]">
                <span aria-hidden="true" className="h-[5px] w-[5px] flex-none rounded-full bg-[#C2410C]" />
                {m}
              </li>
            ))}
          </ul>
        )}
      </div>
      <button
        onClick={onStart}
        disabled={disabled}
        className="rounded-lg bg-primary-600 px-5 py-[11px] font-semibold text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:bg-gray-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 max-sm:w-full"
        style={{ touchAction: 'manipulation' }}
      >
        {busy ? (
          <span className="flex items-center justify-center gap-2">
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
            {label}
          </span>
        ) : (
          label
        )}
      </button>
    </div>
  )
}
