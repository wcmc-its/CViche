import { useEffect, useRef } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useCanSeeCost } from '../contexts/AuthContext'

interface CancelConfirmModalProps {
  isOpen: boolean
  // Busy state for the confirm action. While true the primary button is
  // disabled and shows "Cancelling..." so the user can't double-fire the
  // cancel request (mirrors the isCancelling flag in PipelineViewer).
  isCancelling: boolean
  // Proceed with cancellation — wired to the existing cancelRun flow.
  onConfirm: () => void
  // Dismiss the modal and keep the run going ("Keep running").
  onClose: () => void
}

export default function CancelConfirmModal({
  isOpen,
  isCancelling,
  onConfirm,
  onClose,
}: CancelConfirmModalProps) {
  const showCost = useCanSeeCost()
  // Default focus to the dismissive ("Keep running") action so an accidental
  // Enter press doesn't tear down a run; cancelling should be deliberate.
  const keepRunningButtonRef = useRef<HTMLButtonElement>(null)
  const modalRef = useRef<HTMLDivElement>(null)

  // Close on Escape key — Escape is treated as "keep running" (the safe choice).
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

  // Focus the "Keep running" button when the modal opens
  useEffect(() => {
    if (isOpen) {
      keepRunningButtonRef.current?.focus()
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
      // Shift+Tab: if focus is on first element, wrap to last
      if (document.activeElement === firstElement) {
        e.preventDefault()
        lastElement.focus()
      }
    } else {
      // Tab: if focus is on last element, wrap to first
      if (document.activeElement === lastElement) {
        e.preventDefault()
        firstElement.focus()
      }
    }
  }

  if (!isOpen) return null

  const titleId = 'cancel-confirm-modal-title'
  const descId = 'cancel-confirm-modal-description'

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
            Cancel this run?
          </h2>
        </div>

        {/* Body — spells out the implications of cancelling */}
        <div id={descId} className="px-6 py-4 text-sm text-gray-700">
          <p className="mb-3">Before you cancel, note that:</p>
          <ul className="list-disc space-y-2 pl-5">
            <li>
              Any output produced so far will be <strong>partial and likely unusable</strong> —
              the document is only complete once every stage finishes.
            </li>
            {showCost && (
              <li>
                The cost already incurred for the stages that have run is{' '}
                <strong>not refunded</strong>.
              </li>
            )}
            <li>
              Cancellation <strong>takes a moment to take effect</strong>; the run stops at the
              next safe point rather than instantly.
            </li>
            <li>
              This <strong>can't be undone</strong>. To process this CV again you'll need to
              restart the run.
            </li>
          </ul>
        </div>

        {/* Footer — actions */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200">
          <button
            ref={keepRunningButtonRef}
            onClick={onClose}
            disabled={isCancelling}
            className="rounded-lg px-4 py-1.5 text-sm font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors focus-visible:ring-2 focus-visible:ring-gray-500 focus-visible:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Keep running
          </button>
          <button
            onClick={onConfirm}
            disabled={isCancelling}
            className="rounded-lg px-4 py-1.5 text-sm font-medium bg-red-600 text-white hover:bg-red-700 transition-colors focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isCancelling ? 'Cancelling...' : 'Cancel run'}
          </button>
        </div>
      </div>
    </div>
  )
}
