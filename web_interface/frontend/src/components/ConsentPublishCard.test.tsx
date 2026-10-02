// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import ConsentPublishCard from './ConsentPublishCard'
import { getConsentPublishPreview, publishConsentVersion } from '../api/admin'

vi.mock('../api/admin', () => ({ getConsentPublishPreview: vi.fn(), publishConsentVersion: vi.fn() }))

const PREVIEW = { current_version: '1.1', next_version: '1.2', users_to_reconsent: 3 }
const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })

beforeEach(() => {
  vi.mocked(getConsentPublishPreview).mockResolvedValue(PREVIEW)
  vi.mocked(publishConsentVersion).mockResolvedValue(PREVIEW)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

const openDialog = async () => {
  fireEvent.click(screen.getByRole('button', { name: /Publish new version/ }))
  await flush()
}

describe('ConsentPublishCard', () => {
  it('has no editable version field, only the current version and the publish button', () => {
    render(<ConsentPublishCard currentVersion="1.1" onPublished={vi.fn()} />)
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByText('1.1')).toBeTruthy()
  })

  it('asks in an in-app dialog, never window.confirm, and shows how many users must agree again', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    render(<ConsentPublishCard currentVersion="1.1" onPublished={vi.fn()} />)
    await openDialog()
    const dialog = screen.getByRole('alertdialog')
    expect(dialog.textContent).toContain('Publish consent version 1.2?')
    expect(dialog.textContent).toContain('3 users')
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(publishConsentVersion).not.toHaveBeenCalled()
  })

  it('says "1 user" for a single user', async () => {
    vi.mocked(getConsentPublishPreview).mockResolvedValue({ ...PREVIEW, users_to_reconsent: 1 })
    render(<ConsentPublishCard currentVersion="1.1" onPublished={vi.fn()} />)
    await openDialog()
    expect(screen.getByRole('alertdialog').textContent).toContain('1 user will')
  })

  it('publishes the version it showed and reports it', async () => {
    const onPublished = vi.fn()
    render(<ConsentPublishCard currentVersion="1.1" onPublished={onPublished} />)
    await openDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Publish 1.2' }))
    await flush()
    expect(publishConsentVersion).toHaveBeenCalledWith('1.2')
    expect(onPublished).toHaveBeenCalledWith('1.2')
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('Cancel and Escape close without publishing', async () => {
    render(<ConsentPublishCard currentVersion="1.1" onPublished={vi.fn()} />)
    await openDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    await openDialog()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(publishConsentVersion).not.toHaveBeenCalled()
  })

  it('keeps the dialog open and shows the error when publishing fails', async () => {
    vi.mocked(publishConsentVersion).mockRejectedValue(new Error('The next consent version is now 1.3'))
    const onPublished = vi.fn()
    render(<ConsentPublishCard currentVersion="1.1" onPublished={onPublished} />)
    await openDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Publish 1.2' }))
    await flush()
    expect(screen.getByRole('alert').textContent).toContain('1.3')
    expect(onPublished).not.toHaveBeenCalled()
  })
})
