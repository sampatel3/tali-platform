import { describe, expect, it, vi } from 'vitest';

import {
  indexPipelineDecisionSnapshots,
  loadPipelineDecisionSnapshots,
} from './pipelineDecisionSnapshots';

describe('Job Pipeline decision snapshots', () => {
  it('walks every compact page without reintroducing a queue cutoff', async () => {
    const first = Array.from({ length: 500 }, (_, index) => ({
      id: 900 - index,
      created_at: '2026-07-17T08:00:00Z',
    }));
    const second = [
      { id: 400, created_at: '2026-07-17T07:59:59Z' },
      { id: 399, created_at: '2026-07-17T07:59:58Z' },
    ];
    const agentApi = {
      listDecisionExecutionSnapshots: vi.fn()
        .mockResolvedValueOnce({ data: first })
        .mockResolvedValueOnce({ data: second }),
    };

    const response = await loadPipelineDecisionSnapshots(agentApi, 42);

    expect(response.data).toHaveLength(502);
    expect(agentApi.listDecisionExecutionSnapshots).toHaveBeenCalledTimes(2);
    expect(agentApi.listDecisionExecutionSnapshots).toHaveBeenNthCalledWith(1, {
      role_id: 42,
      limit: 500,
    });
    expect(agentApi.listDecisionExecutionSnapshots).toHaveBeenNthCalledWith(2, {
      role_id: 42,
      limit: 500,
      before_created_at: first[499].created_at,
      before_id: first[499].id,
    });
  });

  it('keeps the newest snapshot when legacy data has multiple pending rows', () => {
    const newest = {
      id: 12,
      application_id: 7,
      status: 'pending',
      created_at: '2026-07-17T08:00:00Z',
    };
    const oldest = {
      id: 10,
      application_id: 7,
      status: 'pending',
      created_at: '2026-07-17T07:00:00Z',
    };

    expect(indexPipelineDecisionSnapshots([newest, oldest])[7]).toEqual(newest);
    expect(indexPipelineDecisionSnapshots([oldest, newest])[7]).toEqual(newest);
  });

  it('prefers an in-flight snapshot so a duplicate pending row cannot expose actions', () => {
    const newerPending = {
      id: 22,
      application_id: 9,
      status: 'pending',
      created_at: '2026-07-17T08:00:00Z',
    };
    const inFlight = {
      id: 21,
      application_id: 9,
      status: 'processing',
      created_at: '2026-07-17T07:59:00Z',
    };

    expect(indexPipelineDecisionSnapshots([newerPending, inFlight])[9]).toEqual(inFlight);
  });
});
