import { describe, expect, it } from 'vitest'
import { canActOnRun } from './AuthContext'
import type { User } from '../types'

const user = (role: string, user_id = 7): User => ({
  user_id,
  email: `${role}@example.com`,
  display_name: 'Test User',
  role,
  consent_version: '1.0',
  default_submission_type: null,
})

describe('canActOnRun', () => {
  it('lets staff act on their own run', () => {
    expect(canActOnRun(user('staff'), 7)).toBe(true)
  })

  it("hides write controls from staff on another user's run", () => {
    expect(canActOnRun(user('staff'), 8)).toBe(false)
  })

  it('hides write controls from staff when the owner is unknown', () => {
    expect(canActOnRun(user('staff'), null)).toBe(false)
    expect(canActOnRun(user('staff'), undefined)).toBe(false)
  })

  it("lets an admin act on another user's run", () => {
    expect(canActOnRun(user('admin'), 8)).toBe(true)
  })

  it('lets a plain user act on the run they loaded (the API omits run_by for them)', () => {
    expect(canActOnRun(user('user'), undefined)).toBe(true)
  })

  it('denies a signed-out viewer', () => {
    expect(canActOnRun(null, 7)).toBe(false)
  })
})
