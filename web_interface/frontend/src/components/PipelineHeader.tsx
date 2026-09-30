import { ArrowLeft, HelpCircle, Download } from 'lucide-react'
import { Link } from 'react-router-dom'
import { runRoutes } from '../api/routes'
import { formatCost } from '../utils'
import UserMenu from './UserMenu'
import { useCanSeeCost } from '../contexts/AuthContext'

interface PipelineHeaderProps {
  runId: string
  filename: string
  status: string
  totalCost: number | null
  inputTokens: number
  outputTokens: number
  elapsedSeconds: number
  isCancelling: boolean
  onCancel: () => void
  onBack: () => void
}

const formatTotalTime = (seconds: number) => {
  const hours = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours > 0) return `${hours}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

const statusColors: Record<string, string> = {
  complete: 'bg-green-100 text-green-800',
  running: 'bg-blue-100 text-blue-800',
  cancelled: 'bg-orange-100 text-orange-800',
  failed: 'bg-red-100 text-red-800',
}

export default function PipelineHeader({
  runId,
  filename,
  status,
  totalCost,
  inputTokens,
  outputTokens,
  elapsedSeconds,
  isCancelling,
  onCancel,
  onBack,
}: PipelineHeaderProps) {
  const showCost = useCanSeeCost()
  const badgeClass = statusColors[status] || 'bg-gray-100 text-gray-800'

  return (
    <header
      role="banner"
      className="border-b border-gray-200 px-4 py-3 md:px-6"
      style={{
        backgroundImage: 'url(/headerbg.png)',
        backgroundSize: 'cover',
        backgroundPosition: 'center',
      }}
    >
      <div className="flex items-center gap-4">
        {/* Left: Back button + Run ID / filename */}
        <div className="flex items-center gap-3 min-w-0 shrink-0">
          <button
            onClick={onBack}
            aria-label="Back to upload"
            className="shrink-0 rounded p-1 text-gray-700 hover:text-gray-900 focus:ring-2 focus:ring-blue-500 focus:outline-none"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div className="min-w-0">
            <h1 className="text-lg font-bold text-gray-900 truncate">Run #{runId}</h1>
            <a
              href={runRoutes.inputFile(runId)}
              download
              title="Download the original uploaded CV"
              className="group inline-flex items-center gap-1 max-w-full text-sm text-gray-700 hover:text-primary-600 hover:underline"
            >
              <span className="truncate">{filename}</span>
              <Download className="h-3.5 w-3.5 shrink-0 opacity-60 group-hover:opacity-100" />
            </a>
          </div>
        </div>

        {/* Center: Logo — hidden on small screens */}
        <div className="hidden md:flex flex-1 justify-center">
          <img src="/header-logo.png" alt="CViche" className="h-10" />
        </div>

        {/* Spacer when logo is hidden */}
        <div className="flex-1 md:hidden" />

        {/* Right: Metrics + Status + Cancel */}
        <div className="flex items-center gap-3 lg:gap-5 text-sm shrink-0">
          <div>
            <span className="text-gray-700">Time:</span>{' '}
            <span className="font-semibold text-gray-900">{formatTotalTime(elapsedSeconds)}</span>
          </div>

          {showCost && (
            <div>
              <span className="text-gray-700">Cost:</span>{' '}
              <span className="font-semibold text-gray-900">{formatCost(totalCost, 3)}</span>
            </div>
          )}

          {/* Token metrics - hidden below lg */}
          <div className="hidden lg:block">
            <span className="text-gray-700">In:</span>{' '}
            <span className="font-semibold text-gray-900">{inputTokens.toLocaleString()}</span>
          </div>
          <div className="hidden lg:block">
            <span className="text-gray-700">Out:</span>{' '}
            <span className="font-semibold text-gray-900">{outputTokens.toLocaleString()}</span>
          </div>

          {/* Help link */}
          <Link
            to="/help"
            aria-label="Help and support"
            title="Help and support"
            className="text-gray-500 hover:text-primary-600 transition-colors"
          >
            <HelpCircle className="h-5 w-5" />
          </Link>

          {/* Status badge */}
          <span className={`px-3 py-1 rounded-full text-xs font-medium ${badgeClass}`}>
            {status}
          </span>

          {/* Cancel button - only when running */}
          {status === 'running' && (
            <button
              onClick={onCancel}
              disabled={isCancelling}
              aria-label="Cancel pipeline run"
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors focus:ring-2 focus:ring-blue-500 focus:outline-none ${
                isCancelling
                  ? 'bg-gray-200 text-gray-500 cursor-not-allowed'
                  : 'bg-red-100 text-red-800 hover:bg-red-200'
              }`}
            >
              {isCancelling ? 'Cancelling...' : 'Cancel'}
            </button>
          )}

          {/* Account / sign out */}
          <UserMenu />
        </div>
      </div>
    </header>
  )
}
