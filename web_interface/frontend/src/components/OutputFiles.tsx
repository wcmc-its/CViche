import { FileText, Download } from 'lucide-react'

interface OutputFilesProps {
  runId: string
  step: {
    step_number: number
    stage_id?: string
    status: string
    output_files?: string
  }
  onOpenJson: (filename: string) => void
}

export default function OutputFiles({ runId, step, onOpenJson }: OutputFilesProps) {
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

  // For the final step, exclude the docx from the additional files list
  // since it's already shown prominently in the Final Output section
  const additionalFiles = isFinalStep && docxFile
    ? outputFiles.filter(f => f !== docxFile)
    : outputFiles

  return (
    <section className="mt-6" aria-label="Output files">
      {/* Final Output — only for stage 6 with a .docx */}
      {isFinalStep && docxFile && (() => {
        const filename = docxFile.split('/').pop() || docxFile

        return (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-gray-700 mb-2">Final Output</h3>
            <div className="bg-gradient-to-r from-blue-50 to-blue-100 border-2 border-blue-300 rounded-lg p-6">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <FileText className="w-10 h-10 text-blue-600" aria-hidden="true" />
                  <div>
                    <div className="font-semibold text-lg text-gray-900">WCM Template Document</div>
                    <div className="text-sm text-gray-600">{filename}</div>
                  </div>
                </div>
                <a
                  href={`/api/run/${runId}/data/${filename}`}
                  download
                  aria-label={`Download final output file ${filename}`}
                  className="px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg shadow-md transition-colors flex items-center gap-2 focus:ring-2 focus:ring-blue-500 focus:outline-none"
                >
                  <Download className="w-5 h-5" aria-hidden="true" />
                  <span>Download</span>
                </a>
              </div>
            </div>
          </div>
        )
      })()}

      {/* Additional / Output Files list — skip if no files remain after filtering */}
      {additionalFiles.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-700 mb-2">
            {isFinalStep ? 'Additional Files' : 'Output Files'}
          </h3>
          <div className="bg-gray-50 rounded-lg p-4 space-y-2">
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
                        className="text-blue-600 hover:text-blue-800 text-sm font-medium flex items-center gap-1.5 focus:ring-2 focus:ring-blue-500 focus:outline-none rounded"
                      >
                        <FileText className="w-4 h-4" aria-hidden="true" />
                        {filename}
                      </button>
                      <a
                        href={`/api/run/${runId}/data/${filename}`}
                        download
                        aria-label={`Download ${filename}`}
                        className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1 focus:ring-2 focus:ring-blue-500 focus:outline-none rounded"
                      >
                        <Download className="w-3 h-3" aria-hidden="true" />
                        Download
                      </a>
                    </>
                  ) : isDocx ? (
                    <a
                      href={`/api/run/${runId}/data/${filename}`}
                      download
                      aria-label={`Download ${filename}`}
                      className="text-blue-600 hover:text-blue-800 text-sm font-medium flex items-center gap-1.5 focus:ring-2 focus:ring-blue-500 focus:outline-none rounded"
                    >
                      <FileText className="w-4 h-4" aria-hidden="true" />
                      {filename}
                    </a>
                  ) : (
                    <a
                      href={`/api/run/${runId}/data/${filename}`}
                      aria-label={`Open ${filename}`}
                      className="text-blue-600 hover:text-blue-800 text-sm font-medium flex items-center gap-1.5 focus:ring-2 focus:ring-blue-500 focus:outline-none rounded"
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
