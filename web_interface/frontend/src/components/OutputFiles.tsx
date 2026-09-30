import { FileText, Download } from 'lucide-react'
import { runRoutes } from '../api/routes'
import { useAuth } from '../contexts/AuthContext'

interface OutputFilesProps {
  runId: string
  step: {
    step_number: number
    stage_id?: string
    status: string
    output_files?: string
  }
  onOpenJson: (filename: string) => void
  /** Set false when the finished document is offered elsewhere (the run header). */
  showFinalOutput?: boolean
}

/** Sand panel with a DOCX tile, the file name and a primary Download button. */
export function DocxDownloadCard({ runId, filename }: { runId: string; filename: string }) {
  return (
    <div className="flex flex-wrap items-center gap-4 p-4 rounded-[10px] bg-sand-50 border border-sand-200">
      <div
        aria-hidden="true"
        className="w-11 h-[52px] flex-none rounded-md bg-white border border-sand-400 flex items-end justify-center pb-1.5 text-[10px] font-bold text-primary-700"
      >
        DOCX
      </div>
      <div className="flex-1 min-w-[200px]">
        <div className="font-semibold text-gray-900 break-all">{filename}</div>
        <div className="text-[13px] text-gray-500">WCM institutional format Word document</div>
      </div>
      <a
        href={runRoutes.dataFile(runId, filename)}
        download
        aria-label={`Download final output file ${filename}`}
        className="inline-flex items-center gap-2 px-5 py-2.5 bg-primary-600 hover:bg-primary-700 text-white font-semibold rounded-lg transition-colors focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 focus:outline-none"
      >
        <Download className="w-4 h-4" aria-hidden="true" />
        <span>Download</span>
      </a>
    </div>
  )
}

export default function OutputFiles({ runId, step, onOpenJson, showFinalOutput = true }: OutputFilesProps) {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'

  let outputFiles: string[] = []
  try {
    outputFiles = step.output_files ? JSON.parse(step.output_files) : []
  } catch {
    outputFiles = []
  }

  if (step.status !== 'complete' || outputFiles.length === 0) {
    return null
  }

  const isFinalStep = step.stage_id === '6'
  const docxFile = outputFiles.find(f => f.endsWith('.docx'))

  // Stage JSON files are internal pipeline artifacts -- admin-only (the backend
  // enforces this too). Hide them entirely from non-admins; the final .docx and
  // other outputs stay visible to the run owner.
  const isJsonName = (f: string) => (f.split('/').pop() || f).endsWith('.json')

  // For the final step, exclude the docx from the additional files list
  // since it's already shown prominently in the Final Output section
  const additionalFiles = (isFinalStep && docxFile
    ? outputFiles.filter(f => f !== docxFile)
    : outputFiles
  ).filter(f => isAdmin || !isJsonName(f))

  return (
    <section aria-label="Output files">
      {/* Final Output — only for stage 6 with a .docx */}
      {showFinalOutput && isFinalStep && docxFile && (
        <div className="mb-6">
          <h3 className="text-sm font-semibold text-gray-700 mb-2">Final Output</h3>
          <DocxDownloadCard runId={runId} filename={docxFile.split('/').pop() || docxFile} />
        </div>
      )}

      {/* Additional / Output Files list — skip if no files remain after filtering */}
      {additionalFiles.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-700 mb-2">
            {isFinalStep ? 'Additional Files' : 'Output Files'}
          </h3>
          <div className="bg-sand-50 border border-sand-200 rounded-[10px] p-4 space-y-2">
            {additionalFiles.map((file, idx) => {
              const filename = file.split('/').pop() || file
              const isJson = filename.endsWith('.json')
              const isDocx = filename.endsWith('.docx')

              return (
                <div key={idx} className="flex items-center gap-3">
                  {isJson ? (
                    <>
                      <button
                        onClick={() => onOpenJson(filename)}
                        aria-label={`View JSON file ${filename}`}
                        className="text-blue-600 hover:text-blue-800 text-sm font-medium flex items-center gap-1.5 focus:ring-2 focus:ring-primary-500 focus:outline-none rounded"
                      >
                        <FileText className="w-4 h-4" aria-hidden="true" />
                        {filename}
                      </button>
                      <a
                        href={runRoutes.dataFile(runId, filename)}
                        download
                        aria-label={`Download ${filename}`}
                        className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1 focus:ring-2 focus:ring-primary-500 focus:outline-none rounded"
                      >
                        <Download className="w-3 h-3" aria-hidden="true" />
                        Download
                      </a>
                    </>
                  ) : isDocx ? (
                    <a
                      href={runRoutes.dataFile(runId, filename)}
                      download
                      aria-label={`Download ${filename}`}
                      className="text-blue-600 hover:text-blue-800 text-sm font-medium flex items-center gap-1.5 focus:ring-2 focus:ring-primary-500 focus:outline-none rounded"
                    >
                      <FileText className="w-4 h-4" aria-hidden="true" />
                      {filename}
                    </a>
                  ) : (
                    <a
                      href={runRoutes.dataFile(runId, filename)}
                      aria-label={`Open ${filename}`}
                      className="text-blue-600 hover:text-blue-800 text-sm font-medium flex items-center gap-1.5 focus:ring-2 focus:ring-primary-500 focus:outline-none rounded"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <FileText className="w-4 h-4" aria-hidden="true" />
                      {filename}
                    </a>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
