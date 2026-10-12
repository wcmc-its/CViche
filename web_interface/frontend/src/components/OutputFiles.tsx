import { FileText, Download } from 'lucide-react'
import { runRoutes } from '../api/routes'
import { useCanViewAllRuns } from '../contexts/AuthContext'

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

/** Label of the review copy's download beside the main one (#1589). */
export const REVIEW_COPY_LABEL = "Review copy (with CViche's notes)"

/** Sand panel with a DOCX tile, the file name and a primary Download button,
 *  plus the review copy (#1591) beside it when the run has one. */
export function DocxDownloadCard({ runId, filename, reviewFilename = null }: { runId: string; filename: string; reviewFilename?: string | null }) {
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
      <div className="flex flex-wrap items-center gap-2.5">
        {reviewFilename && (
          <a
            href={runRoutes.dataFile(runId, reviewFilename)}
            download
            title={reviewFilename}
            className="inline-flex items-center gap-2 px-4 py-2.5 bg-white border border-sand-400 hover:bg-sand-50 text-gray-900 font-medium rounded-lg transition-colors focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 focus-visible:outline-none"
          >
            <Download className="w-4 h-4" aria-hidden="true" />
            <span>{REVIEW_COPY_LABEL}</span>
          </a>
        )}
        <a
          href={runRoutes.dataFile(runId, filename)}
          download
          aria-label={`Download final output file ${filename}`}
          className="inline-flex items-center gap-2 px-5 py-2.5 bg-primary-600 hover:bg-primary-700 text-white font-semibold rounded-lg transition-colors focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 focus-visible:outline-none"
        >
          <Download className="w-4 h-4" aria-hidden="true" />
          <span>Download</span>
        </a>
      </div>
    </div>
  )
}

// Stage JSON files are internal pipeline artifacts -- admin and staff only (the
// backend enforces this too). Hide them entirely from everyone else; the final
// .docx, its review copy (#1591) and other outputs stay visible to the run owner.
const isJsonName = (f: string) => (f.split('/').pop() || f).endsWith('.json')
/** artifact_service.REVIEW_DOCX_SUFFIX */
export const isReviewDocx = (f: string) => f.endsWith('_wcm_review.docx')
/** The finished document, never its review copy. */
export const isFinalDocx = (f: string) => f.endsWith('.docx') && !isReviewDocx(f)

const baseName = (f: string) => f.split('/').pop() || f

/** A finished run's document and its review copy (null when none was
 *  written), by base name: the latest step that lists a final .docx. */
export function finalDocuments(steps: Pick<OutputFilesProps['step'], 'output_files'>[]): { docx: string; review: string | null } | null {
  for (let i = steps.length - 1; i >= 0; i--) {
    const files = visibleOutputFiles(steps[i], false)
    const docx = files.find(isFinalDocx)
    if (!docx) continue
    const review = files.find(isReviewDocx)
    return { docx: baseName(docx), review: review ? baseName(review) : null }
  }
  return null
}

/** The step's output files this user will actually see listed. */
export function visibleOutputFiles(step: Pick<OutputFilesProps['step'], 'output_files'>, canSeeStageJson: boolean): string[] {
  let files: string[] = []
  try {
    files = step.output_files ? JSON.parse(step.output_files) : []
  } catch {
    files = []
  }
  return files.filter(f => canSeeStageJson || !isJsonName(f))
}

export default function OutputFiles({ runId, step, onOpenJson, showFinalOutput = true }: OutputFilesProps) {
  const outputFiles = visibleOutputFiles(step, useCanViewAllRuns())

  if (step.status !== 'complete' || outputFiles.length === 0) {
    return null
  }

  const isFinalStep = step.stage_id === '6'
  const docxFile = outputFiles.find(isFinalDocx)

  // For the final step, exclude the docx from the additional files list
  // since it's already shown prominently in the Final Output section
  const additionalFiles = isFinalStep && docxFile
    ? outputFiles.filter(f => f !== docxFile)
    : outputFiles

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
                        className="text-blue-600 hover:text-blue-800 min-w-0 text-left text-sm font-medium flex items-center gap-1.5 [overflow-wrap:anywhere] focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none rounded"
                      >
                        <FileText className="w-4 h-4" aria-hidden="true" />
                        {filename}
                      </button>
                      <a
                        href={runRoutes.dataFile(runId, filename)}
                        download
                        aria-label={`Download ${filename}`}
                        className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none rounded"
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
                      className="text-blue-600 hover:text-blue-800 min-w-0 text-left text-sm font-medium flex items-center gap-1.5 [overflow-wrap:anywhere] focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none rounded"
                    >
                      <FileText className="w-4 h-4" aria-hidden="true" />
                      {filename}
                    </a>
                  ) : (
                    <a
                      href={runRoutes.dataFile(runId, filename)}
                      aria-label={`Open ${filename}`}
                      className="text-blue-600 hover:text-blue-800 min-w-0 text-left text-sm font-medium flex items-center gap-1.5 [overflow-wrap:anywhere] focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none rounded"
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
