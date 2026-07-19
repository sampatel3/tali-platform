import { describe, expect, it, vi } from 'vitest';

import { decisionCursorParams, listAllDecisionPages } from './agentDecisionPagination';

describe('agent decision keyset pagination', () => {
  it('walks every page and preserves stable cursor ordering', async () => {
    const first = Array.from({ length: 200 }, (_, index) => ({
      id: 400 - index,
      created_at: '2026-07-16T12:00:00Z',
    }));
    const second = [
      { id: 200, created_at: '2026-07-16T11:59:59Z' },
      { id: 199, created_at: '2026-07-16T11:59:58Z' },
    ];
    const request = vi.fn()
      .mockResolvedValueOnce({ data: first })
      .mockResolvedValueOnce({ data: second });

    const response = await listAllDecisionPages(request, {
      role_id: 42,
      status: 'pending',
    });

    expect(response.data).toHaveLength(202);
    expect(request).toHaveBeenNthCalledWith(1, {
      role_id: 42,
      status: 'pending',
      limit: 200,
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      role_id: 42,
      status: 'pending',
      limit: 200,
      before_created_at: first[199].created_at,
      before_id: first[199].id,
    });
  });

  it('rejects an unusable row instead of silently looping', () => {
    expect(decisionCursorParams({ id: 1 })).toBeNull();
    expect(decisionCursorParams({ created_at: '2026-07-16T12:00:00Z' })).toBeNull();
  });
});
