import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { FileText, Loader2, ChevronLeft, ChevronRight } from 'lucide-react'
import { getRuns, getRunFilterOptions } from '../api/runs'
import { listBatches } from '../api/batches'
import type { BatchSummary, RunFilterOptions, RunSummary } from '../types'
import ErrorBanner from './ErrorBanner'
import { useAuth, useCanSeeCost } from '../contexts/AuthContext'
import RunTable from './runs/RunTable'
import { ActiveFilterChips, RunFilterCombos } from './runs/RunFilterBar'
import { hasActiveFilters, toListParams, useBatchFilter, useRunFilters } from './runs/runFilters'
import type { RunFilterControls } from './runs/runFilters'
import { BatchFilterCombo, batchLabel } from './runs/BatchFilter'
import BatchView from './runs/BatchView'
import { compareGroupsDir, groupRuns } from './runs/runGroups'
import type { SortDir, SortField } from './runs/runGroups'

interface RunHistoryProps {
  onSelectRun: (runId: string) => void
}

const PAGE_SIZE = 100

function getPageNumbers(currentPage: number, totalPages: number): (number | 'ellipsis')[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i)
  }

  const pages: (number | 'ellipsis')[] = []
  const first = 0
  const last = totalPages - 1

  pages.push(first)

  if (currentPage > 2) {
    pages.push('ellipsis')
  }

  for (let i = Math.max(1, currentPage - 1); i <= Math.min(last - 1, currentPage + 1); i++) {
    if (!pages.includes(i)) {
      pages.push(i)
    }
  }

  if (currentPage < last - 2) {
    pages.push('ellipsis')
  }

  if (!pages.includes(last)) {
    pages.push(last)
  }

  return pages
}

/** Batches the caller may see, for the Batch filter; [] until loaded or when none. */
function useBatches(onError: (message: string) => void): BatchSummary[] {
  const [batches, setBatches] = useState<BatchSummary[]>([])
  useEffect(() => {
    let cancelled = false
    listBatches()
      .then((data) => { if (!cancelled) setBatches(data) })
      .catch((err) => {
        console.error('Error fetching batches:', err)
        if (!cancelled) onError('Unable to load the batch list. Please refresh the page to try again.')
      })
    return () => { cancelled = true }
  }, [])
  return batches
}

interface FilterRowProps {
  isAdmin: boolean
  controls: RunFilterControls
  options: RunFilterOptions | null
  runs: RunSummary[]
  currentUserId: number | undefined
  currentUserEmail: string | undefined
  batches: BatchSummary[]
  batchId: string
  setBatch: (batchId: string) => void
}

/** The filter combos (admin filters, then Batch for anyone with a batch) and the active chips. */
function RunFilterRow({ isAdmin, batches, batchId, setBatch, currentUserEmail, ...bar }: FilterRowProps) {
  const batchCombo = (batches.length > 0 || batchId) && (
    <BatchFilterCombo batches={batches} batchId={batchId} currentUserId={bar.currentUserId} onPick={setBatch} />
  )
  if (!isAdmin && !batchCombo) return null
  const selected = batches.find((b) => b.id === batchId)
  const batchChip = batchId ? { value: selected ? batchLabel(selected) : batchId, onRemove: () => setBatch('') } : undefined
  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-center gap-2">
        {isAdmin
          ? <RunFilterCombos {...bar} currentUserEmail={currentUserEmail} extra={batchCombo} />
          : <div className="flex flex-wrap gap-2 sm:ml-auto">{batchCombo}</div>}
      </div>
      <ActiveFilterChips {...bar} batchChip={batchChip} />
    </div>
  )
}

export default function RunHistory({ onSelectRun }: RunHistoryProps) {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const showCost = useCanSeeCost()
  const controls = useRunFilters(isAdmin)
  const { filters } = controls
  const listParams = useMemo(() => (isAdmin ? toListParams(filters) : undefined), [isAdmin, filters])
  const filterKey = JSON.stringify(listParams ?? {})
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [filterOptions, setFilterOptions] = useState<RunFilterOptions | null>(null)
  const [currentPage, setCurrentPage] = useState(0)
  const [pageFilterKey, setPageFilterKey] = useState(filterKey)
  const [sortField, setSortField] = useState<SortField>('started_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [error, setError] = useState<string | null>(null)
  const containerRef = useRef<HTMLElement>(null)
  const requestSeq = useRef(0)
  const [batchId, setBatch] = useBatchFilter()
  const batches = useBatches(setError)

  // A changed filter restarts paging; done during render so no fetch runs at the stale page.
  if (pageFilterKey !== filterKey) {
    setPageFilterKey(filterKey)
    setCurrentPage(0)
  }

  const fetchRuns = useCallback(async (offset: number) => {
    const seq = ++requestSeq.current
    try {
      setError(null)
      const data = await getRuns(offset, PAGE_SIZE, listParams)
      if (seq !== requestSeq.current) return
      setRuns(data.runs)
      setTotal(data.total || data.runs.length)
    } catch (err) {
      if (seq !== requestSeq.current) return
      console.error('Error fetching runs:', err)
      setError('Unable to load run history. Please refresh the page to try again.')
    }
  }, [listParams])

  useEffect(() => {
    if (!isAdmin) return
    let cancelled = false
    getRunFilterOptions(toListParams(filters))
      .then((data) => { if (!cancelled) setFilterOptions(data) })
      .catch((err) => {
        console.error('Error fetching run filter options:', err)
        if (!cancelled) setError('Unable to load the filter lists. Please refresh the page to try again.')
      })
    return () => { cancelled = true }
  }, [isAdmin, filters])

  useEffect(() => {
    fetchRuns(currentPage * PAGE_SIZE).then(() => {
      setLoading(false)
      if (currentPage > 0) containerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [currentPage, fetchRuns])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDir(field === 'started_at' ? 'desc' : 'asc')
    }
    setCurrentPage(0)
  }

  // Grouping is per loaded page: reruns of the same faculty member that fall on
  // another page are not merged into this page's group.
  const groups = groupRuns(runs).sort((a, b) => compareGroupsDir(a, b, sortField, sortDir, user?.user_id, isAdmin))

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const startIndex = currentPage * PAGE_SIZE
  const endIndex = Math.min(startIndex + PAGE_SIZE, total)

  if (loading) {
    return (
      <div className="text-center py-4 text-sm text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin inline mr-2" aria-hidden="true" />
        Loading history...
      </div>
    )
  }

  const filterBarProps = { controls, options: filterOptions, runs, currentUserId: user?.user_id }

  if (runs.length === 0 && !error && !hasActiveFilters(controls.filters) && !batchId) {
    return (
      <section ref={containerRef} aria-label="Previous runs" className="mt-6 bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] p-6">
        <div className="flex flex-col items-center justify-center py-12">
          <FileText className="w-12 h-12 text-gray-300 mb-3" aria-hidden="true" />
          <p className="text-sm font-semibold text-gray-900">No runs yet</p>
          <p className="text-sm text-gray-500 text-center max-w-[280px] mt-1">Start a new run to process your first CV.</p>
        </div>
      </section>
    )
  }

  return (
    <>
      <RunFilterRow
        {...filterBarProps}
        isAdmin={isAdmin}
        currentUserEmail={user?.email}
        batches={batches}
        batchId={batchId}
        setBatch={setBatch}
      />
      {batchId ? (
        <BatchView batchId={batchId} isAdmin={isAdmin} currentUserId={user?.user_id} onSelectRun={onSelectRun} />
      ) : (
        <section ref={containerRef} aria-label="Previous runs" className="mt-4 bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] overflow-hidden">

          {error && (
            <div className="mb-4">
              <ErrorBanner message={error} onDismiss={() => { setError(null); fetchRuns(currentPage * PAGE_SIZE) }} />
            </div>
          )}

          {runs.length === 0 && !error && (
            <p className="px-5 py-8 text-center text-sm text-gray-500">No runs match these filters.</p>
          )}

          {runs.length > 0 && (
            <>
              <RunTable
                groups={groups}
                isAdmin={isAdmin}
                showCost={showCost}
                currentUserId={user?.user_id}
                sortField={sortField}
                sortDir={sortDir}
                onSort={handleSort}
                onSelectRun={onSelectRun}
                onFilter={controls.setFilter}
                onOpenBatch={setBatch}
              />

              {totalPages > 1 && (
                <div className="flex items-center justify-between px-5 py-3 border-t border-sand-200">
                  <span className="text-sm text-gray-500">
                    Showing {startIndex + 1}{'\u2013'}{endIndex} of {total} runs
                  </span>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setCurrentPage(p => Math.max(0, p - 1))}
                      disabled={currentPage === 0}
                      aria-label="Previous page"
                      className={`p-1 rounded-md ${currentPage === 0 ? 'text-gray-300 cursor-not-allowed' : 'text-gray-600 hover:bg-gray-100'}`}
                    >
                      <ChevronLeft className="w-4 h-4" aria-hidden="true" />
                    </button>
                    {getPageNumbers(currentPage, totalPages).map((page, i) =>
                      page === 'ellipsis' ? (
                        <span key={`ellipsis-${i}`} className="px-2 text-sm text-gray-400">&hellip;</span>
                      ) : (
                        <button
                          key={page}
                          onClick={() => setCurrentPage(page)}
                          className={`min-w-[32px] h-8 px-3 py-1 rounded-md text-sm ${
                            page === currentPage
                              ? 'bg-primary-600 text-white'
                              : 'text-gray-600 hover:bg-gray-100'
                          }`}
                        >
                          {page + 1}
                        </button>
                      )
                    )}
                    <button
                      onClick={() => setCurrentPage(p => Math.min(totalPages - 1, p + 1))}
                      disabled={currentPage === totalPages - 1}
                      aria-label="Next page"
                      className={`p-1 rounded-md ${currentPage === totalPages - 1 ? 'text-gray-300 cursor-not-allowed' : 'text-gray-600 hover:bg-gray-100'}`}
                    >
                      <ChevronRight className="w-4 h-4" aria-hidden="true" />
                    </button>
                  </div>
                </div>
              )}

            </>
          )}
        </section>
      )}
    </>
  )
}
