// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import RunTable from './RunTable'
import type { RunSummary } from '../../types'

const run = (over: Partial<RunSummary>): RunSummary => ({
  run_id: 'RUNAAA', filename: 'cv.docx', status: 'complete', started_at: '2026-01-02T15:00:00Z', completed_at: null,
  total_cost: null, total_duration_seconds: 100, cv_owner_name: 'Pat Example',
  feedback: { count: 0, given_by_me: false, last_at: null, reviewers: null }, ...over,
})

const onSelectRun = vi.fn()
const onOpenBatch = vi.fn()

function renderTable(runs: RunSummary[]) {
  render(
    <MemoryRouter>
      <RunTable
        groups={runs.map((r) => ({ key: r.run_id, owner: r.cv_owner_name ?? null, latest: r, older: [] }))}
        isAdmin={false}
        showCost={false}
        currentUserId={7}
        sortField="started_at"
        sortDir="desc"
        onSort={vi.fn()}
        onSelectRun={onSelectRun}
        onFilter={vi.fn()}
        onOpenBatch={onOpenBatch}
      />
    </MemoryRouter>,
  )
}

afterEach(() => { cleanup(); vi.resetAllMocks() })

describe('RunTable batch tag', () => {
  it('tags a batch run and opens its batch without opening the run', () => {
    renderTable([run({ batch_id: 'BQXZKD' })])
    fireEvent.click(screen.getByRole('button', { name: 'Batch' }))
    expect(onOpenBatch).toHaveBeenCalledWith('BQXZKD')
    expect(onSelectRun).not.toHaveBeenCalled()
  })

  it('shows no tag on a single upload', () => {
    renderTable([run({ batch_id: null })])
    expect(screen.queryByRole('button', { name: 'Batch' })).toBeNull()
  })
})
