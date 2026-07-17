import { describe, expect, it } from 'vitest';

import { pendingDecisionMapsEqual } from './pendingDecisionCache';

const FAMILY = {
  owner: { id: 11, name: 'Platform Engineer' },
  related: [{ id: 12, name: 'AI Platform Engineer' }],
};

describe('pendingDecisionMapsEqual', () => {
  it('recognizes an unchanged complete decision snapshot', () => {
    const previous = {
      41: { id: 7, decision_type: 'reject', reasoning: 'No fit', role_family: FAMILY },
    };
    expect(pendingDecisionMapsEqual(previous, structuredClone(previous))).toBe(true);
  });

  it('invalidates the cache when the same decision id has a changed role family', () => {
    const previous = {
      41: { id: 7, decision_type: 'reject', reasoning: 'No fit', role_family: FAMILY },
    };
    const next = {
      41: {
        ...previous[41],
        role_family: {
          ...FAMILY,
          related: [...FAMILY.related, { id: 13, name: 'ML Platform Engineer' }],
        },
      },
    };
    expect(pendingDecisionMapsEqual(previous, next)).toBe(false);
  });

  it('invalidates the cache for any changed decision content or application key', () => {
    const previous = { 41: { id: 7, decision_type: 'reject', reasoning: 'No fit' } };
    expect(pendingDecisionMapsEqual(previous, {
      41: { ...previous[41], reasoning: 'Updated evidence' },
    })).toBe(false);
    expect(pendingDecisionMapsEqual(previous, { 42: previous[41] })).toBe(false);
  });
});
