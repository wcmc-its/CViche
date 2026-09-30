import React, { useState } from 'react'

interface LogViewerProps {
  logs: string[]
  logsEndRef: React.RefObject<HTMLDivElement>
}

// Lines arrive as "[HH:MM:SS] message" (usePipelineRun formats them).
const LINE = /^\[([^\]]+)\]\s?(.*)$/s

function lineTone(message: string): string {
  if (/^\s*(error|failed|traceback)/i.test(message)) return 'text-red-300'
  if (/^\s*warn/i.test(message)) return 'text-amber-300'
  return 'text-gray-100'
}

export default function LogViewer({ logs, logsEndRef }: LogViewerProps) {
  const [copied, setCopied] = useState(false)
  const text = logs.join('\n')

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard blocked (permissions/insecure context): Download still works.
    }
  }

  const handleDownload = () => {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'step-logs.txt'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <section aria-label="Step logs" className="overflow-hidden rounded-[10px] bg-gray-900">
      <div className="flex items-center justify-between gap-3 border-b border-white/10 px-3.5 py-2 text-xs text-gray-400">
        <span className="font-mono">{logs.length} {logs.length === 1 ? 'line' : 'lines'}</span>
        {logs.length > 0 && (
          <span className="flex gap-3">
            <button type="button" onClick={handleCopy} className="hover:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500 rounded">
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button type="button" onClick={handleDownload} className="hover:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500 rounded">
              Download
            </button>
          </span>
        )}
      </div>
      <div className="max-h-[320px] overflow-y-auto px-3.5 py-3 font-mono text-[13px] leading-[1.7]">
        {logs.length > 0 ? (
          <>
            {logs.map((log, i) => {
              const m = LINE.exec(log)
              const time = m ? m[1] : ''
              const message = m ? m[2] : log
              return (
                <div key={i} className="grid grid-cols-[auto_1fr] gap-x-4">
                  <span className="text-gray-500 tabular-nums">{time}</span>
                  <span className={`${lineTone(message)} break-words`}>{message}</span>
                </div>
              )
            })}
            <div ref={logsEndRef} />
          </>
        ) : (
          <div className="text-gray-500">Pipeline is running, logs will appear here...</div>
        )}
      </div>
    </section>
  )
}
