import React from 'react'

interface LogViewerProps {
  logs: string[]
  logsEndRef: React.RefObject<HTMLDivElement>
}

export default function LogViewer({ logs, logsEndRef }: LogViewerProps) {
  return (
    <section aria-label="Step logs">
      <div
        className="bg-gray-900 rounded-[10px] p-4 text-sm font-mono text-gray-100 overflow-y-auto"
        style={{ height: '60vh' }}
      >
        {logs.length > 0 ? (
          <>
            {logs.map((log, i) => (
              <div key={i}>{log}</div>
            ))}
            <div ref={logsEndRef} />
          </>
        ) : (
          <div className="text-gray-500">
            Pipeline is running, logs will appear here...
          </div>
        )}
      </div>
    </section>
  )
}
