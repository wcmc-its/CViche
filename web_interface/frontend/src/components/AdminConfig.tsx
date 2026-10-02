import { useState, useEffect, FormEvent } from 'react'
import {
  Loader2,
  X,
  Shield,
  Save,
  Download,
  UserPlus,
  ShieldCheck,
  ShieldOff,
} from 'lucide-react'
import type { SystemConfig } from '../types'
import { getAdminConfig, updateAdminConfig } from '../api/admin'
import { adminRoutes } from '../api/routes'
import { isWcmEmail } from './adminUserRules'
import { limitsChanged } from './adminConfigState'

export default function AdminConfig() {
  const [config, setConfig] = useState<SystemConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Local edit state
  const [newUserEmail, setNewUserEmail] = useState('')
  const [rateLimitDaily, setRateLimitDaily] = useState('')
  const [rateLimitMonthly, setRateLimitMonthly] = useState('')
  const [consentVersion, setConsentVersion] = useState('')

  const fetchConfig = async () => {
    try {
      const data = await getAdminConfig()
      setConfig(data)
      setRateLimitDaily(data.rate_limit_daily.toString())
      setRateLimitMonthly(data.rate_limit_monthly.toString())
      setConsentVersion(data.consent_version)
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
      setConsentVersion(data.consent_version)
      setSuccess('Configuration saved.')
      setTimeout(() => setSuccess(null), 3000)
    } catch (err: any) {
      setError(err.message || 'Failed to save config')
    } finally {
      setSaving(false)
    }
  }

  const addUser = (e: FormEvent) => {
    e.preventDefault()
    if (!newUserEmail.trim() || !config) return
    const email = newUserEmail.trim().toLowerCase()
    if (config.allowed_users.includes(email)) {
      setError('User already in allowed list.')
      return
    }
    saveConfig({ allowed_users: [...config.allowed_users, email] })
    setNewUserEmail('')
  }

  const removeUser = (email: string) => {
    if (!config) return
    // Check if they're an admin
    if (config.admin_users.map((e) => e.toLowerCase()).includes(email.toLowerCase())) {
      setError('Remove admin privileges first before removing user.')
      return
    }
    const updated = config.allowed_users.filter(
      (e) => e.toLowerCase() !== email.toLowerCase()
    )
    saveConfig({ allowed_users: updated })
  }

  const promoteToAdmin = (email: string) => {
    if (!config) return
    if (config.admin_users.map((e) => e.toLowerCase()).includes(email.toLowerCase())) return
    saveConfig({ admin_users: [...config.admin_users, email] })
  }

  const demoteFromAdmin = (email: string) => {
    if (!config) return
    if (config.admin_users.length <= 1) {
      setError('Cannot remove the last admin.')
      return
    }
    const updated = config.admin_users.filter(
      (e) => e.toLowerCase() !== email.toLowerCase()
    )
    saveConfig({ admin_users: updated })
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

  const saveConsentVersion = () => {
    if (!consentVersion.trim()) {
      setError('Consent version cannot be empty.')
      return
    }
    saveConfig({ consent_version: consentVersion.trim() })
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

      {/* Allowed Users */}
      <section className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
        <h3 className="text-sm font-semibold text-gray-900 mb-4">Allowed Users</h3>
        <div className="space-y-2 mb-4">
          {config.allowed_users.map((email) => {
            const isAdmin = config.admin_users
              .map((e) => e.toLowerCase())
              .includes(email.toLowerCase())

            return (
              <div
                key={email}
                className="flex items-center justify-between py-2 px-3 rounded-lg bg-gray-50 group"
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-700">{email}</span>
                  {!isWcmEmail(email) && (
                    <span
                      title="This address is not on a WCM domain"
                      className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-1.5 py-px text-[11px] font-medium text-amber-700"
                    >
                      Non-WCM
                    </span>
                  )}
                  {isAdmin && (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-100 text-purple-700">
                      <Shield className="w-2.5 h-2.5" aria-hidden="true" />
                      Admin
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  {isAdmin ? (
                    <button
                      onClick={() => demoteFromAdmin(email)}
                      className="p-1 text-purple-500 hover:text-purple-700 transition-colors"
                      title="Remove admin privileges"
                      aria-label={`Remove admin privileges from ${email}`}
                    >
                      <ShieldOff className="w-3.5 h-3.5" />
                    </button>
                  ) : (
                    <button
                      onClick={() => promoteToAdmin(email)}
                      className="p-1 text-gray-400 hover:text-purple-600 transition-colors"
                      title="Promote to admin"
                      aria-label={`Promote ${email} to admin`}
                    >
                      <ShieldCheck className="w-3.5 h-3.5" />
                    </button>
                  )}
                  <button
                    onClick={() => removeUser(email)}
                    className="p-1 text-gray-400 hover:text-red-600 transition-colors"
                    title="Remove user"
                    aria-label={`Remove ${email} from allowed users`}
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
        <form onSubmit={addUser} className="flex gap-2">
          <label className="sr-only" htmlFor="new-user-email">New user email</label>
          <input
            id="new-user-email"
            type="email"
            placeholder="user@med.cornell.edu"
            value={newUserEmail}
            onChange={(e) => setNewUserEmail(e.target.value)}
            className="flex-1 text-sm border border-gray-300 rounded-lg px-3 py-2 focus-visible:ring-1 focus-visible:ring-primary-500 focus:border-primary-500 focus-visible:outline-none"
          />
          <button
            type="submit"
            disabled={!newUserEmail.trim() || saving}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          >
            {saving ? (
              <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
            ) : (
              <UserPlus className="w-4 h-4" aria-hidden="true" />
            )}
            Add
          </button>
        </form>
      </section>

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

      {/* Consent Version */}
      <section className="bg-white rounded-lg shadow-sm border border-sand-300 p-6">
        <h3 className="text-sm font-semibold text-gray-900 mb-4">Consent Version</h3>
        <p className="text-xs text-gray-500 mb-4">
          Bumping the version will require all users to re-consent on their next visit.
        </p>
        <div className="flex gap-4 items-end">
          <div>
            <label htmlFor="consent-version" className="block text-xs font-medium text-gray-600 mb-1">
              Current Version
            </label>
            <input
              id="consent-version"
              type="text"
              value={consentVersion}
              onChange={(e) => setConsentVersion(e.target.value)}
              className="w-32 text-sm border border-gray-300 rounded-lg px-3 py-2 focus-visible:ring-1 focus-visible:ring-primary-500 focus:border-primary-500 focus-visible:outline-none"
            />
          </div>
          <button
            onClick={saveConsentVersion}
            disabled={saving || consentVersion === config.consent_version}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          >
            {saving ? (
              <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="w-4 h-4" aria-hidden="true" />
            )}
            Update
          </button>
        </div>
      </section>

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
