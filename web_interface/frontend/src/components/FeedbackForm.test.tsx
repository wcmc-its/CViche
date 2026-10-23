// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
  vi.mocked(getRunFeedbackAll).mockResolvedValue([])
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
    vi.mocked(getRunFeedbackAll).mockResolvedValue([SUBMISSION])
    render(<FeedbackForm runId="run-1" />)
    expect(await screen.findByText('Jane Testperson')).toBeTruthy()
    expect(screen.getByText('B1: Appointments')).toBeTruthy()
    expect(screen.getByText('Yes')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Submit review' })).toBeNull()
  })

  it('replaces the form with the summary after a submit', async () => {
    await renderForm()
    vi.mocked(getRunFeedbackAll).mockResolvedValue([SUBMISSION])
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
    expect(screen.getAllByRole('region', { name: 'Feedback' })).toHaveLength(1)
  })
})

// Paul's call 2026-10-23 reverses #1196: the owner, admins and staff see
// other reviewers' feedback before reviewing the run themselves.
describe('FeedbackForm with others\' feedback, before the viewer reviews', () => {
  it('shows the form first and the other submissions under it', async () => {
    vi.mocked(getRunFeedbackAll).mockResolvedValue([SUBMISSION])
    await renderForm()
    const others = await screen.findByRole('region', { name: 'Feedback' })
    expect(within(others).getByText('Jane Testperson')).toBeTruthy()
    const form = screen.getByRole('region', { name: 'Review this output' })
    expect(form.compareDocumentPosition(others) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Submit review' })).toBeTruthy()
  })

  it('shows no empty Feedback card under the form when nobody has reviewed the run', async () => {
    await renderForm()
    await waitFor(() => expect(getRunFeedbackAll).toHaveBeenCalledWith('run-1'))
    expect(screen.queryByRole('region', { name: 'Feedback' })).toBeNull()
    expect(screen.queryByLabelText('Loading feedback')).toBeNull()
  })

  it('read-only (staff on a run that is not theirs): the submissions, no form', async () => {
    vi.mocked(getRunFeedbackAll).mockResolvedValue([SUBMISSION])
    render(<FeedbackForm runId="run-1" readOnly />)
    const others = await screen.findByRole('region', { name: 'Feedback' })
    expect(within(others).getByText('Jane Testperson')).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Review this output' })).toBeNull()
  })

  it('read-only with zero submissions renders nothing at all', async () => {
    const { container } = render(<FeedbackForm runId="run-1" readOnly />)
    expect(container.innerHTML).toBe('') // no loading card either
    await waitFor(() => expect(getRunFeedbackAll).toHaveBeenCalledWith('run-1'))
    await act(async () => {})
    expect(container.innerHTML).toBe('')
  })

  it('still reports a failed load rather than hiding it', async () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(getRunFeedbackAll).mockRejectedValue(new Error('boom'))
    render(<FeedbackForm runId="run-1" readOnly />)
    expect((await screen.findByRole('region', { name: 'Feedback' })).textContent).toContain('Unable to load the feedback')
    quiet.mockRestore()
  })
})

// ---------------------------------------------------------------------------
// "Help improve CViche" (#1587) moved beside the run's Download
// (CorrectedUpload.test.tsx); the survey no longer carries it.
// ---------------------------------------------------------------------------

/** The keys today's form sends. */
const TODAYS_PAYLOAD_KEYS = [
  'reviewer_role', 'overall_usefulness', 'overall_accuracy', 'overall_completeness',
  'manual_conversion_effort', 'correction_effort', 'enrichment_quality', 'summary_generated',
  'summary_quality', ...ISSUE_KEYS, 'issue_locations', 'biggest_issue', 'likelihood_to_recommend',
].sort()

function answerRequired() {
  const pick = (name: string, group: string) =>
    fireEvent.click(within(screen.getByRole('radiogroup', { name: group })).getByRole('radio', { name }))
  pick('Department administrator', 'Your role')
  pick('3', 'How useful was the CViche output?')
  pick('0 minutes', QUESTION_LABELS.manual_conversion_effort)
  pick('0 minutes', 'How long did it take to correct the CViche output?')
  pick('4', 'How likely are you to recommend CViche to a colleague?')
}

describe('FeedbackForm without the corrected-copy upload', () => {
  it('has no upload of its own, so the run page shows it once', async () => {
    await renderForm()
    expect(screen.queryByText(/Help improve CViche/)).toBeNull()
    expect(screen.queryByTestId('file-input')).toBeNull()
  })

  it('sends exactly the payload the form sent before', async () => {
    await renderForm()
    answerRequired()
    fireEvent.click(screen.getByRole('button', { name: 'Submit review' }))
    await waitFor(() => expect(submitFeedback).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(submitFeedback).mock.calls[0][1] as unknown as Record<string, unknown>
    expect(Object.keys(payload).sort()).toEqual(TODAYS_PAYLOAD_KEYS)
  })
})
