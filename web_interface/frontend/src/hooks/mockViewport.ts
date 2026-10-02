/** Test helper: make window.matchMedia answer `(max-width: Npx)` queries for a viewport `width` px wide. */
export function mockViewport(width: number): void {
  window.matchMedia = ((query: string) => {
    const max = /max-width:\s*(\d+(?:\.\d+)?)px/.exec(query)
    return {
      matches: max !== null && width <= Number(max[1]),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }
  }) as unknown as typeof window.matchMedia
}

/** Test helper: back to jsdom's default (no matchMedia), which the components read as desktop. */
export function clearViewport(): void {
  delete (window as { matchMedia?: unknown }).matchMedia
}
