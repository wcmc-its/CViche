// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import type { ReactNode } from 'react'
import { act, renderHook } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { useBatchFilter, useRunFilters } from './runFilters'

function renderAt(url: string) {
  const wrapper = ({ children }: { children: ReactNode }) => createElement(MemoryRouter, { initialEntries: [url] }, children)
  return renderHook(() => ({ filters: useRunFilters(true), batch: useBatchFilter(), search: useLocation().search }), { wrapper })
}

describe('runFilters URL state', () => {
  it('reads and sets the batch in ?batch=', () => {
    const { result } = renderAt('/runs?batch=BQXZKD')
    expect(result.current.batch[0]).toBe('BQXZKD')
    act(() => result.current.batch[1](''))
    expect(result.current.search).toBe('')
  })

  it('Clear all clears the batch with the admin filters and keeps other params', () => {
    const { result } = renderAt('/runs?department=Medicine&batch=BQXZKD&tab=all')
    act(() => result.current.filters.clearAll())
    expect(result.current.search).toBe('?tab=all')
    expect(result.current.batch[0]).toBe('')
  })
})
