import { Link, useNavigate } from 'react-router-dom'
import { Plus } from 'lucide-react'
import RunHistory from './RunHistory'

/** The run list as its own page, reached from the top bar's Runs tab (#1112). */
export default function RunsPage() {
  const navigate = useNavigate()

  return (
    <main className="max-w-6xl mx-auto px-4 md:px-7 py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-[26px] font-semibold text-gray-900">Runs</h1>
        <Link
          to="/"
          className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          New run
        </Link>
      </div>
      <RunHistory onSelectRun={(runId) => navigate(`/run/${runId}`)} />
    </main>
  )
}
