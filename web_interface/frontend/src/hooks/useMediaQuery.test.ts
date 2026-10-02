// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import { NARROW_FILTERS_QUERY, NARROW_HEADER_QUERY, PHONE_QUERY, useMediaQuery } from './useMediaQuery'
import { clearViewport, mockViewport } from './mockViewport'

afterEach(clearViewport)

describe('useMediaQuery', () => {
  it('is false without matchMedia, so jsdom and old browsers get the desktop layout', () => {
    expect(renderHook(() => useMediaQuery(PHONE_QUERY)).result.current).toBe(false)
  })

  it('switches each narrow-screen layout at its own breakpoint', () => {
    const at = (width: number) => {
      mockViewport(width)
      return [NARROW_HEADER_QUERY, PHONE_QUERY, NARROW_FILTERS_QUERY].map((q) => renderHook(() => useMediaQuery(q)).result.current)
    }
    expect(at(479)).toEqual([true, true, true])
    expect(at(480)).toEqual([false, true, true])
    expect(at(639)).toEqual([false, true, true])
    expect(at(640)).toEqual([false, false, true])
    expect(at(1023)).toEqual([false, false, true])
    expect(at(1024)).toEqual([false, false, false])
  })
})
