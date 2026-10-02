import { useState, useEffect } from 'react'
import { Loader2, Shield, User as UserIcon, Ban, Check, Pencil, X, AlertTriangle } from 'lucide-react'
import type { AdminUser, SystemConfig } from '../types'
import { getAdminConfig, getAdminUsers, updateAdminConfig, updateAdminUser } from '../api/admin'
import { formatDateShort, formatCost } from '../utils'
import { useAuth } from '../contexts/AuthContext'
import { demotionBlock, inviteDemotionBlock, isListedAdmin, isWcmEmail, pendingInvites } from './adminUserRules'
import AddUserForm from './AddUserForm'
import PendingInviteRow from './PendingInviteRow'

export default function AdminUsers() {
  const currentUserId = useAuth().user?.user_id
  const [users, setUsers] = useState<AdminUser[]>([])
  const [config, setConfig] = useState<SystemConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editLimits, setEditLimits] = useState({ daily: '', monthly: '' })
  const [error, setError] = useState<string | null>(null)

  const fetchUsers = async () => {
    try {
      const [loadedUsers, loadedConfig] = await Promise.all([getAdminUsers(), getAdminConfig()])
      setUsers(loadedUsers)
      setConfig(loadedConfig)
    } catch (err) {
      console.error('Failed to fetch users:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchUsers()
  }, [])

  /** Save allowed/admin list changes, then reload the config so the panel shows what the server holds. */
  const saveLists = async (updates: Partial<SystemConfig>): Promise<boolean> => {
    setError(null)
    try {
      await updateAdminConfig(updates)
      setConfig(await getAdminConfig())
      return true
    } catch (err: any) {
      setError(err.message || 'Failed to save the user list')
      return false
    }
  }

  const addUser = async (email: string): Promise<boolean> => {
    if (!config) return false
    if (config.allowed_users.some((e) => e.toLowerCase() === email)) {
      setError('User already in allowed list.')
      return false
    }
    return saveLists({ allowed_users: [...config.allowed_users, email] })
  }

  const removeInvite = (email: string) => {
    if (!config) return
    if (isListedAdmin(config.admin_users, email)) {
      setError('Remove admin privileges first before removing user.')
      return
    }
    saveLists({ allowed_users: config.allowed_users.filter((e) => e.toLowerCase() !== email.toLowerCase()) })
  }

  const toggleInviteRole = (email: string) => {
    if (!config) return
    const admins = isListedAdmin(config.admin_users, email)
      ? config.admin_users.filter((e) => e.toLowerCase() !== email.toLowerCase())
      : [...config.admin_users, email]
    saveLists({ admin_users: admins })
  }

  const toggleStatus = async (user: AdminUser) => {
    setError(null)
    const newStatus = user.status === 'active' ? 'disabled' : 'active'
    try {
      const updated = await updateAdminUser(user.id, { status: newStatus })
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)))
    } catch (err: any) {
      setError(err.message || 'Failed to update user status')
    }
  }

  const toggleRole = async (user: AdminUser) => {
    setError(null)
    try {
      const updated = await updateAdminUser(user.id, { role: user.role === 'admin' ? 'user' : 'admin' })
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)))
    } catch (err: any) {
      setError(err.message || 'Failed to update role')
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
      const updated = await updateAdminUser(user.id, {
        daily_limit: daily,
        monthly_limit: monthly,
      })
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)))
      setEditingId(null)
    } catch (err: any) {
      setError(err.message || 'Failed to update limits')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    )
  }

  const invites = config ? pendingInvites(config.allowed_users, users) : []

  return (
    <div>
      {error && (
        <div className="mb-4 p-3 bg-error-50 border border-error-100 rounded-lg text-sm text-error-700">
          {error}
        </div>
      )}

      <div className="bg-white rounded-lg shadow-sm border border-sand-300 overflow-hidden">
        <AddUserForm onAdd={addUser} />
        {config && config.auth_mode !== 'simple' && (
          <p className="px-4 py-2 text-xs text-gray-500 border-b border-sand-200">
            This instance signs in through SSO and the directory group, so added addresses only apply to simple (email) login.
          </p>
        )}
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-sand-200">
            <thead className="bg-sand-50">
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
            <tbody className="bg-white divide-y divide-sand-200">
              {users.map((user) => {
                const feedbackRate =
                  user.completed_run_count > 0
                    ? Math.round((user.feedback_count / user.completed_run_count) * 100)
                    : null

                const isAdmin = user.role === 'admin'
                const blocked = demotionBlock(user, users, currentUserId)

                return (
                  <tr key={user.id} className={user.status === 'disabled' ? 'bg-gray-50 opacity-60' : ''}>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <div className="text-sm font-medium text-gray-900">{user.display_name}</div>
                      <div className="flex items-center gap-2 text-xs text-gray-500">
                        {user.email}
                        {!isWcmEmail(user.email) && (
                          <span
                            title="This address is not on a WCM domain"
                            className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-1.5 py-px text-[11px] font-medium text-amber-700"
                          >
                            <AlertTriangle className="w-3 h-3" aria-hidden="true" />
                            Non-WCM
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => toggleRole(user)}
                        disabled={blocked !== null}
                        title={blocked ?? (isAdmin ? 'Switch to Member' : 'Switch to Admin')}
                        aria-label={`${isAdmin ? 'Admin' : user.role === 'staff' ? 'Staff' : 'Member'}: switch ${user.display_name} to ${isAdmin ? 'Member' : 'Admin'}`}
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed ${
                          isAdmin
                            ? 'bg-purple-100 text-purple-700 enabled:hover:bg-purple-200'
                            : 'bg-gray-100 text-gray-600 enabled:hover:bg-gray-200'
                        }`}
                      >
                        {isAdmin ? <Shield className="w-3 h-3" aria-hidden="true" /> : <UserIcon className="w-3 h-3" aria-hidden="true" />}
                        {isAdmin ? 'Admin' : user.role === 'staff' ? 'Staff' : 'Member'}
                      </button>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-right text-sm text-gray-700">
                      {user.runs_today}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-right text-sm text-gray-700">
                      {formatCost(user.total_cost)}
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
                      {formatDateShort(user.last_active_at)}
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
                            className="w-16 text-xs border border-gray-300 rounded px-1.5 py-1 focus-visible:ring-1 focus-visible:ring-primary-500 focus:border-primary-500 focus-visible:outline-none"
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
                            className="w-16 text-xs border border-gray-300 rounded px-1.5 py-1 focus-visible:ring-1 focus-visible:ring-primary-500 focus:border-primary-500 focus-visible:outline-none"
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
                        className={`text-xs font-medium px-3 py-1 rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 ${
                          user.status === 'active'
                            ? 'text-red-600 hover:bg-red-50 focus-visible:ring-red-400'
                            : 'text-green-600 hover:bg-green-50 focus-visible:ring-green-400'
                        }`}
                        aria-label={user.status === 'active' ? `Disable ${user.display_name}` : `Enable ${user.display_name}`}
                      >
                        {user.status === 'active' ? 'Disable' : 'Enable'}
                      </button>
                    </td>
                  </tr>
                )
              })}
              {invites.map((email) => (
                <PendingInviteRow
                  key={email}
                  email={email}
                  isAdmin={isListedAdmin(config?.admin_users ?? [], email)}
                  roleBlock={inviteDemotionBlock(config?.admin_users ?? [], email)}
                  onToggleRole={() => toggleInviteRole(email)}
                  onRemove={() => removeInvite(email)}
                />
              ))}
            </tbody>
          </table>
        </div>

        {users.length === 0 && invites.length === 0 && (
          <div className="text-center py-12 text-gray-500 text-sm">
            No users found.
          </div>
        )}
      </div>
    </div>
  )
}
