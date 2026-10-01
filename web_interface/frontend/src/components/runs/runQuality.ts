import type { QualityBand, RunSummary } from '../../types'

/** Tailwind classes for one quality band. Colours are the app's existing palette. */
export interface BandStyle {
  label: string
  /** Solid dot / bar colour. */
  dot: string
  text: string
  /** Border + background of the score box. */
  box: string
}

export const BAND_STYLE: Record<QualityBand, BandStyle> = {
  GREEN: { label: 'Green', dot: 'bg-success-700', text: 'text-success-700', box: 'border-green-300 bg-success-50' },
  YELLOW: { label: 'Yellow', dot: 'bg-amber-700', text: 'text-amber-700', box: 'border-amber-300 bg-amber-50' },
  RED: { label: 'Red', dot: 'bg-error-700', text: 'text-error-700', box: 'border-red-300 bg-error-50' },
}

export const NO_SCORE_TEXT = '—'

/** Hover text for a score cell in the runs list (the list does not carry the cap reason). */
export function scoreTitle(run: Pick<RunSummary, 'quality_score' | 'quality_band' | 'quality_cap'>): string {
  if (run.quality_score == null || !run.quality_band) return 'No score'
  if (run.quality_cap != null) return `Capped at ${run.quality_score}. Open the run for the reason.`
  return `${BAND_STYLE[run.quality_band].label} · provisional`
}
