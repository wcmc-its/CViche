import { describe, expect, it } from 'vitest'
import { clockTime, formatLogLine, mergeLogLines } from './logLines'

describe('log lines', () => {
  it('formats a WebSocket timestamp the way the polled logs are formatted', () => {
    expect(formatLogLine(clockTime('2026-10-02T14:03:09.123456'), 'Reading')).toBe('[14:03:09] Reading')
    expect(formatLogLine(clockTime(undefined), 'Reading')).toBe('Reading')
  })

  it('prints a line once when the WebSocket and the poll both deliver it', () => {
    const fromWs = [formatLogLine(clockTime('2026-10-02T14:03:09.5'), 'Reading')]
    const fromPoll = ['[14:03:09] Reading', '[14:03:10] Parsing']
    expect(mergeLogLines(fromWs, fromPoll)).toEqual(['[14:03:09] Reading', '[14:03:10] Parsing'])
  })

  it('keeps a same-message line at a different time, and drops repeats inside one batch', () => {
    expect(mergeLogLines(['[14:03:09] Retry'], ['[14:03:11] Retry', '[14:03:11] Retry'])).toEqual([
      '[14:03:09] Retry',
      '[14:03:11] Retry',
    ])
  })

  it('returns the same array when nothing is new', () => {
    const lines = ['[14:03:09] Reading']
    expect(mergeLogLines(lines, ['[14:03:09] Reading'])).toBe(lines)
  })
})
