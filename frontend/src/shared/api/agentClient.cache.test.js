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
    [
      'override',
      () => agent.overrideDecision(42, { override_action: 'manual_review' }),
      '/agent-decisions/42/override',
      { override_action: 'manual_review' },
    ],
    [
      're-evaluation',
      () => agent.reEvaluateDecision(42),
      '/agent-decisions/42/re-evaluate',
      {},
    ],
    [
      'queue discard',
      () => agent.discardPending(31, 7),
      '/agent-decisions/discard',
      { role_id: 31, expected_version: 7 },
    ],
    [
      'bulk override',
      () => agent.bulkOverrideDecisions([42, 43], 'skip_assessment_advance', 'Reviewed', { 31: 'interview' }),
      '/agent-decisions/bulk-override',
      {
        decision_ids: [42, 43],
        override_action: 'skip_assessment_advance',
        note: 'Reviewed',
        workable_target_stages: { 31: 'interview' },
      },
    ],
    [
      'snooze',
      () => agent.snoozeDecision(42),
      '/agent-decisions/42/snooze',
      {},
    ],
    [
      'feedback',
      () => agent.sendFeedback({ decision_id: 42, correction_text: 'Keep in review' }),
      '/agent/feedback',
      { decision_id: 42, correction_text: 'Keep in review' },
    ],
    [
      'feedback revert',
      () => agent.revertFeedback(9),
      '/agent/feedback/9/revert',
      {},
    ],
  ])('drops every Home queue scope around %s', async (_label, approve, url, body) => {
    let settlePost;
    post.mockImplementationOnce(() => new Promise((resolve) => { settlePost = resolve; }));
    writeCache('home:decisions:{"status":"pending"}', { stale: 'pending' });
    writeCache('home:decisions:{"status":"all"}', { stale: 'all' });
    writeCache('home:stale:all:all', 7);
    writeCache('home:roles-breakdown', [{ role_id: 31 }]);
    const beforeMutation = captureCacheGeneration('home:decisions:');
    const beforeStaleMutation = captureCacheGeneration('home:stale:');

    const request = approve();

    // Mutation start clears every old filter scope immediately.
    expect(readCache('home:decisions:{"status":"pending"}')).toBeNull();
    expect(readCache('home:decisions:{"status":"all"}')).toBeNull();
    expect(readCache('home:stale:all:all')).toBeNull();
    expect(isCacheGenerationCurrent(beforeMutation)).toBe(false);
    expect(isCacheGenerationCurrent(beforeStaleMutation)).toBe(false);
    const duringMutation = captureCacheGeneration('home:decisions:');
    const duringStaleMutation = captureCacheGeneration('home:stale:');

    // A decision poll that began before the mutation can still finish while the
    // POST is in flight. Settlement invalidates that late stale write again.
    writeCache('home:decisions:{"status":"all"}', { stale: 'late poll' });
    writeCache('home:stale:all:all', 7);
    settlePost({ data: {} });
    await request;

    expect(readCache('home:decisions:{"status":"pending"}')).toBeNull();
    expect(readCache('home:decisions:{"status":"all"}')).toBeNull();
    expect(readCache('home:stale:all:all')).toBeNull();
    expect(readCache('home:roles-breakdown')?.data).toEqual([{ role_id: 31 }]);
    expect(isCacheGenerationCurrent(duringMutation)).toBe(false);
    expect(isCacheGenerationCurrent(duringStaleMutation)).toBe(false);
    expect(post).toHaveBeenCalledWith(url, body);
  });
});
