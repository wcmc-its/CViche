import { useEffect, useRef } from 'react'
import { Loader2 } from 'lucide-react'
import type { ConsentPublishPreview } from '../types'

interface ConsentPublishDialogProps {
  /** The version and affected-user count the admin is confirming. */
  preview: ConsentPublishPreview
  publishing: boolean
  error: string | null
  onConfirm: () => void
  onClose: () => void
}

/** "1 user" / "3 users". */
export function userCountText(count: number): string {
  return `${count.toLocaleString()} ${count === 1 ? 'user' : 'users'}`
}

/** In-app confirmation for publishing a new consent version. Escape and Cancel dismiss;
 *  focus starts on Cancel so a stray Enter does not publish. */
export default function ConsentPublishDialog({ preview, publishing, error, onConfirm, onClose }: ConsentPublishDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    cancelRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !publishing) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, publishing])

  // Keep Tab inside the dialog.
  const trapFocus = (e: React.KeyboardEvent) => {
    if (e.key !== 'Tab' || !dialogRef.current) return
    const items = dialogRef.current.querySelectorAll<HTMLElement>('button:not([disabled])')
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4"
      onClick={publishing ? undefined : onClose}
    >
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="consent-publish-title"
        aria-describedby="consent-publish-body"
        className="bg-white rounded-lg shadow-xl w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={trapFocus}
      >
        <div className="px-6 py-4 border-b border-gray-200">
          <h2 id="consent-publish-title" className="text-lg font-bold text-gray-900">
            Publish consent version {preview.next_version}?
          </h2>
        </div>
        <div id="consent-publish-body" className="px-6 py-4 text-sm text-gray-700 space-y-3">
          <p>
            <strong>{userCountText(preview.users_to_reconsent)}</strong>{' '}
            will be asked to agree again before their next upload.
          </p>
          <p className="text-xs text-gray-500">
            Current version is {preview.current_version}. Only active users are counted. The consent text itself does not change.
          </p>
          {error && <p role="alert" className="text-error-700">{error}</p>}
        </div>
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200">
          <button
            ref={cancelRef}
            type="button"
            onClick={onClose}
            disabled={publishing}
            className="rounded-lg px-4 py-1.5 text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors focus-visible:ring-2 focus-visible:ring-gray-500 focus-visible:outline-none disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={publishing}
            className="inline-flex items-center gap-1.5 rounded-lg px-4 py-1.5 text-sm font-medium bg-ink text-white hover:bg-gray-800 transition-colors focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none disabled:opacity-50"
          >
            {publishing && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
            Publish {preview.next_version}
          </button>
        </div>
      </div>
    </div>
  )
}
