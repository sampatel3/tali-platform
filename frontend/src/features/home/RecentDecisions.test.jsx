import { describe, expect, it } from 'vitest';

import { outcomeFor } from './RecentDecisions';

describe('RecentDecisions grounded action labels', () => {
  it('does not present an ordinary-role approval as a completed advance', () => {
    const outcome = outcomeFor({
      status: 'approved',
      decision_type: 'advance_to_interview',
      resolution_effect: { status: 'unknown', action: 'advance' },
    });

    expect(outcome.label).toBe('Advance approved');
    expect(outcome.label).not.toBe('Advanced');
  });

  it('uses completed language only for a confirmed role-matched effect', () => {
    expect(outcomeFor({
      status: 'approved',
      resolution_effect: { status: 'confirmed', action: 'advance', event_id: 7 },
    }).label).toBe('Advanced');
    expect(outcomeFor({
      status: 'approved',
      resolution_effect: { status: 'confirmed', action: 'assessment_send', event_id: 8 },
    }).label).toBe('Assessment sent');
  });

  it('keeps independent related-role failures and queued effects explicit', () => {
    expect(outcomeFor({
      status: 'overridden',
      role_id: 202,
      resolution_effect: { status: 'failed', action: 'reject', event_id: 9 },
    }).label).toBe('Rejection failed');
    expect(outcomeFor({
      status: 'approved',
      role_id: 202,
      resolution_effect: { status: 'pending', action: 'assessment_send' },
    }).label).toBe('Assessment queued');
  });
});
