import { CheckCircle2, Loader2, XCircle, Circle } from 'lucide-react'

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
}

// The pipeline's stages grouped into the six phases shown to the user.
const PHASE_DEFS: { name: string; stages: string[] }[] = [
  { name: 'Read structure', stages: ['1a', '1b'] },
  { name: 'Split entries', stages: ['2'] },
  { name: 'Classify', stages: ['3a', '3b'] },
  { name: 'Extract fields', stages: ['4', '4.5'] },
  { name: 'Enrich', stages: ['5', '5b', '5c', '5d'] },
  { name: 'Assemble', stages: ['6'] },
]

export interface Phase<T> {
  name: string
  steps: T[]
}

/**
 * Group steps (already in pipeline order) into phases. A step whose stage id is
 * unknown joins the phase of the step before it, or the first phase when it
 * leads, so nothing is ever dropped. Phases with no steps are omitted.
 */
export function groupStepsIntoPhases<T extends { stage_id?: string; step_number: number }>(
  steps: T[],
): Phase<T>[] {
  const buckets: T[][] = PHASE_DEFS.map(() => [])
  let last = 0
  for (const step of steps) {
    const idx = PHASE_DEFS.findIndex((p) => p.stages.includes(step.stage_id ?? String(step.step_number)))
    if (idx >= 0) last = idx
    buckets[last].push(step)
  }
  return PHASE_DEFS.map((p, i) => ({ name: p.name, steps: buckets[i] })).filter((p) => p.steps.length > 0)
}

export function StepStatusIcon({ status, className = 'w-5 h-5' }: { status: string; className?: string }) {
  switch (status) {
    case 'complete':
      return <CheckCircle2 className={`${className} text-green-600 flex-shrink-0`} aria-hidden="true" />
    case 'running':
      return <Loader2 className={`${className} text-primary-600 animate-spin flex-shrink-0`} aria-hidden="true" />
    case 'error':
      return <XCircle className={`${className} text-red-600 flex-shrink-0`} aria-hidden="true" />
    default:
      return <Circle className={`${className} text-gray-300 flex-shrink-0`} aria-hidden="true" />
  }
}

export default function StepSidebar({
  steps,
  currentStep,
  onSelectStep,
  stepStartTimes,
}: StepSidebarProps) {
  const formatStepTiming = (step: StepInfo) => {
    if (step.status === 'running' && stepStartTimes[step.step_number]) {
      return `${Math.floor((Date.now() - stepStartTimes[step.step_number]) / 1000)}s`
    }
    if (step.duration_seconds) {
      return `${step.duration_seconds}s`
    }
    return '—'
  }

  const phases = groupStepsIntoPhases(steps)

  return (
    <aside
      aria-label="Pipeline steps"
      className="bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] p-2.5"
    >
      <nav role="list" className="flex flex-col gap-0.5">
        {phases.map((phase) => (
          <div key={phase.name} role="listitem">
            <div className="px-2.5 pt-2.5 pb-1 text-xs font-semibold uppercase tracking-[0.03em] text-gray-500">
              {phase.name}
            </div>
            <div role="list" className="flex flex-col gap-0.5">
              {phase.steps.map((step) => {
                const isActive = currentStep === step.step_number
                return (
                  <div key={step.step_number} role="listitem">
                    <button
                      onClick={() => onSelectStep(step.step_number)}
                      aria-current={isActive ? 'step' : undefined}
                      style={{ touchAction: 'manipulation' }}
                      className={`w-full grid grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-2.5 text-left px-2.5 py-2 rounded-lg border-[1.5px] transition-colors cursor-pointer focus:ring-2 focus:ring-primary-500 focus:outline-none ${
                        isActive
                          ? 'border-ink bg-primary-50'
                          : 'border-transparent hover:bg-sand-50'
                      }`}
                    >
                      <StepStatusIcon status={step.status} />
                      <span className="min-w-0 flex flex-col">
                        <span className="text-sm font-medium text-gray-900 truncate">{step.step_name}</span>
                        <span className="text-xs text-gray-400 font-mono">{step.stage_id || step.step_number}</span>
                      </span>
                      <span className="text-xs text-gray-500 tabular-nums text-right">
                        {formatStepTiming(step)}
                      </span>
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </nav>
    </aside>
  )
}
