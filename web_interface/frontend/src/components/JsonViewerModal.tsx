import { useEffect, useRef, useMemo, useCallback } from 'react'
import { X, Download } from 'lucide-react'

interface JsonViewerModalProps {
  isOpen: boolean
  onClose: () => void
  content: any
  filename: string
  downloadUrl: string
}

export default function JsonViewerModal({
  isOpen,
  onClose,
  content,
  filename,
  downloadUrl,
}: JsonViewerModalProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const modalRef = useRef<HTMLDivElement>(null)

  const formattedJson = useMemo(() => {
    if (content == null) return null
    try {
      return JSON.stringify(content, null, 2)
    } catch {
      return String(content)
    }
  }, [content])

  // Close on Escape key
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

  // Focus the close button when the modal opens
  useEffect(() => {
    if (isOpen) {
      closeButtonRef.current?.focus()
    }
  }, [isOpen])

  // Focus trap: keep focus within the modal while it is open
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
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
    },
    []
  )

  if (!isOpen) return null

  const titleId = 'json-viewer-modal-title'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50"
      onClick={onClose}
    >
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="bg-white rounded-lg shadow-xl flex flex-col"
        style={{ width: '90%', height: '60vh' }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 id={titleId} className="text-lg font-bold text-gray-900 truncate">
            {filename}
          </h2>
          <div className="flex items-center gap-3">
            <a
              href={downloadUrl}
              download
              aria-label={`Download ${filename}`}
              className="inline-flex items-center gap-1.5 px-3 py-1 text-sm bg-blue-500 text-white rounded hover:bg-blue-600 focus:ring-2 focus:ring-blue-500 focus:outline-none"
            >
              <Download size={14} />
              <span>Download</span>
            </a>
            <button
              ref={closeButtonRef}
              onClick={onClose}
              aria-label="Close JSON viewer"
              className="text-gray-500 hover:text-gray-700 p-1 rounded focus:ring-2 focus:ring-blue-500 focus:outline-none"
            >
              <X size={24} />
            </button>
          </div>
        </div>

        {/* Content - Scrollable JSON Viewer */}
        <div className="flex-1 overflow-auto p-6 bg-gray-50">
          {formattedJson != null ? (
            <pre className="bg-gray-900 text-green-400 p-6 rounded-lg text-sm font-mono leading-relaxed shadow-inner whitespace-pre-wrap">
              {formattedJson}
            </pre>
          ) : (
            <div className="text-gray-500 text-center py-12">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
              <p>Loading JSON...</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
