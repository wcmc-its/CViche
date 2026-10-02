import { useState } from 'react'
import { Upload } from 'lucide-react'

interface DropZoneProps {
  /** Accept several files (batch) or one. */
  multiple: boolean
  /** e.g. "Drop .docx files here" -- followed by "or browse". */
  title: string
  hint: string
  onFiles: (files: File[]) => void
  compact?: boolean
}

/** The dashed drop target with a hidden file input behind its label. */
export default function DropZone({ multiple, title, hint, onFiles, compact = false }: DropZoneProps) {
  const [isDragging, setIsDragging] = useState(false)

  const take = (list: FileList | null | undefined) => {
    const files = Array.from(list ?? [])
    if (files.length) onFiles(multiple ? files : files.slice(0, 1))
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    // Ignore dragleave events fired when moving over child elements.
    if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsDragging(false)
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        if (!isDragging) setIsDragging(true)
      }}
      onDragLeave={handleDragLeave}
      onDrop={(e) => {
        e.preventDefault()
        setIsDragging(false)
        take(e.dataTransfer.files)
      }}
      className={`relative flex items-center gap-4 rounded-lg border-[1.5px] border-dashed ${compact ? 'px-[18px] py-3.5' : 'p-5'} transition-colors focus-within:ring-2 focus-within:ring-primary-500 ${
        isDragging
          ? 'border-primary-500 bg-primary-50'
          : 'border-sand-400 bg-sand-50/40 hover:bg-sand-50 hover:border-[#B8A67F]'
      }`}
    >
      <input
        type="file"
        multiple={multiple}
        onChange={(e) => {
          take(e.target.files)
          // Clear so choosing the same file again still fires a change.
          e.target.value = ''
        }}
        className="sr-only"
        id="file-upload"
        aria-describedby="file-type-hint"
        data-testid="file-input"
      />
      <div className="flex h-10 w-10 flex-none items-center justify-center rounded-[10px] bg-sand-200 text-[#6B5E45]">
        <Upload className="h-5 w-5" aria-hidden="true" />
      </div>
      <label htmlFor="file-upload" className="block min-w-0 cursor-pointer after:absolute after:inset-0 after:content-['']">
        <span className="block font-medium text-gray-900">
          {title} or <span className="text-primary-700">browse</span>
        </span>
        <span className="block text-[13px] text-gray-500" id="file-type-hint">{hint}</span>
      </label>
    </div>
  )
}
