import { useState, useEffect } from 'react'
import { Loader2, Shield, User as UserIcon, Ban, Check, Pencil, X } from 'lucide-react'

interface AdminUser {
  id: number
  email: string
  display_name: string
  role: string
  status: string
  daily_limit: number | null
  monthly_limit: number | null
  runs_today: number
  total_runs: number
  total_cost: number
  feedback_count: number
  completed_run_count: number
  last_active_at: string | null
  created_at: string | null
}

export default function AdminUsers() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editLimits, setEditLimits] = useState({ daily: '', monthly: '' })
  const [error, setError] = useState<string | null>(null)

  const fetchUsers = async () => {
    try {
      const res = await fetch('/api/admin/users')
      if (res.ok) {
        setUsers(await res.json())
      }
    } catch (err) {
      console.error('Failed to fetch users:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchUsers()
  }, [])

  const toggleStatus = async (user: AdminUser) => {
    setError(null)
    const newStatus = user.status === 'active' ? 'disabled' : 'active'
    try {
      const res = await fetch(`/api/admin/users/${user.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      })
      if (res.ok) {
        const updated = await res.json()
        setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)))
      } else {
        const err = await res.json()
        setError(err.detail?.message || 'Failed to update user status')
      }
    } catch {
      setError('Failed to update user status')
    }
  }

  const startEditLimits = (user: AdminUser) => {
    setEditingId(user.id)
    setEditLimits({
      daily: user.daily_limit?.toString() ?? '',
      monthly: user.monthly_limit?.toString() ?? '',
    })
  }

  const saveLimits = async (user: AdminUser) => {
    setError(null)
    const daily = editLimits.daily === '' ? 0 : parseInt(editLimits.daily, 10)
    const monthly = editLimits.monthly === '' ? 0 : parseInt(editLimits.monthly, 10)

    if (isNaN(daily) || isNaN(monthly)) {
      setError('Limits must be numbers')
      return
    }

    try {
      const res = await fetch(`/api/admin/users/${user.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          daily_limit: daily,
          monthly_limit: monthly,
        }),
      })
      if (res.ok) {
        const updated = await res.json()
        setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)))
        setEditingId(null)
      } else {
        const err = await res.json()
        setError(err.detail?.message || 'Failed to update limits')
      }
    } catch {
      setError('Failed to update limits')
    }
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '--'
    const d = new Date(dateStr)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    )
  }

  return (
    <div>
      {error && (
        <div className="mb-4 p-3 bg-error-50 border border-error-100 rounded-lg text-sm text-error-700">
          {error}
        </div>
      )}

      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  User
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Role
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Runs Today
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Total Cost
                </th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Feedback
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Last Active
                </th>
                <th scope="col" className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th scope="col" className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Limits
                </th>
                <th scope="col" className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {users.map((user) => {
                const feedbackRate =
                  user.completed_run_count > 0
                    ? Math.round((user.feedback_count / user.completed_run_count) * 100)
                    : null

                return (
                  <tr key={user.id} className={user.status === 'disabled' ? 'bg-gray-50 opacity-60' : ''}>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <div className="text-sm font-medium text-gray-900">{user.display_name}</div>
                      <div className="text-xs text-gray-500">{user.email}</div>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      {user.role === 'admin' ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
                          <Shield className="w-3 h-3" aria-hidden="true" />
                          Admin
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
                          <UserIcon className="w-3 h-3" aria-hidden="true" />
                          User
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-right text-sm text-gray-700">
                      {user.runs_today}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-right text-sm text-gray-700">
                      ${user.total_cost.toFixed(2)}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-right text-sm">
                      {feedbackRate !== null ? (
                        <span className={feedbackRate < 50 ? 'text-error-600 font-medium' : 'text-gray-700'}>
                          {user.feedback_count}/{user.completed_run_count}
                          <span className="text-xs text-gray-400 ml-1">({feedbackRate}%)</span>
                        </span>
                      ) : (
                        <span className="text-gray-400">--</span>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                      {formatDate(user.last_active_at)}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-center">
                      {user.status === 'active' ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                          <Check className="w-3 h-3" aria-hidden="true" />
                          Active
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
                          <Ban className="w-3 h-3" aria-hidden="true" />
                          Disabled
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-center text-sm">
                      {editingId === user.id ? (
                        <div className="flex items-center gap-1 justify-center">
                          <label className="sr-only" htmlFor={`daily-${user.id}`}>Daily limit</label>
                          <input
                            id={`daily-${user.id}`}
                            type="number"
                            min="0"
                            placeholder="Daily"
                            value={editLimits.daily}
                            onChange={(e) =>
                              setEditLimits((prev) => ({ ...prev, daily: e.target.value }))
                            }
                            className="w-16 text-xs border border-gray-300 rounded px-1.5 py-1 focus:ring-1 focus:ring-primary-500 focus:border-primary-500 focus:outline-none"
                          />
                          <label className="sr-only" htmlFor={`monthly-${user.id}`}>Monthly limit</label>
                          <input
                            id={`monthly-${user.id}`}
                            type="number"
                            min="0"
                            placeholder="Monthly"
                            value={editLimits.monthly}
                            onChange={(e) =>
                              setEditLimits((prev) => ({ ...prev, monthly: e.target.value }))
                            }
                            className="w-16 text-xs border border-gray-300 rounded px-1.5 py-1 focus:ring-1 focus:ring-primary-500 focus:border-primary-500 focus:outline-none"
                          />
                          <button
                            onClick={() => saveLimits(user)}
                            className="p-1 text-green-600 hover:text-green-800 transition-colors"
                            aria-label="Save limits"
                          >
                            <Check className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => setEditingId(null)}
                            className="p-1 text-gray-400 hover:text-gray-600 transition-colors"
                            aria-label="Cancel editing"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => startEditLimits(user)}
                          className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 transition-colors"
                          aria-label={`Edit limits for ${user.display_name}`}
                        >
                          <Pencil className="w-3 h-3" aria-hidden="true" />
                          {user.daily_limit ?? 'Sys'}/{user.monthly_limit ?? 'Sys'}
                        </button>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-center">
                      <button
                        onClick={() => toggleStatus(user)}
                        className={`text-xs font-medium px-3 py-1 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-offset-1 ${
                          user.status === 'active'
                            ? 'text-red-600 hover:bg-red-50 focus:ring-red-400'
                            : 'text-green-600 hover:bg-green-50 focus:ring-green-400'
                        }`}
                        aria-label={user.status === 'active' ? `Disable ${user.display_name}` : `Enable ${user.display_name}`}
                      >
                        {user.status === 'active' ? 'Disable' : 'Enable'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {users.length === 0 && (
          <div className="text-center py-12 text-gray-500 text-sm">
            No users found.
          </div>
        )}
      </div>
    </div>
  )
}
