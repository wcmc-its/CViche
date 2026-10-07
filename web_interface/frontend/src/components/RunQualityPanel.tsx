import { useEffect, useState } from 'react'
import { AlertCircle, Info, Lock, XCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { getRunQuality, getRunReviewNote } from '../api/runs'
import type { DoctorFindingGroup, DoctorFindingInstance, DoctorSeverity, QualityDimension, QualityGate, RunDoctorReport, RunQualityReport, ScoreRowWording } from '../types'
import { BAND_STYLE } from './runs/runQuality'

const CARD = 'flex flex-col bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] px-4 py-5 sm:px-6'
const MONO = 'font-mono'
const FULL_SCORE = 100
const PERCENT = 100
/** The mockup collapses INFO findings until asked: they fire on most runs. */
const COLLAPSED_BY_DEFAULT: DoctorSeverity = 'INFO'

const SEVERITIES: DoctorSeverity[] = ['ERROR', 'WARN', 'INFO']

interface SeverityStyle {
  title: string
  /** Pill noun for a count of 1 and for any other count. */
  one: string
  many: string
  pill: string
  text: string
  Icon: LucideIcon
}

const SEVERITY_STYLE: Record<DoctorSeverity, SeverityStyle> = {
  ERROR: { title: 'Errors', one: 'error', many: 'errors', pill: 'bg-error-100 text-error-700', text: 'text-error-700', Icon: XCircle },
  WARN: { title: 'Warnings', one: 'warning', many: 'warnings', pill: 'bg-warning-100 text-amber-700', text: 'text-amber-700', Icon: AlertCircle },
  INFO: { title: 'Info', one: 'info', many: 'info', pill: 'bg-gray-100 text-gray-500', text: 'text-gray-500', Icon: Info },
}

type Load<T> = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; data: T }

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? `${fallback} (${err.message})` : fallback
}

/** How often, and how many times, an empty quality report (or an unscored review
 *  note, #1199) is fetched again. The page turns "complete" when the run row is
 *  committed, but the score and the doctor report are written a few seconds after
 *  that (orchestrator.execute), so a page open at completion asks too early.
 *  5 x 3s covers the ~4s seen on run JCWIFB (2026-10-01) with room to spare. */
export const EMPTY_REPORT_RETRY_MS = 3000
export const EMPTY_REPORT_RETRIES = 5

/** Fetches once per run id, and again (up to EMPTY_REPORT_RETRIES times) while
 *  `isEmpty` says the data isn't there yet. Failures are logged and kept as an
 *  inline message. */
function useLoad<T>(
  load: (() => Promise<T>) | null,
  key: string,
  failure: string,
  isEmpty: (data: T) => boolean = () => false,
): Load<T> {
  const [result, setResult] = useState<Load<T>>({ state: 'loading' })
  useEffect(() => {
    if (!load) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    setResult({ state: 'loading' })
    const attempt = (retriesLeft: number) => {
      load()
        .then((data) => {
          if (cancelled) return
          setResult({ state: 'ready', data })
          if (retriesLeft > 0 && isEmpty(data)) {
            timer = setTimeout(() => attempt(retriesLeft - 1), EMPTY_REPORT_RETRY_MS)
          }
        })
        .catch((err: unknown) => {
          console.error(failure, err)
          if (!cancelled) setResult({ state: 'error', message: errorText(err, failure) })
        })
    }
    attempt(EMPTY_REPORT_RETRIES)
    return () => { cancelled = true; clearTimeout(timer) }
    // `load` is rebuilt every render; the run id is the real dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return result
}

const round1 = (n: number) => Math.round(n * 10) / 10

function SectionHeading({ title, blurb }: { title: string; blurb: string }) {
  return (
    <div>
      <h2 className="m-0 text-base font-semibold text-gray-900">{title}</h2>
      <p className="mt-1 mb-0 max-w-[460px] text-[13px] text-gray-500 [text-wrap:pretty]">{blurb}</p>
    </div>
  )
}

function ScoreBox({ report }: { report: RunQualityReport }) {
  const style = report.band ? BAND_STYLE[report.band] : null
  const border = report.cap != null ? 'border-red-700 bg-error-50' : (style?.box ?? '')
  return (
    <div className={`flex items-center gap-3 rounded-[10px] border-[1.5px] px-4 py-2.5 ${border}`}>
      <span className={`text-[32px] font-bold leading-none tabular-nums ${style?.text ?? ''}`}>{report.score}</span>
      <span className="flex flex-col leading-snug">
        <span className={`font-semibold ${style?.text ?? ''}`}>{style?.label}</span>
        <span className="text-xs text-gray-500">
          {report.band_meaning}{report.provisional ? ' · Provisional' : ''}
        </span>
      </span>
    </div>
  )
}

/** Id of a Run Doctor finding row; the cap banner links to it. */
export const doctorRowId = (lint: string): string => `doctor-lint-${lint}`

/** The cap lint's plain title, from its Run Doctor row; the lint name when the
 *  row is absent or has no wording. */
function capLintTitle(report: RunQualityReport): string | null {
  if (!report.cap_lint) return null
  return report.doctor?.findings.find((f) => f.lint === report.cap_lint)?.title ?? report.cap_lint
}

function CapBanner({ report }: { report: RunQualityReport }) {
  const title = capLintTitle(report)
  return (
    <div className="flex items-start gap-3 rounded-[10px] border border-red-200 bg-error-50 px-3.5 py-3">
      <Lock className="mt-px h-[18px] w-[18px] flex-none text-error-700" aria-hidden="true" />
      <div className="flex min-w-0 flex-col gap-0.5 text-error-800">
        <span className="font-semibold">
          Capped at {report.cap}{report.cap_reason ? `: ${report.cap_reason}` : ''}
        </span>
        <span className="text-[13px] [overflow-wrap:anywhere]">
          {report.earned != null && <>The weighted dimensions alone would score {report.earned}. </>}
          A hard-fail cap overrides that.
          {report.cap_lint && <> See <a href={`#${doctorRowId(report.cap_lint)}`} className="underline">{title}</a> in Run Doctor below.</>}
        </span>
      </div>
    </div>
  )
}

/** A row's plain label with the mock's hover / focus card: what it checks, how
 *  it loses points or caps, what to do, and the technical name. */
/** `weight` is null for a weight-0 row: it carries no points, it only caps. */
function RowLabel({ row, canCap, weight }: { row: ScoreRowWording; canCap: boolean; weight: number | null }) {
  const [open, setOpen] = useState(false)
  const label = row.label ?? row.name
  return (
    <span
      tabIndex={0}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      className="relative flex min-w-0 cursor-help items-center gap-1.5 text-gray-900 outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
    >
      <span className="underline decoration-dotted decoration-sand-500 underline-offset-[3px] [overflow-wrap:anywhere]">{label}</span>
      {canCap && <Lock className="h-3 w-3 flex-none text-gray-400" aria-label="Can cap the score" />}
      {open && (
        <span role="tooltip" className="absolute left-0 top-[calc(100%+8px)] z-30 flex w-[320px] max-w-[calc(100vw-32px)] cursor-default flex-col gap-1.5 rounded-[9px] bg-[#1F2328] px-3.5 py-3 text-[13px] leading-[1.45] text-gray-100 shadow-[0_10px_28px_rgba(0,0,0,.22)]">
          <span className="font-semibold text-white">{label}</span>
          {row.checks && <span>{row.checks}</span>}
          {row.scoring && <span className={canCap ? 'text-red-300' : 'text-gray-300'}>{row.scoring}</span>}
          {row.if_lost && <span className="text-gray-300"><strong className="font-semibold text-white">{weight == null ? 'If it fires:' : 'If points are lost:'}</strong> {row.if_lost}</span>}
          <span className="border-t border-gray-700 pt-1.5 text-xs text-gray-400">Scored as {'\u201c'}{row.name}{'\u201d'} {'\u00b7'} {weight == null ? 'caps only, no points' : `weight ${weight}`}</span>
        </span>
      )}
    </span>
  )
}

const ROW_GRID = 'grid grid-cols-1 items-center gap-x-3.5 gap-y-1 text-[13px] sm:grid-cols-[minmax(0,240px)_minmax(0,1fr)_84px]'

function DimensionRow({ dim, maxWeight, lostClass }: { dim: QualityDimension; maxWeight: number; lostClass: string }) {
  const lost = Math.max(0, dim.weight - dim.points)
  return (
    <div className={ROW_GRID}>
      <RowLabel row={dim} canCap={dim.can_cap} weight={dim.weight} />
      <div
        className="flex h-2.5 justify-end overflow-hidden rounded-[3px] bg-green-300"
        style={{ width: `${(dim.weight / maxWeight) * PERCENT}%` }}
        role="img"
        aria-label={`${round1(dim.points)} of ${dim.weight} points, ${round1(lost)} lost`}
      >
        <div className={`h-full ${lostClass}`} style={{ width: `${(lost / dim.weight) * PERCENT}%` }} />
      </div>
      <span className="text-gray-900 tabular-nums sm:text-right">
        <strong className="font-semibold">{round1(dim.points)}</strong> / {dim.weight}
      </span>
    </div>
  )
}

/** A weight-0 row whose cap fired: no points, so no bar. */
function GateRow({ gate }: { gate: QualityGate }) {
  return (
    <div className={ROW_GRID}>
      <RowLabel row={gate} canCap weight={null} />
      <span className="text-error-700 sm:col-span-2 sm:text-right">
        <a href={`#${doctorRowId(gate.lint)}`} className="font-semibold underline">Caps score at {gate.cap}</a>
      </span>
    </div>
  )
}

/** The footnote is computed from the data: weights are not always a round 100. */
function weightsFootnote(dimensions: QualityDimension[]): string {
  const sum = dimensions.reduce((acc, d) => acc + d.weight, 0)
  const lead = `The ${dimensions.length} weights add up to ${sum}.`
  if (sum >= FULL_SCORE) return `${lead} A run with no penalties scores ${FULL_SCORE}.`
  return `${lead} The other ${FULL_SCORE - sum} points aren't tied to a dimension, so a run with no penalties scores ${FULL_SCORE}.`
}

function DimensionBars({ report }: { report: RunQualityReport }) {
  const dims = report.dimensions
  if (dims.length === 0) return null
  const maxWeight = Math.max(...dims.map((d) => d.weight))
  const earned = round1(dims.reduce((acc, d) => acc + d.points, 0))
  const total = report.total_weight ?? dims.reduce((acc, d) => acc + d.weight, 0)
  const lostClass = report.band ? BAND_STYLE[report.band].dot : 'bg-amber-700'
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-baseline justify-between">
        <h3 className="m-0 text-[13px] font-semibold text-gray-700">What the score checks</h3>
        <span className="text-[13px] text-gray-500 tabular-nums">{earned} / {total}</span>
      </div>
      {dims.map((dim) => <DimensionRow key={dim.name} dim={dim} maxWeight={maxWeight} lostClass={lostClass} />)}
      {report.gates_fired.map((gate) => <GateRow key={gate.name} gate={gate} />)}
      <p className="mt-0.5 mb-0 text-xs text-gray-500">
        {weightsFootnote(dims)} Hover a check to see what it measures and what to do if it loses points.{' '}
        <Lock className="inline h-[11px] w-[11px] align-[-1px] text-gray-400" aria-hidden="true" /> marks checks that can cap the score.
      </p>
    </div>
  )
}

function QualityScoreSection({ report }: { report: RunQualityReport }) {
  const blurb = "Scored automatically from the output. Bands haven't been re-baselined since June, so treat them as provisional. Advisory only; never blocks a run."
  return (
    <section aria-label="Quality score" className={`${CARD} gap-[18px]`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <SectionHeading title="Quality score" blurb={blurb} />
        {report.score != null && <ScoreBox report={report} />}
      </div>
      {report.score == null ? (
        <p className="m-0 text-[13px] text-gray-500">No quality score is stored for this run.</p>
      ) : (
        <>
          {report.cap != null && <CapBanner report={report} />}
          <DimensionBars report={report} />
        </>
      )}
    </section>
  )
}

function InstanceItem({ instance }: { instance: DoctorFindingInstance }) {
  return (
    <li className="flex min-w-0 flex-col gap-1">
      <span className="[overflow-wrap:anywhere]">
        {instance.section && <strong className="font-semibold text-gray-900">{instance.section}: </strong>}
        {instance.detail}
      </span>
      {instance.quotes.map((quote, i) => (
        <q key={i} className="block whitespace-pre-wrap border-l-2 border-sand-300 pl-2.5 text-gray-600 [overflow-wrap:anywhere] before:content-none after:content-none">{quote}</q>
      ))}
      {instance.notes.map((note, i) => (
        <span key={i} className="whitespace-pre-wrap text-xs text-gray-500 [overflow-wrap:anywhere]">{note}</span>
      ))}
    </li>
  )
}

/** Where each instance of a finding is: its CV section, what the doctor saw,
 *  and the text it quotes. Collapsed, so a long list never buries the rows. */
function FindingInstances({ finding }: { finding: DoctorFindingGroup }) {
  const shown = finding.instances.length
  if (shown === 0) return null
  return (
    <details className="mt-1 text-[13px] text-gray-700">
      <summary className="cursor-pointer select-none text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500">
        {shown === 1 ? 'Where' : `Where (${shown})`}
      </summary>
      <ul className="mt-2 mb-0 flex list-none flex-col gap-2.5 pl-0">
        {finding.instances.map((instance, i) => <InstanceItem key={i} instance={instance} />)}
      </ul>
      {finding.count > shown && (
        <p className="mt-2 mb-0 text-xs text-gray-500">Showing the first {shown} of {finding.count}.</p>
      )}
    </details>
  )
}

function FindingRow({ finding, capValue, style }: { finding: DoctorFindingGroup; capValue: number | null; style: SeverityStyle }) {
  const tied = finding.caps_score
  const { Icon } = style
  return (
    <div id={doctorRowId(finding.lint)} className={`grid grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-3 border-t border-l-[3px] border-t-sand-200 px-3.5 py-3 ${tied ? 'border-l-error-700 bg-error-50' : 'border-l-transparent'}`}>
      <Icon className={`mt-0.5 h-4 w-4 flex-none ${style.text}`} aria-hidden="true" />
      <div className="flex min-w-0 flex-col gap-[3px]">
        <div className="flex flex-wrap items-center gap-2">
          {finding.title && <span className="text-sm font-semibold text-gray-900 [overflow-wrap:anywhere]">{finding.title}</span>}
          <span className={finding.title
            ? `${MONO} text-[11px] text-gray-400 [overflow-wrap:anywhere]`
            : `${MONO} text-[13px] font-semibold text-gray-900 [overflow-wrap:anywhere]`}>{finding.lint}</span>
          {tied && (
            <span className="inline-flex items-center gap-1 rounded-full bg-error-100 px-2 py-px text-xs font-semibold text-error-700">
              <Lock className="h-[11px] w-[11px]" aria-hidden="true" />
              {capValue != null ? `Caps score at ${capValue}` : 'Caps score'}
            </span>
          )}
        </div>
        <span className="text-[13px] text-gray-700 [overflow-wrap:anywhere]">{finding.message}</span>
        {finding.what_to_do && (
          <span className="text-[13px] text-gray-700 [overflow-wrap:anywhere]"><strong className="font-semibold">What to do:</strong> {finding.what_to_do}</span>
        )}
        {finding.prevalence != null && (
          <span className="text-xs text-gray-400">Seen on {seenOn(finding.prevalence)} of runs</span>
        )}
        <FindingInstances finding={finding} />
      </div>
      <span className="whitespace-nowrap text-[13px] text-gray-500">{finding.count === 1 ? 'Once' : `${finding.count} times`}</span>
    </div>
  )
}

/** A rate that rounds to 0% (an unobserved lint carries a 0.001 floor) reads
 *  "under 1%", not "0%". */
export const seenOn = (prevalence: number): string => {
  const pct = Math.round(prevalence * PERCENT)
  return pct < 1 ? 'under 1%' : `${pct}%`
}

interface FindingGroupProps {
  severity: DoctorSeverity
  items: DoctorFindingGroup[]
  hidden: boolean
  capValue: number | null
  onToggle: () => void
}

function FindingGroup({ severity, items, hidden, capValue, onToggle }: FindingGroupProps) {
  const style = SEVERITY_STYLE[severity]
  const hint = hidden
    ? `Show ${items.length}${severity === COLLAPSED_BY_DEFAULT ? ' · common on most runs' : ''}`
    : 'Hide'
  return (
    <div className="flex flex-col overflow-hidden rounded-[10px] border border-sand-200">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!hidden}
        className="flex items-center justify-between gap-3 bg-sand-50 px-3.5 py-2.5 text-xs font-semibold uppercase tracking-[0.04em] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        <span className={style.text}>{style.title}</span>
        <span className="text-xs font-medium normal-case tracking-normal text-gray-500">{hint}</span>
      </button>
      {!hidden && items.map((f) => <FindingRow key={f.lint} finding={f} capValue={capValue} style={style} />)}
    </div>
  )
}

function SeverityPills({ counts }: { counts: RunDoctorReport['counts'] }) {
  const pills = SEVERITIES.map((s) => ({ s, n: counts[s.toLowerCase() as keyof RunDoctorReport['counts']] })).filter((p) => p.n > 0)
  return (
    <div className="flex flex-wrap gap-1.5">
      {pills.map(({ s, n }) => (
        <span key={s} className={`rounded-full px-2.5 py-[3px] text-xs font-semibold ${SEVERITY_STYLE[s].pill}`}>
          {n} {n === 1 ? SEVERITY_STYLE[s].one : SEVERITY_STYLE[s].many}
        </span>
      ))}
    </div>
  )
}

function RunDoctorSection({ doctor, capValue }: { doctor: RunDoctorReport | null; capValue: number | null }) {
  const [hidden, setHidden] = useState<Record<DoctorSeverity, boolean>>({ ERROR: false, WARN: false, INFO: true })
  const blurb = 'Specific problems found in this output. Unusual ones are listed first; minor ones most runs have are under Info.'
  const groups = SEVERITIES.map((s) => ({ s, items: (doctor?.findings ?? []).filter((f) => f.severity === s) })).filter((g) => g.items.length > 0)
  return (
    <section aria-label="Run Doctor" className={`${CARD} gap-3.5`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <SectionHeading title="Run Doctor" blurb={blurb} />
        {doctor && <SeverityPills counts={doctor.counts} />}
      </div>
      {!doctor && <p className="m-0 text-[13px] text-gray-500">No Run Doctor report is stored for this run.</p>}
      {doctor && groups.length === 0 && <p className="m-0 text-[13px] text-gray-500">The doctor found nothing to report.</p>}
      {groups.map(({ s, items }) => (
        <FindingGroup
          key={s}
          severity={s}
          items={items}
          hidden={hidden[s]}
          capValue={capValue}
          onToggle={() => setHidden((prev) => ({ ...prev, [s]: !prev[s] }))}
        />
      ))}
      {doctor && doctor.not_run > 0 && (
        <p className="m-0 text-xs text-gray-500">
          {doctor.not_run} {doctor.not_run === 1 ? 'check' : 'checks'} could not run because an input file was missing.
        </p>
      )}
    </section>
  )
}

const QUALITY_FAILURE = 'Could not load the quality score and Run Doctor findings.'

const reportIsEmpty = (report: RunQualityReport) => report.score == null && report.doctor == null

/** Admin only: the Quality score and Run Doctor cards of a finished run. */
export function RunQualitySections({ runId }: { runId: string }) {
  const result = useLoad(() => getRunQuality(runId), runId, QUALITY_FAILURE, reportIsEmpty)
  if (result.state === 'loading') {
    return <p role="status" className="m-0 text-[13px] text-gray-500">Loading quality score...</p>
  }
  if (result.state === 'error') {
    return (
      <p role="alert" className="m-0 rounded-xl border border-red-300 bg-error-50 px-4 py-3 text-[13px] text-error-800">
        {result.message}
      </p>
    )
  }
  const report = result.data
  return (
    <>
      <QualityScoreSection report={report} />
      <RunDoctorSection key={runId} doctor={report.doctor} capValue={report.cap} />
    </>
  )
}

const REVIEW_FAILURE = 'Could not check whether this draft needs review.'

/** Non-admin note for a draft that may need cleanup. Never shows the score. */
export function ReviewNote({ runId }: { runId: string }) {
  const result = useLoad(() => getRunReviewNote(runId), runId, REVIEW_FAILURE, (note) => !note.scored)
  if (result.state === 'error') {
    return <p role="status" className="m-0 text-[13px] text-gray-500">{REVIEW_FAILURE}</p>
  }
  if (result.state !== 'ready' || !result.data.needs_cleanup) return null
  return (
    <div role="note" className="flex items-center gap-2.5 rounded-[9px] border border-amber-200 bg-warning-50 px-3.5 py-2.5 text-[13px] text-amber-900">
      <AlertCircle className="h-4 w-4 flex-none" aria-hidden="true" />
      This draft may need some cleanup before use. Review the entries below.
    </div>
  )
}
