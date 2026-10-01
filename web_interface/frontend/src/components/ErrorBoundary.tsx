import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

/**
 * Catches render-time exceptions in its subtree and shows a fallback instead of
 * letting one thrown error blank the whole app. React unmounts the entire tree
 * on an uncaught render error, so without a boundary a single bad field (e.g. a
 * null cost reaching `cost.toFixed`) produces a white screen. Wrap routed views
 * so one broken page degrades to a recoverable message.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Render error caught by ErrorBoundary:', error, info.componentStack)
  }

  render() {
    if (!this.state.hasError) return this.props.children
    if (this.props.fallback) return this.props.fallback

    return (
      <main className="max-w-2xl mx-auto px-4 py-16">
        <div role="alert" className="bg-white border border-red-200 rounded-lg shadow-sm p-6 text-center">
          <AlertTriangle className="w-8 h-8 text-red-500 mx-auto mb-3" aria-hidden="true" />
          <h2 className="text-lg font-semibold text-gray-900 mb-1">
            Something went wrong displaying this page
          </h2>
          <p className="text-sm text-gray-600 mb-4">
            The error has been logged. You can return home and try again. If it keeps happening,
            let the CViche team know which page caused it.
          </p>
          {this.state.error?.message && (
            <pre className="text-xs text-left text-gray-500 bg-gray-50 rounded p-3 mb-4 overflow-x-auto whitespace-pre-wrap">
              {this.state.error.message}
            </pre>
          )}
          <div className="flex items-center justify-center gap-3">
            <a
              href="/"
              className="inline-flex items-center px-4 py-2 rounded-lg text-sm font-medium bg-primary-600 text-white hover:bg-primary-700 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none"
            >
              Back to home
            </a>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="inline-flex items-center px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 text-gray-700 hover:bg-gray-50 focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:outline-none"
            >
              Reload
            </button>
          </div>
        </div>
      </main>
    )
  }
}
