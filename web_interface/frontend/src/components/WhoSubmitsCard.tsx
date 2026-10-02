import { Link } from 'react-router-dom'
import type { SubmissionSplit } from '../types'
import { departmentBars, percent, type DepartmentBar } from './whoSubmits'

const ROW = 'grid grid-cols-[minmax(0,110px)_minmax(0,1fr)_92px] items-center gap-3 rounded-md text-[13px]'

function BarRow({ bar }: { bar: DepartmentBar }) {
  const content = (
    <>
      <span className="truncate text-gray-700">{bar.name}</span>
      <span className="flex h-2.5 overflow-hidden rounded-[3px] bg-[#D6CCB6]" aria-hidden="true">
        <span className="h-full bg-[#1F2328]" style={{ width: `${bar.pct}%` }} />
      </span>
      <span className="text-right tabular-nums text-gray-600">
        <strong className="font-semibold text-gray-900">{bar.pct}%</strong> · {bar.own} of {bar.total}
      </span>
    </>
  )
  return bar.href ? (
    <Link
      to={bar.href}
      title="Show these runs submitted on faculty's behalf"
      className={`${ROW} hover:bg-sand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500`}
    >
      {content}
    </Link>
  ) : (
    <div className={ROW} title="No department on record, so there is no Runs filter for it">{content}</div>
  )
}

export default function WhoSubmitsCard({ split }: { split: SubmissionSplit | undefined }) {
  const total = (split?.own_cv ?? 0) + (split?.on_behalf ?? 0)
  const bars = split ? departmentBars(split) : []
  return (
    <section
      className="bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] p-5 min-w-0"
      aria-labelledby="who-submits-heading"
    >
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <h2 id="who-submits-heading" className="text-[15px] font-semibold text-gray-900">Who submits CVs</h2>
        <span className="text-xs text-gray-500">All runs</span>
      </div>
      {total === 0 ? (
        <p className="text-sm text-gray-500">No runs yet</p>
      ) : (
        <>
          <p className="flex items-baseline gap-2.5">
            <span className="text-[30px] leading-none font-semibold tabular-nums text-gray-900">
              {percent(split?.own_cv ?? 0, total)}%
            </span>
            <span className="text-sm text-gray-600">submitted by faculty themselves</span>
          </p>
          <div className="mt-3 flex gap-4 text-xs text-gray-600">
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm bg-[#1F2328]" aria-hidden="true" />Faculty themselves
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm bg-[#D6CCB6]" aria-hidden="true" />On their behalf
            </span>
          </div>
          <div className="mt-3 space-y-2">
            {bars.map((bar) => <BarRow key={bar.name} bar={bar} />)}
          </div>
          <p className="mt-3 text-xs text-gray-500">Click a department to see its runs submitted on faculty&apos;s behalf.</p>
        </>
      )}
    </section>
  )
}
