import { useEffect, useRef } from 'react'
import { AlertTriangle } from 'lucide-react'

interface DeleteFeedbackModalProps {
  isOpen: boolean
  // The feedback being deleted (run id + free-text issue) so the admin can
  // confirm they are purging the right submission. Null while no row is queued.
  runId: string
  biggestIssue: string
  // Busy state for the confirm action. While true the primary button is
  // disabled and shows "Deleting..." so the request can't be double-fired.
  isDeleting: boolean
  // Proceed with the hard delete.
  onConfirm: () => void
  // Dismiss the modal and keep the feedback (the safe choice).
  onClose: () => void
}

export default function DeleteFeedbackModal({
  isOpen,
  runId,
  biggestIssue,
  isDeleting,
  onConfirm,
  onClose,
}: DeleteFeedbackModalProps) {
  // Default focus to the dismissive ("Keep it") action so an accidental Enter
  // press doesn't permanently delete a submission; deletion should be deliberate.
  const cancelButtonRef = useRef<HTMLButtonElement>(null)
  const modalRef = useRef<HTMLDivElement>(null)

  // Close on Escape key — Escape is treated as "keep it" (the safe choice).
  useEffect(() => {
    if (!isOpen) return

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  // Focus the dismissive button when the modal opens
  useEffect(() => {
    if (isOpen) {
      cancelButtonRef.current?.focus()
    }
  }, [isOpen])

  // Focus trap: keep focus within the modal while it is open
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'Tab' || !modalRef.current) return

    const focusableElements = modalRef.current.querySelectorAll<HTMLElement>(
      'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )

    if (focusableElements.length === 0) return

    const firstElement = focusableElements[0]
    const lastElement = focusableElements[focusableElements.length - 1]

    if (e.shiftKey) {
      if (document.activeElement === firstElement) {
        e.preventDefault()
        lastElement.focus()
      }
    } else {
      if (document.activeElement === lastElement) {
        e.preventDefault()
        firstElement.focus()
      }
    }
  }

  if (!isOpen) return null

  const titleId = 'delete-feedback-modal-title'
  const descId = 'delete-feedback-modal-description'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4"
      onClick={onClose}
    >
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        ref={modalRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        className="bg-white rounded-lg shadow-xl flex flex-col w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-200">
          <AlertTriangle className="h-6 w-6 text-red-600 flex-shrink-0" aria-hidden="true" />
          <h2 id={titleId} className="text-lg font-bold text-gray-900">
            Delete this feedback?
          </h2>
        </div>

        {/* Body */}
        <div id={descId} className="px-6 py-4 text-sm text-gray-700">
          <p className="mb-3">
            Are you sure? This permanently deletes this feedback submission for run{' '}
            <span className="font-mono font-medium">{runId}</span> and cannot be undone.
          </p>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Biggest issue
          </p>
          <blockquote className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-800">
            {biggestIssue.trim() || <span className="italic text-gray-400">(no text provided)</span>}
          </blockquote>
        </div>

        {/* Footer — actions */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200">
          <button
            ref={cancelButtonRef}
            onClick={onClose}
            disabled={isDeleting}
            className="rounded-lg px-4 py-1.5 text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors focus-visible:ring-2 focus-visible:ring-gray-500 focus-visible:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Keep it
          </button>
          <button
            onClick={onConfirm}
            disabled={isDeleting}
            className="rounded-lg px-4 py-1.5 text-sm font-medium bg-red-600 text-white hover:bg-red-700 transition-colors focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isDeleting ? 'Deleting...' : 'Delete feedback'}
          </button>
        </div>
      </div>
    </div>
  )
}
