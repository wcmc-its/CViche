import { AlertTriangle, Shield, User as UserIcon } from 'lucide-react'
import { isWcmEmail } from './adminUserRules'

interface PendingInviteRowProps {
  email: string
  isAdmin: boolean
  /** Why the role can't be switched to Member; null when it can. */
  roleBlock: string | null
  onToggleRole: () => void
  onRemove: () => void
}

/** A users-table row for an allowed address that has not signed in yet: no stats, no limits. */
export default function PendingInviteRow({ email, isAdmin, roleBlock, onToggleRole, onRemove }: PendingInviteRowProps) {
  return (
    <tr>
      <td className="px-4 py-3 whitespace-nowrap">
        <div className="flex items-center gap-2 text-sm text-gray-900">
          {email}
          {!isWcmEmail(email) && (
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
          onClick={onToggleRole}
          disabled={roleBlock !== null}
          title={roleBlock ?? (isAdmin ? 'Switch to Member' : 'Switch to Admin')}
          aria-label={`${isAdmin ? 'Admin' : 'Member'}: switch ${email} to ${isAdmin ? 'Member' : 'Admin'}`}
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed ${
            isAdmin ? 'bg-purple-100 text-purple-700 enabled:hover:bg-purple-200' : 'bg-gray-100 text-gray-600 enabled:hover:bg-gray-200'
          }`}
        >
          {isAdmin ? <Shield className="w-3 h-3" aria-hidden="true" /> : <UserIcon className="w-3 h-3" aria-hidden="true" />}
          {isAdmin ? 'Admin' : 'Member'}
        </button>
      </td>
      <td colSpan={6} className="px-4 py-3 text-sm text-gray-500">Not signed in yet</td>
      <td className="px-4 py-3 whitespace-nowrap text-center">
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${email} from the allowed list`}
          className="text-xs font-medium px-3 py-1 rounded-lg text-red-600 hover:bg-red-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
        >
          Remove
        </button>
      </td>
    </tr>
  )
}
