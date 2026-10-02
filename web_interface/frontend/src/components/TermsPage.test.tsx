// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import TermsPage from './TermsPage'
import type { ConsentStatus } from '../types'

// Invented text: the page must show whatever the consent API served, not a copy of its own.
const SERVED: ConsentStatus = {
  text: '# Sample consent\n\nFirst paragraph of the served text.\n\n- A served bullet',
  version: '7.2',
  user_has_consented: true,
  current_hash: 'x',
}
let status: ConsentStatus | null = SERVED
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ consentStatus: status }) }))

afterEach(() => { cleanup(); status = SERVED })

describe('TermsPage', () => {
  it('shows the served consent text and version, read-only', () => {
    render(<TermsPage />)
    expect(screen.getByRole('heading', { name: 'Sample consent' })).toBeTruthy()
    expect(screen.getByText('First paragraph of the served text.')).toBeTruthy()
    expect(screen.getByText('A served bullet')).toBeTruthy()
    expect(screen.getByText(/version 7\.2/)).toBeTruthy()
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('shows a spinner until the text is loaded', () => {
    status = null
    render(<TermsPage />)
    expect(screen.getByLabelText('Loading')).toBeTruthy()
  })
})
