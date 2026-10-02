import { useState, useEffect } from 'react'
import {
  Loader2,
  X,
  Save,
  Download,
} from 'lucide-react'
import type { SystemConfig } from '../types'
import { getAdminConfig, updateAdminConfig } from '../api/admin'
import { adminRoutes } from '../api/routes'
import ConsentPublishCard from './ConsentPublishCard'
import { limitsChanged } from './adminConfigState'

export default function AdminConfig() {
  const [config, setConfig] = useState<SystemConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Local edit state
  const [rateLimitDaily, setRateLimitDaily] = useState('')
  const [rateLimitMonthly, setRateLimitMonthly] = useState('')

  const fetchConfig = async () => {
    try {
      const data = await getAdminConfig()
      setConfig(data)
      setRateLimitDaily(data.rate_limit_daily.toString())
      setRateLimitMonthly(data.rate_limit_monthly.toString())
    } catch (err) {
      console.error('Failed to fetch config:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchConfig()
  }, [])

  const saveConfig = async (updates: Partial<SystemConfig>) => {
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      await updateAdminConfig(updates)
      // Re-fetch to get the updated config from the server
      const data = await getAdminConfig()
      setConfig(data)
      setRateLimitDaily(data.rate_limit_daily.toString())
      setRateLimitMonthly(data.rate_limit_monthly.toString())
      setSuccess('Configuration saved.')
      setTimeout(() => setSuccess(null), 3000)
    } catch (err: any) {
      setError(err.message || 'Failed to save config')
    } finally {
      setSaving(false)
    }
  }

  const saveRateLimits = () => {
    const daily = parseInt(rateLimitDaily, 10)
    const monthly = parseInt(rateLimitMonthly, 10)
    if (isNaN(daily) || daily < 1) {
      setError('Daily rate limit must be a positive number.')
      return
    }
    if (isNaN(monthly) || monthly < 1) {
      setError('Monthly rate limit must be a positive number.')
      return
    }
    saveConfig({ rate_limit_daily: daily, rate_limit_monthly: monthly })
  }

  const onConsentPublished = (version: string) => {
    setConfig((prev) => (prev ? { ...prev, consent_version: version } : prev))
    setSuccess(`Consent version ${version} published.`)
    setTimeout(() => setSuccess(null), 3000)
  }

  const handleExport = (type: string) => {
    window.open(adminRoutes.export(type), '_blank')
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    )
  }

  if (!config) {
    return (
      <div className="text-center py-12 text-gray-500">
        Failed to load configuration.
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="p-3 bg-error-50 border border-error-100 rounded-lg text-sm text-error-700 flex items-center justify-between">
          {error}
          <button onClick={() => setError(null)} className="text-error-600 hover:text-error-800" aria-label="Dismiss error">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}
      {success && (
        <div className="p-3 bg-success-50 border border-success-100 rounded-lg text-sm text-success-700">
          {success}
        </div>
      )}

      {/* Rate Limits */}
      <section className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
        <h3 className="text-sm font-semibold text-gray-900 mb-4">Default Rate Limits</h3>
        <p className="text-xs text-gray-500 mb-4">
          System-wide defaults. Individual user overrides can be set in the Users tab.
        </p>
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <label htmlFor="rate-daily" className="block text-xs font-medium text-gray-600 mb-1">
              Daily Limit
            </label>
            <input
              id="rate-daily"
              type="number"
              min="1"
              value={rateLimitDaily}
              onChange={(e) => setRateLimitDaily(e.target.value)}
              className="w-24 text-sm border border-gray-300 rounded-lg px-3 py-2 focus-visible:ring-1 focus-visible:ring-primary-500 focus:border-primary-500 focus-visible:outline-none"
            />
          </div>
          <div>
            <label htmlFor="rate-monthly" className="block text-xs font-medium text-gray-600 mb-1">
              Monthly Limit
            </label>
            <input
              id="rate-monthly"
              type="number"
              min="1"
              value={rateLimitMonthly}
              onChange={(e) => setRateLimitMonthly(e.target.value)}
              className="w-24 text-sm border border-gray-300 rounded-lg px-3 py-2 focus-visible:ring-1 focus-visible:ring-primary-500 focus:border-primary-500 focus-visible:outline-none"
            />
          </div>
          {limitsChanged(config, rateLimitDaily, rateLimitMonthly) && (
            <button
              onClick={saveRateLimits}
              disabled={saving}
              className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
            >
              {saving ? (
                <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
              ) : (
                <Save className="w-4 h-4" aria-hidden="true" />
              )}
              Save
            </button>
          )}
        </div>
      </section>

      <ConsentPublishCard currentVersion={config.consent_version} onPublished={onConsentPublished} />

      {/* Export Data */}
      <section className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
        <h3 className="text-sm font-semibold text-gray-900 mb-4">Export Data</h3>
        <p className="text-xs text-gray-500 mb-4">
          Download data as CSV files for analysis.
        </p>
        <div className="flex flex-wrap gap-3">
          {(['runs', 'users', 'consent', 'feedback'] as const).map((type) => (
            <button
              key={type}
              onClick={() => handleExport(type)}
              className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
            >
              <Download className="w-4 h-4" aria-hidden="true" />
              {type.charAt(0).toUpperCase() + type.slice(1)}
            </button>
          ))}
        </div>
      </section>
    </div>
  )
}
