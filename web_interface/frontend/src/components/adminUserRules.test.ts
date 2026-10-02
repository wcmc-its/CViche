import { describe, expect, it } from 'vitest'
import { LAST_ADMIN_REASON, SELF_DEMOTION_REASON, demotionBlock, inviteDemotionBlock, isListedAdmin, isWcmEmail, pendingInvites } from './adminUserRules'
import type { AdminUser } from '../types'

const user = (id: number, role: string, status = 'active'): AdminUser => ({
  id, email: `u${id}@example.com`, display_name: `User ${id}`, role, status, daily_limit: null, monthly_limit: null,
  runs_today: 0, total_runs: 0, total_cost: 0, feedback_count: 0, completed_run_count: 0, last_active_at: null, created_at: null,
})

describe('isWcmEmail', () => {
  it('accepts the WCM domains and their subdomains, any case', () => {
    for (const e of ['a@med.cornell.edu', 'a@Qatar-Med.Cornell.edu', 'a@weill.cornell.edu', 'a@nyp.org', 'a@dept.med.cornell.edu']) {
      expect(isWcmEmail(e)).toBe(true)
    }
  })

  it('rejects other domains, look-alikes and non-addresses', () => {
    for (const e of ['a@gmail.com', 'a@cornell.edu', 'a@notmed.cornell.edu.evil.com', 'a@xnyp.org', 'a@evilmed.cornell.edu', 'nope', '']) {
      expect(isWcmEmail(e)).toBe(false)
    }
  })
})

describe('demotionBlock', () => {
  const users = [user(1, 'admin'), user(2, 'admin'), user(3, 'user')]

  it('refuses self-demotion even when another admin exists', () => {
    expect(demotionBlock(users[0], users, 1)).toBe(SELF_DEMOTION_REASON)
  })

  it('refuses demoting the last active admin, but not one of two', () => {
    expect(demotionBlock(users[1], users, 1)).toBeNull()
    expect(demotionBlock(users[1], [user(1, 'admin', 'disabled'), users[1]], 9)).toBe(LAST_ADMIN_REASON)
  })

  it('never blocks a promotion', () => {
    expect(demotionBlock(users[2], users, 3)).toBeNull()
  })
})

describe('pendingInvites', () => {
  it('lists allowed addresses with no account, ignoring case, in list order', () => {
    const users = [user(1, 'admin'), user(2, 'user')]
    const allowed = ['U2@Example.com', 'new@example.com', 'u1@example.com', 'later@example.com']
    expect(pendingInvites(allowed, users)).toEqual(['new@example.com', 'later@example.com'])
  })

  it('treats every address as pending when nobody has signed in', () => {
    expect(pendingInvites(['a@example.com'], [])).toEqual(['a@example.com'])
  })
})

describe('invite roles', () => {
  it('finds an admin-listed address regardless of case', () => {
    expect(isListedAdmin(['Boss@Example.com'], 'boss@example.com')).toBe(true)
    expect(isListedAdmin(['boss@example.com'], 'other@example.com')).toBe(false)
  })

  it('blocks demoting the only listed admin, not one of several or a plain member', () => {
    expect(inviteDemotionBlock(['boss@example.com'], 'boss@example.com')).toBe(LAST_ADMIN_REASON)
    expect(inviteDemotionBlock(['boss@example.com', 'b@example.com'], 'boss@example.com')).toBeNull()
    expect(inviteDemotionBlock(['boss@example.com'], 'member@example.com')).toBeNull()
  })
})
