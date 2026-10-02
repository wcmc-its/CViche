// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import FeedbackForm from './FeedbackForm'
import { getFeedback, getRunFeedbackAll, submitFeedback } from '../api/feedback'
import type { FeedbackDetail } from '../types'
import { QUESTION_LABELS } from './feedbackQuestions'

vi.mock('../api/feedback', () => ({
  getFeedback: vi.fn(),
  submitFeedback: vi.fn(),
  getRunFeedbackAll: vi.fn(),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { role: 'user' } }),
  useCanViewAllRuns: () => false,
}))

const SUBMISSION = {
  id: 1,
  run_id: 'run-1',
  user_id: 7,
  display_name: 'Jane Testperson',
  reviewer_role: 'cv_owner',
  overall_usefulness: 4,
  manual_conversion_effort: '0 minutes',
  correction_effort: '0 minutes',
  summary_generated: 1,
  issue_locations: ['B1'],
  likelihood_to_recommend: 5,
  submitted_at: '2026-09-02T00:00:00+00:00',
} as unknown as FeedbackDetail

const ISSUE_KEYS = [
  'issue_missing_content',
  'issue_split_merged',
  'issue_wrong_section',
  'issue_inaccurate',
  'issue_ai_enrichment',
  'issue_formatting',
]
const LABELS = [
  'Missing content',
  'Split or merged entries',
  'Wrong section',
  'Inaccurate details',
  'PubMed enrichment errors',
  'Formatting',
]

beforeEach(() => {
  vi.mocked(getFeedback).mockResolvedValue({ feedback: null, run_context: { wcm_sections: [] } })
  vi.mocked(getRunFeedbackAll).mockResolvedValue([SUBMISSION])
  vi.mocked(submitFeedback).mockResolvedValue({ status: 201 } as Response)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderForm() {
  render(<FeedbackForm runId="run-1" />)
  await screen.findByText('Problems')
}

const card = (label: string) => screen.getByRole('checkbox', { name: new RegExp(label) }).closest('label') as HTMLElement

describe('FeedbackForm problem cards', () => {
  it('renders the intro line and six cards in order with descriptions', async () => {
    await renderForm()
    expect(
      screen.getByText('Select any that apply and say where, so we can find the entries.'),
    ).toBeTruthy()
    const boxes = screen.getAllByRole('checkbox')
    expect(boxes.map((b) => b.closest('label')?.querySelector('span.font-semibold')?.textContent)).toEqual(LABELS)
    expect(screen.getByText('Wrong citation, journal or PMID added')).toBeTruthy()
    expect(screen.queryByPlaceholderText('Which entries?')).toBeNull()
  })

  it('checking a card reveals the input inside that card; unchecking removes it', async () => {
    await renderForm()
    const label = card('Missing content')
    fireEvent.click(within(label).getByRole('checkbox'))
    const input = within(card('Missing content')).getByPlaceholderText('Which entries?')
    expect(input).toBeTruthy()
    expect(screen.getAllByPlaceholderText('Which entries?')).toHaveLength(1)

    // Clicking the text input must not toggle the card.
    fireEvent.click(input)
    expect(within(card('Missing content')).getByPlaceholderText('Which entries?')).toBeTruthy()

    fireEvent.click(within(card('Missing content')).getByRole('checkbox'))
    expect(screen.queryByPlaceholderText('Which entries?')).toBeNull()
  })

  it('unchecking nulls the field in the submitted payload; keys are unchanged', async () => {
    await renderForm()
    const pick = (name: string, group: string) =>
      fireEvent.click(within(screen.getByRole('radiogroup', { name: group })).getByRole('radio', { name }))
    pick('Department administrator', 'Your role')
    pick('3', 'How useful was the CViche output?')
    pick('0 minutes', QUESTION_LABELS.manual_conversion_effort)
    pick('0 minutes', 'How long did it take to correct the CViche output?')
    pick('4', 'How likely are you to recommend CViche to a colleague?')

    fireEvent.click(within(card('Missing content')).getByRole('checkbox'))
    fireEvent.change(within(card('Missing content')).getByPlaceholderText('Which entries?'), {
      target: { value: 'Section B2' },
    })
    fireEvent.click(within(card('Formatting')).getByRole('checkbox'))
    fireEvent.click(within(card('Formatting')).getByRole('checkbox'))

    fireEvent.click(screen.getByRole('button', { name: 'Submit review' }))
    await waitFor(() => expect(submitFeedback).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(submitFeedback).mock.calls[0][1] as unknown as Record<string, unknown>
    for (const key of ISSUE_KEYS) expect(key in payload).toBe(true)
    expect(payload.issue_missing_content).toBe('Section B2')
    expect(payload.issue_formatting).toBeNull()
    expect(payload.issue_split_merged).toBeNull()
  })
})

describe('FeedbackForm summary', () => {
  it('shows the summary instead of the form when the viewer already reviewed the run', async () => {
    vi.mocked(getFeedback).mockResolvedValue({
      feedback: SUBMISSION,
      run_context: { wcm_sections: [{ section_id: 'B1', section_name: 'Appointments' }] },
    } as unknown as Awaited<ReturnType<typeof getFeedback>>)
    render(<FeedbackForm runId="run-1" />)
    expect(await screen.findByText('Jane Testperson')).toBeTruthy()
    expect(screen.getByText('B1: Appointments')).toBeTruthy()
    expect(screen.getByText('Yes')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Submit review' })).toBeNull()
  })

  it('replaces the form with the summary after a submit', async () => {
    await renderForm()
    const pick = (name: string, group: string) =>
      fireEvent.click(within(screen.getByRole('radiogroup', { name: group })).getByRole('radio', { name }))
    pick('Department administrator', 'Your role')
    pick('3', 'How useful was the CViche output?')
    pick('0 minutes', QUESTION_LABELS.manual_conversion_effort)
    pick('0 minutes', 'How long did it take to correct the CViche output?')
    pick('4', 'How likely are you to recommend CViche to a colleague?')
    fireEvent.click(screen.getByRole('button', { name: 'Submit review' }))
    expect(await screen.findByText('Jane Testperson')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Submit review' })).toBeNull()
  })
})
