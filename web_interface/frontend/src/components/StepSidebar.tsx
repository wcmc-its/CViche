import { CheckCircle2, Loader2, XCircle, Circle, ChevronRight } from 'lucide-react'
import { formatCost, runningStepCost } from '../utils'
import { useCanSeeCost } from '../contexts/AuthContext'

interface StepInfo {
  step_number: number
  stage_id?: string
  step_name: string
  status: string
  duration_seconds?: number
  cost: number | null
}

interface StepSidebarProps {
  steps: StepInfo[]
  currentStep: number
  onSelectStep: (stepNumber: number) => void
  stepStartTimes: Record<number, number>
  stepStartCosts: Record<number, number>
  totalCost: number | null
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'complete':
      return <CheckCircle2 className="w-5 h-5 text-green-600 flex-shrink-0" />
    case 'running':
      return <Loader2 className="w-5 h-5 text-blue-600 animate-spin flex-shrink-0" />
    case 'error':
      return <XCircle className="w-5 h-5 text-red-600 flex-shrink-0" />
    default:
      return <Circle className="w-5 h-5 text-gray-400 flex-shrink-0" />
  }
}

export default function StepSidebar({
  steps,
  currentStep,
  onSelectStep,
  stepStartTimes,
  stepStartCosts,
  totalCost,
}: StepSidebarProps) {
  const showCost = useCanSeeCost()
  const formatStepTiming = (step: StepInfo) => {
    if (step.status === 'running' && stepStartTimes[step.step_number]) {
      return `${Math.floor((Date.now() - stepStartTimes[step.step_number]) / 1000)}s`
    }
    if (step.duration_seconds) {
      return `${step.duration_seconds}s`
    }
    return '\u2014'
  }

  const formatStepCost = (step: StepInfo) => {
    if (step.status === 'running') {
      return formatCost(runningStepCost(totalCost, stepStartCosts[step.step_number]), 3)
    }
    return formatCost(step.cost, 3)
  }

  return (
    <aside aria-label="Pipeline steps">
      {/* Desktop sidebar */}
      <div className="hidden md:block w-80 bg-white border-r border-gray-200 min-h-[calc(100vh-73px)]">
        <div className="p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Pipeline Steps</h2>
          <nav role="list" className="space-y-2">
            {steps.map((step) => {
              const isActive = currentStep === step.step_number

              return (
                <div key={step.step_number} role="listitem">
                  <button
                    onClick={() => onSelectStep(step.step_number)}
                    style={{ touchAction: 'manipulation' }}
                    className={`w-full text-left px-3 py-2 rounded-lg transition-colors cursor-pointer focus:ring-2 focus:ring-blue-500 focus:outline-none ${
                      isActive
                        ? 'bg-blue-50 border-2 border-blue-500'
                        : 'border-2 border-transparent hover:bg-gray-50'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <StatusIcon status={step.status} />
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-xs text-gray-900 truncate">
                          <span className="text-blue-600 font-semibold">
                            {step.stage_id || step.step_number}
                          </span>{' '}
                          {step.step_name}
                        </div>
                        <div className="text-xs text-gray-500">
                          {formatStepTiming(step)}{showCost && <> &middot; {formatStepCost(step)}</>}
                        </div>
                      </div>
                      <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
                    </div>
                  </button>
                </div>
              )
            })}
          </nav>
        </div>
      </div>

      {/* Mobile horizontal scroll strip */}
      <div className="block md:hidden border-b border-gray-200 bg-white">
        <div className="px-3 py-2">
          <h2 className="text-xs font-semibold text-gray-500 mb-2">Pipeline Steps</h2>
        </div>
        <nav role="list" className="flex overflow-x-auto gap-2 px-3 pb-3 scrollbar-thin">
          {steps.map((step) => {
            const isActive = currentStep === step.step_number

            return (
              <div key={step.step_number} role="listitem" className="flex-shrink-0">
                <button
                  onClick={() => onSelectStep(step.step_number)}
                  style={{ touchAction: 'manipulation' }}
                  className={`flex items-center gap-1.5 px-3 py-2 rounded-full text-xs font-medium transition-colors cursor-pointer focus:ring-2 focus:ring-blue-500 focus:outline-none whitespace-nowrap ${
                    isActive
                      ? 'bg-blue-50 border-2 border-blue-500 text-blue-800'
                      : 'bg-gray-100 border-2 border-transparent text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  <StatusIcon status={step.status} />
                  <span className="font-semibold">{step.stage_id || step.step_number}</span>
                  {isActive && (
                    <span className="ml-1 text-gray-600 max-w-[120px] truncate">
                      {step.step_name}
                    </span>
                  )}
                </button>

                {/* Expanded info panel below selected pill */}
                {isActive && (
                  <div className="mt-1 px-2 py-1 text-xs text-gray-500 text-center">
                    {formatStepTiming(step)}{showCost && <> &middot; {formatStepCost(step)}</>}
                  </div>
                )}
              </div>
            )
          })}
        </nav>
      </div>
    </aside>
  )
}
