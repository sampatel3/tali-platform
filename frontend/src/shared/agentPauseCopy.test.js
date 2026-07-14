import { describe, expect, it } from 'vitest';

import { formatAgentPauseStatus, getAgentPauseCopy } from './agentPauseCopy';

describe('agent pause copy', () => {
  it.each([
    'monthly USD cap reached: 5157c >= 5000c',
    'monthly_USD_cap_reached: 5157c >= 5000c',
    'monthly_budget_reached',
    'monthly budget reached',
    'role paused: monthly cap reached: spent 51570000 >= cap 50000000 micro-USD',
    'budget cap reached',
  ])('hides monthly budget implementation details from %s', (reason) => {
    expect(formatAgentPauseStatus(reason)).toBe('Paused · Monthly budget reached');
  });

  it('uses product language for manual, review-limit, and credit pauses', () => {
    expect(formatAgentPauseStatus('paused_by_recruiter')).toBe('Paused by you');
    expect(formatAgentPauseStatus('paused by you')).toBe('Paused by you');
    expect(formatAgentPauseStatus('per-cycle token budget exhausted (12000)'))
      .toBe('Paused · Review limit reached');
    expect(formatAgentPauseStatus('insufficient organization credits: 5 < 20'))
      .toBe('Paused · Workspace credits used up');
  });

  it('never displays an unknown machine reason', () => {
    const copy = getAgentPauseCopy('worker_guard_code_17: role_id=42');

    expect(copy.status).toBe('Paused');
    expect(copy.description).toBe('This agent is paused.');
    expect(JSON.stringify(copy)).not.toMatch(/worker|guard|role_id|42/);
  });
});
