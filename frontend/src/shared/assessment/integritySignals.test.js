import { describe, expect, it } from 'vitest';

import {
  isIntegritySignalEvent,
  resolveTabSwitchCount,
  summarizeIntegritySignals,
} from './integritySignals';

const timeline = [
  { event_type: 'assessment_started', timestamp: '2026-07-23T10:00:00Z' },
  { event_type: 'copy_attempt', source: 'editor', length: 12, timestamp: '2026-07-23T10:05:00Z' },
  { event_type: 'context_menu_blocked', source: 'editor', timestamp: '2026-07-23T10:06:00Z' },
  { event_type: 'visibility_hidden', source: 'document', timestamp: '2026-07-23T10:07:00Z' },
  { event_type: 'code_execute', tests_passed: 3, tests_total: 4, timestamp: '2026-07-23T10:08:00Z' },
  { event_type: 'fullscreen_exit', source: 'document', timestamp: '2026-07-23T10:09:00Z' },
  { event_type: 'visibility_hidden', source: 'document', timestamp: '2026-07-23T10:11:00Z' },
];

describe('assessment integrity signals', () => {
  it('separates integrity metrics from the candidate work events', () => {
    expect(isIntegritySignalEvent({ event_type: 'visibility_hidden' })).toBe(true);
    expect(isIntegritySignalEvent({ event_type: 'code_execute' })).toBe(false);
    expect(isIntegritySignalEvent({ event_type: 'ai_prompt' })).toBe(false);
    expect(isIntegritySignalEvent(null)).toBe(false);
  });

  it('counts each disclosed group off the real timeline', () => {
    const summary = summarizeIntegritySignals(timeline);
    expect(summary.total).toBe(5);
    expect(summary.hasData).toBe(true);
    expect(summary.groups).toEqual([
      { label: 'clipboard use', count: 1 },
      { label: 'blocked export attempts', count: 1 },
      { label: 'when the tab loses focus or you leave fullscreen', count: 3 },
    ]);
  });

  it('counts only real tab-focus losses, not fullscreen exits', () => {
    const summary = summarizeIntegritySignals(timeline);
    // The timeline holds two visibility_hidden and one fullscreen_exit. Leaving
    // fullscreen is not losing tab focus, so a reviewer must not be shown three.
    expect(summary.tabFocusCount).toBe(2);
    expect(summary.tabFocusTimestamps).toEqual([
      '2026-07-23T10:07:00Z',
      '2026-07-23T10:11:00Z',
    ]);
    // ...but it still counts inside its disclosed group.
    expect(summary.total).toBe(5);
  });

  it('derives the tab-switch count when proctoring left the stored one at zero', () => {
    const summary = summarizeIntegritySignals(timeline);
    // The exact production case: MVP_DISABLE_PROCTORING means the submit
    // payload sends 0, so the card read 0 while the timeline held three events.
    expect(resolveTabSwitchCount({ tab_switch_count: 0 }, summary)).toBe(2);
    expect(resolveTabSwitchCount({}, summary)).toBe(2);
    // A real proctored count still wins — it counts switches, not just hides.
    expect(resolveTabSwitchCount({ tab_switch_count: 9 }, summary)).toBe(9);
  });

  it('degrades quietly on missing or malformed timelines', () => {
    for (const input of [null, undefined, 'nope', [null, 'x', 42]]) {
      const summary = summarizeIntegritySignals(input);
      expect(summary.total).toBe(0);
      expect(summary.hasData).toBe(false);
      expect(summary.tabFocusTimestamps).toEqual([]);
      expect(resolveTabSwitchCount({}, summary)).toBe(0);
    }
  });
});
