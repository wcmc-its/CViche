interface PromptLogViewerProps {
  promptLogs: { filename: string; content: string }[]
  promptLogsMessage: string | null
  selectedPromptLog: string | null
  onSelectPromptLog: (filename: string) => void
}

export default function PromptLogViewer({
  promptLogs,
  promptLogsMessage,
  selectedPromptLog,
  onSelectPromptLog,
}: PromptLogViewerProps) {
  const selectedContent = promptLogs.find(
    (l) => l.filename === selectedPromptLog
  )?.content

  if (promptLogs.length === 0) {
    return (
      <div
        className="flex items-center justify-center bg-sand-50 rounded-[10px] border border-sand-200 text-gray-500 text-sm"
        style={{ height: '60vh' }}
      >
        <p>{promptLogsMessage || 'No prompt logs available for this stage.'}</p>
      </div>
    )
  }

  return (
    <div
      className="flex flex-col md:flex-row gap-4"
      style={{ height: '60vh' }}
    >
      {/* Left sidebar - file list */}
      <nav
        className="w-full md:w-64 flex-shrink-0 bg-sand-50 border border-sand-200 rounded-[10px] p-2 overflow-y-auto"
        aria-label="Prompt log files"
      >
        <ul className="space-y-1" role="list">
          {promptLogs.map((log, i) => (
            <li key={i}>
              <button
                onClick={() => onSelectPromptLog(log.filename)}
                className={`w-full text-left px-3 py-2 rounded text-xs truncate transition-colors focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none ${
                  selectedPromptLog === log.filename
                    ? 'bg-primary-600 text-white'
                    : 'bg-white hover:bg-sand-100 text-gray-700'
                }`}
                title={log.filename}
                aria-current={
                  selectedPromptLog === log.filename ? 'true' : undefined
                }
              >
                {log.filename.split('/').pop()}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      {/* Right panel - content display */}
      <div className="flex-1 min-w-0 bg-white rounded-[10px] border border-sand-300 overflow-hidden flex flex-col">
        {/* Browser-chrome-style titlebar */}
        <div className="bg-sand-50 px-4 py-2 text-xs font-semibold text-gray-600 border-b border-sand-200 flex items-center gap-2">
          <div
            className="w-3 h-3 rounded-full bg-red-400"
            aria-hidden="true"
          />
          <div
            className="w-3 h-3 rounded-full bg-yellow-400"
            aria-hidden="true"
          />
          <div
            className="w-3 h-3 rounded-full bg-green-400"
            aria-hidden="true"
          />
          <span className="ml-2 truncate">
            {selectedPromptLog
              ? selectedPromptLog.split('/').pop()
              : 'Select a prompt log'}
          </span>
        </div>

        {/* Content area */}
        <div className="flex-1 overflow-auto p-4 bg-sand-50">
          {selectedPromptLog ? (
            <pre className="text-xs font-mono whitespace-pre-wrap text-gray-800">
              <code>{selectedContent || 'No content available'}</code>
            </pre>
          ) : (
            <div className="text-gray-500 text-center py-8">
              Select a prompt log to view its contents
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
