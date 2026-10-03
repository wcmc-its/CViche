// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import RunTable from './RunTable'
import type { RunSummary } from '../../types'
import { clearViewport, mockViewport } from '../../hooks/mockViewport'

const run = (over: Partial<RunSummary>): RunSummary => ({
  run_id: 'RUNAAA', filename: 'cv.docx', status: 'complete', started_at: '2026-01-02T15:00:00Z', completed_at: null,
  total_cost: null, total_duration_seconds: 100, cv_owner_name: 'Pat Example',
  feedback: { count: 0, given_by_me: false, last_at: null, reviewers: null }, ...over,
})

const onSelectRun = vi.fn()
const onOpenBatch = vi.fn()

function renderTable(runs: RunSummary[], older: RunSummary[] = [], allRunsView = false) {
  render(
    <MemoryRouter>
      <RunTable
        groups={runs.map((r) => ({ key: r.run_id, owner: r.cv_owner_name ?? null, latest: r, older }))}
        allRunsView={allRunsView}
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

describe('RunTable columns', () => {
  it('heads the date column Started for members and admins', () => {
    renderTable([run({})])
    expect(screen.getByRole('button', { name: 'Started' })).toBeTruthy()
    cleanup()
    renderTable([run({})], [], true)
    expect(screen.getByRole('button', { name: 'Started' })).toBeTruthy()
  })

  it('shows a bare dash, no band dot, for an unscored run, and a dot for a scored one', () => {
    renderTable([run({ run_id: 'RUNBBB', cv_owner_name: 'Quinn Example', quality_score: null })], [], true)
    const cells = (id: string) => within(screen.getByText(id).closest('tr')!)
    expect(cells('Quinn Example').queryByTitle('No score')?.textContent).toBe('—')
    expect(cells('Quinn Example').queryByTitle('No score')?.querySelector('span')).toBeNull()
    cleanup()
    renderTable([run({ quality_score: 88, quality_band: 'GREEN' })], [], true)
    expect(screen.getByTitle(/Green/).querySelector('span[aria-hidden]')).toBeTruthy()
  })

  it('reads No feedback yet the same, in grey text, for members and admins', () => {
    renderTable([run({})])
    expect(screen.getByText('No feedback yet').tagName).toBe('SPAN')
    expect(screen.queryByText('Needs feedback')).toBeNull()
    cleanup()
    renderTable([run({})], [], true)
    expect(screen.getByText('No feedback yet').tagName).toBe('SPAN')
  })
})

afterEach(() => { cleanup(); clearViewport(); vi.resetAllMocks() })

describe('RunTable batch tag', () => {
  it('tags a batch run and opens its batch without opening the run', () => {
    renderTable([run({ batch_id: 'BQXZKD' })])
    fireEvent.click(screen.getByRole('button', { name: 'Batch' }))
    expect(onOpenBatch).toHaveBeenCalledWith('BQXZKD')
    expect(onSelectRun).not.toHaveBeenCalled()
  })

  it('sits beside the filename, not the faculty name', () => {
    renderTable([run({ batch_id: 'BQXZKD', filename: 'alpha_cv.docx' })])
    const line = screen.getByRole('button', { name: 'Batch' }).parentElement!
    expect(within(line).getByText('alpha_cv.docx')).toBeTruthy()
    expect(within(line).queryByText('Pat Example')).toBeNull()
  })

  it('tags an earlier run of a batch too', () => {
    renderTable([run({ batch_id: null })], [run({ run_id: 'RUNOLD', filename: 'old_cv.docx', batch_id: 'BQOLDB' })])
    fireEvent.click(screen.getByRole('button', { name: 'Show 1 earlier run' }))
    fireEvent.click(screen.getByRole('button', { name: 'Batch' }))
    expect(onOpenBatch).toHaveBeenCalledWith('BQOLDB')
  })

  it('shows no tag on a single upload', () => {
    renderTable([run({ batch_id: null })])
    expect(screen.queryByRole('button', { name: 'Batch' })).toBeNull()
  })
})

describe('RunTable phone cards', () => {
  const onFilter = vi.fn()
  const renderCards = (runs: RunSummary[], older: RunSummary[] = [], allRunsView = true) => {
    mockViewport(639)
    render(
      <MemoryRouter>
        <RunTable
          groups={runs.map((r) => ({ key: r.run_id, owner: r.cv_owner_name ?? null, latest: r, older }))}
          allRunsView={allRunsView} showCost={false} currentUserId={7} sortField="started_at" sortDir="desc"
          onSort={vi.fn()} onSelectRun={onSelectRun} onFilter={onFilter} onOpenBatch={onOpenBatch}
        />
      </MemoryRouter>,
    )
  }

  it('keeps the table from 640px up and shows cards below it', () => {
    mockViewport(640)
    renderTable([run({})])
    expect(screen.getByRole('table')).toBeTruthy()
    cleanup()
    renderCards([run({})])
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getAllByRole('link')).toHaveLength(1)
  })

  it('shows name, file, status, score and date on a card, with the full file name on hover', () => {
    renderCards([run({ quality_score: 88, quality_band: 'GREEN', filename: 'a_very_long_synthetic_cv_name.docx' })])
    const card = screen.getByRole('link')
    expect(within(card).getByText('Pat Example')).toBeTruthy()
    expect(within(card).getByTitle('a_very_long_synthetic_cv_name.docx').className).toContain('truncate')
    expect(within(card).getByText('Complete')).toBeTruthy()
    expect(within(card).getByText('88')).toBeTruthy()
    expect(within(card).getByTitle(/Green/).querySelector('span[aria-hidden]')).toBeTruthy()
  })

  it('shows no score for a member, and a bare dash with no dot for an unscored admin run', () => {
    renderCards([run({ quality_score: 88, quality_band: 'GREEN' })], [], false)
    expect(screen.queryByText('88')).toBeNull()
    cleanup()
    renderCards([run({ quality_score: null })])
    expect(screen.getByTitle('No score').textContent).toBe('—')
    expect(screen.getByTitle('No score').querySelector('span')).toBeNull()
  })

  it('opens the run on click and filters on the faculty name without opening it', () => {
    renderCards([run({})])
    fireEvent.click(screen.getByRole('button', { name: 'Pat Example' }))
    expect(onFilter).toHaveBeenCalledWith('faculty', 'Pat Example')
    expect(onSelectRun).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('link'))
    expect(onSelectRun).toHaveBeenCalledWith('RUNAAA')
  })

  it('expands earlier reruns as their own cards', () => {
    renderCards([run({})], [run({ run_id: 'RUNOLD', filename: 'old_cv.docx' })])
    expect(screen.getAllByRole('link')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Show 1 earlier run' }))
    expect(screen.getAllByRole('link')).toHaveLength(2)
    fireEvent.click(screen.getByText('old_cv.docx'))
    expect(onSelectRun).toHaveBeenCalledWith('RUNOLD')
  })

  it('opens the batch from the Batch tag on a card', () => {
    renderCards([run({ batch_id: 'BQXZKD' })])
    fireEvent.click(screen.getByRole('button', { name: 'Batch' }))
    expect(onOpenBatch).toHaveBeenCalledWith('BQXZKD')
    expect(onSelectRun).not.toHaveBeenCalled()
  })
})
