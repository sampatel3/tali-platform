import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  listDecisions: vi.fn(),
}));

vi.mock('../api', () => ({
  agent: { listDecisions: mocks.listDecisions },
}));

import { useRoleDecisionDetails } from './useRoleDecisionDetails';


describe('useRoleDecisionDetails', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.listDecisions.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('refreshes a processing decision until its background action settles', async () => {
    mocks.listDecisions
      .mockResolvedValueOnce({ data: [{ id: 21, status: 'processing' }] })
      .mockResolvedValueOnce({ data: [{ id: 21, status: 'approved' }] });

    const timeline = [{ kind: 'decision', decision_id: 21, status: 'pending' }];
    const { result } = renderHook(() => useRoleDecisionDetails(4, timeline));

    await act(async () => {});
    expect(result.current.byId[21]?.status).toBe('processing');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(mocks.listDecisions).toHaveBeenCalledTimes(2);
    expect(result.current.byId[21]?.status).toBe('approved');
  });
});
