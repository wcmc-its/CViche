import type { AdminUser } from '../types'

/** Email domains that count as WCM. Subdomains of these count too. Extend this list to recognise more. */
export const WCM_EMAIL_DOMAINS: readonly string[] = [
  'med.cornell.edu',
  'qatar-med.cornell.edu',
  'weill.cornell.edu',
  'nyp.org',
]

/** True when the address is on a WCM domain (or a subdomain of one). Anything unparseable is not WCM. */
export function isWcmEmail(email: string): boolean {
  const at = email.lastIndexOf('@')
  if (at < 0) return false
  const domain = email.slice(at + 1).trim().toLowerCase()
  return WCM_EMAIL_DOMAINS.some((wcm) => domain === wcm || domain.endsWith(`.${wcm}`))
}

export const SELF_DEMOTION_REASON = "You can't remove your own admin role"
export const LAST_ADMIN_REASON = "The last admin can't be demoted"

/** Why this user's role can't be switched to Member; null when the switch is allowed (or it is a promotion). */
export function demotionBlock(user: AdminUser, users: AdminUser[], currentUserId: number | undefined): string | null {
  if (user.role !== 'admin') return null
  if (user.id === currentUserId) return SELF_DEMOTION_REASON
  const activeAdmins = users.filter((u) => u.role === 'admin' && u.status === 'active').length
  return activeAdmins <= 1 ? LAST_ADMIN_REASON : null
}

/** Allowed-list emails that have not signed in yet (no users-table account), in list order.
 *  Emails compare case-insensitively; the users table's own address may be absent (e.g. SSO without mail). */
export function pendingInvites(allowedUsers: string[], users: Pick<AdminUser, 'email'>[]): string[] {
  const signedIn = new Set(users.map((u) => (u.email ?? '').toLowerCase()))
  return allowedUsers.filter((email) => !signedIn.has(email.toLowerCase()))
}

/** True when ``email`` is in the admin list (case-insensitive). */
export function isListedAdmin(adminUsers: string[], email: string): boolean {
  return adminUsers.some((a) => a.toLowerCase() === email.toLowerCase())
}

/** Why a not-yet-signed-in address can't be switched to Member; null when the switch is allowed. */
export function inviteDemotionBlock(adminUsers: string[], email: string): string | null {
  return isListedAdmin(adminUsers, email) && adminUsers.length <= 1 ? LAST_ADMIN_REASON : null
}
