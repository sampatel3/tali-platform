import { beforeEach, describe, expect, it, vi } from 'vitest';

const { post } = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock('./httpClient', () => ({
  default: { post },
}));

import { agent } from './agentClient';
import {
  captureCacheGeneration,
  clearCache,
  isCacheGenerationCurrent,
  readCache,
  writeCache,
} from './resourceCache';

describe('agent approval cache invalidation', () => {
  beforeEach(() => {
    clearCache();
    post.mockReset().mockResolvedValue({ data: {} });
  });

  it.each([
    [
      'single approval',
      () => agent.approveDecision(42, { note: 'Reviewed' }, { force: true }),
      '/agent-decisions/42/approve?force=true',
      { note: 'Reviewed' },
    ],
    [
      'bulk approval',
      () => agent.bulkApproveDecisions([42, 43], null, { 31: 'interview' }),
      '/agent-decisions/bulk-approve',
      {
        decision_ids: [42, 43],
        note: null,
        workable_target_stages: { 31: 'interview' },
      },
    ],
  ])('drops every Home decision scope around %s', async (_label, approve, url, body) => {
    let settlePost;
    post.mockImplementationOnce(() => new Promise((resolve) => { settlePost = resolve; }));
    writeCache('home:decisions:{"status":"pending"}', { stale: 'pending' });
    writeCache('home:decisions:{"status":"all"}', { stale: 'all' });
    writeCache('home:roles-breakdown', [{ role_id: 31 }]);
    const beforeMutation = captureCacheGeneration('home:decisions:');

    const request = approve();

    // Mutation start clears every old filter scope immediately.
    expect(readCache('home:decisions:{"status":"pending"}')).toBeNull();
    expect(readCache('home:decisions:{"status":"all"}')).toBeNull();
    expect(isCacheGenerationCurrent(beforeMutation)).toBe(false);
    const duringMutation = captureCacheGeneration('home:decisions:');

    // A decision poll that began before the mutation can still finish while the
    // POST is in flight. Settlement invalidates that late stale write again.
    writeCache('home:decisions:{"status":"all"}', { stale: 'late poll' });
    settlePost({ data: {} });
    await request;

    expect(readCache('home:decisions:{"status":"pending"}')).toBeNull();
    expect(readCache('home:decisions:{"status":"all"}')).toBeNull();
    expect(readCache('home:roles-breakdown')?.data).toEqual([{ role_id: 31 }]);
    expect(isCacheGenerationCurrent(duringMutation)).toBe(false);
    expect(post).toHaveBeenCalledWith(url, body);
  });
});
