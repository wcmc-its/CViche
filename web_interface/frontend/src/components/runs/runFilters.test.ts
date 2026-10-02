// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import type { ReactNode } from 'react'
import { act, renderHook } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import {
  EMPTY_FILTERS, activeStatusPill, applyStatusPill, toListParams, toMemberListParams, useBatchFilter,
  useRunFilters,
} from './runFilters'

function renderAt(url: string, isAdmin = true) {
  const wrapper = ({ children }: { children: ReactNode }) => createElement(MemoryRouter, { initialEntries: [url] }, children)
  return renderHook(() => ({ filters: useRunFilters(isAdmin), batch: useBatchFilter(), search: useLocation().search }), { wrapper })
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

  it('reads and sets the input format in ?input_format=, dropping unknown values', () => {
    const { result } = renderAt('/runs?input_format=wcm')
    expect(result.current.filters.filters.inputFormat).toBe('wcm')
    act(() => result.current.filters.setFilter('inputFormat', 'other'))
    expect(result.current.search).toBe('?input_format=other')
    expect(renderAt('/runs?input_format=bogus').result.current.filters.filters.inputFormat).toBe('')
  })

  it('sends only a valid input format to the API', () => {
    expect(toListParams({ ...EMPTY_FILTERS, inputFormat: 'unknown' }).input_format).toBe('unknown')
    expect(toListParams({ ...EMPTY_FILTERS, inputFormat: 'bogus' }).input_format).toBeUndefined()
  })
})

describe('status pills', () => {
  it('reads and sets the status in ?status=, dropping unknown values', () => {
    const { result } = renderAt('/runs?status=failed')
    expect(result.current.filters.filters.status).toBe('failed')
    act(() => result.current.filters.setFilter('status', 'running'))
    expect(result.current.search).toBe('?status=running')
    expect(renderAt('/runs?status=bogus').result.current.filters.filters.status).toBe('')
  })

  it('sends the status and run_by=on_behalf to the API', () => {
    expect(toListParams({ ...EMPTY_FILTERS, status: 'red', runBy: 'on_behalf' })).toEqual(
      { scope: 'all', status: 'red', run_by: 'on_behalf' })
    expect(toListParams({ ...EMPTY_FILTERS, status: 'bogus' }).status).toBeUndefined()
  })

  it('lights one pill: a status wins, feedback=needed is Awaiting feedback, else All', () => {
    expect(activeStatusPill({ ...EMPTY_FILTERS, status: 'failed', feedback: 'needed' })).toBe('failed')
    expect(activeStatusPill({ ...EMPTY_FILTERS, feedback: 'needed' })).toBe('awaiting_feedback')
    expect(activeStatusPill({ ...EMPTY_FILTERS, feedback: 'given' })).toBe('all')
  })

  it('picking a pill clears the other pills but keeps the other filters', () => {
    const pick = (url: string, pill: Parameters<typeof applyStatusPill>[1]) => {
      const params = new URLSearchParams(url)
      applyStatusPill(params, pill)
      return params.toString()
    }
    expect(pick('department=Medicine&feedback=needed', 'running')).toBe('department=Medicine&status=running')
    expect(pick('status=failed', 'awaiting_feedback')).toBe('feedback=needed')
    expect(pick('status=red&feedback=given', 'all')).toBe('feedback=given')
    expect(pick('feedback=needed&run_by=self', 'all')).toBe('run_by=self')
  })

  it('setStatusPill writes the URL; Clear all removes the status with the other filters', () => {
    const { result } = renderAt('/runs?department=Medicine')
    act(() => result.current.filters.setStatusPill('failed'))
    expect(result.current.search).toBe('?department=Medicine&status=failed')
    act(() => result.current.filters.clearAll())
    expect(result.current.search).toBe('')
  })

  it('members get status and feedback only, and never the red pill', () => {
    const member = renderAt('/runs?status=red&department=Medicine&feedback=needed', false)
    expect(member.result.current.filters.filters).toEqual({ ...EMPTY_FILTERS, feedback: 'needed' })
    act(() => member.result.current.filters.setFilter('department', 'Library'))
    act(() => member.result.current.filters.setStatusPill('red'))
    expect(member.result.current.search).toBe('?status=red&department=Medicine&feedback=needed')
    act(() => member.result.current.filters.setStatusPill('failed'))
    expect(member.result.current.search).toContain('status=failed')
    expect(toMemberListParams({ ...EMPTY_FILTERS, status: 'red', feedback: 'needed' })).toEqual({ feedback: 'needed' })
    expect(toMemberListParams({ ...EMPTY_FILTERS, status: 'running' })).toEqual({ status: 'running' })
  })
})
