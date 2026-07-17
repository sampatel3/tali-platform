import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./httpClient', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import api from './httpClient';
import { agent } from './agentClient';

const expectedRoleFamilies = {
  31: {
    owner: { id: 31, name: 'Data Platform Lead' },
    related: [{ id: 47, name: 'AI Engineer' }],
  },
};
const expectedDecisionTypes = { 7: 'reject' };

describe('agent decision family expectations', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.get.mockResolvedValue({ data: [] });
    api.post.mockResolvedValue({ data: {} });
  });

  it('requests the server-side audit export as a blob', () => {
    agent.exportDecisions({ format: 'csv', role_id: 31 });

    expect(api.get).toHaveBeenCalledWith('/agent-decisions/export', {
      params: { format: 'csv', role_id: 31 },
      responseType: 'blob',
    });
  });

  it('requests the bounded Job Pipeline decision projection', () => {
    agent.listDecisionExecutionSnapshots({ role_id: 31, limit: 500 });

    expect(api.get).toHaveBeenCalledWith('/agent-decisions/execution-snapshots', {
      params: { role_id: 31, limit: 500 },
    });
  });

  it('adds exact per-role expectations to bulk approval requests', () => {
    agent.bulkApproveDecisions(
      [7],
      null,
      null,
      expectedRoleFamilies,
      expectedDecisionTypes,
    );

    expect(api.post).toHaveBeenCalledWith('/agent-decisions/bulk-approve', {
      decision_ids: [7],
      expected_role_families: expectedRoleFamilies,
      expected_decision_types: expectedDecisionTypes,
      note: null,
      workable_target_stages: null,
    });
  });

  it('sends the displayed type for a non-reject bulk approval', () => {
    agent.bulkApproveDecisions([8], null, null, null, { 8: 'send_assessment' });

    expect(api.post).toHaveBeenCalledWith('/agent-decisions/bulk-approve', {
      decision_ids: [8],
      expected_decision_types: { 8: 'send_assessment' },
      note: null,
      workable_target_stages: null,
    });
  });
});
