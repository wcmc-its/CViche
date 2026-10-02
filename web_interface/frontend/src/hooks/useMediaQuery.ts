import { useSyncExternalStore } from 'react'

/** Below this width the header nav collapses into a menu. */
export const NARROW_HEADER_QUERY = '(max-width: 479px)'
/** Below this width the Runs filter combos collapse into a "Filters" panel (the combos stop fitting on one row). */
export const NARROW_FILTERS_QUERY = '(max-width: 1023px)'
/** Below this width (Tailwind's sm) the Runs table becomes stacked cards. */
export const PHONE_QUERY = '(max-width: 639px)'

/** True while the viewport matches `query`. False where matchMedia is missing (jsdom), i.e. the desktop layout. */
export function useMediaQuery(query: string): boolean {
  const subscribe = (notify: () => void) => {
    if (typeof window.matchMedia !== 'function') return () => {}
    const list = window.matchMedia(query)
    list.addEventListener('change', notify)
    return () => list.removeEventListener('change', notify)
  }
  const snapshot = () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches
  return useSyncExternalStore(subscribe, snapshot, () => false)
}
