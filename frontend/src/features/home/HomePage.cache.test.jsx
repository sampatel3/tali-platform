import { act, render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';

import { clearCache, readCache } from '../../shared/api/resourceCache';

// The Home page re-mounts on every tab switch (React Router unmounts it). This
// pins the fix: org-wide numbers (hero kicker, funnel roles, pending queue, the
// "needs re-eval" count) are seeded from the module-level SWR cache, so a warm
// re-mount paints the last-known values instantly — no flash to 0/empty and no
// loading spinner — while the polls revalidate in the background.

const orgStatus = vi.fn();
const rolesBreakdown = vi.fn();
const needsReevalCount = vi.fn();
const listDecisions = vi.fn();
const listConversations = vi.fn();
const capturedHomeNowProps = vi.hoisted(() => ({ current: null }));
const showToast = vi.hoisted(() => vi.fn());
const approvePost = vi.hoisted(() => vi.fn());

vi.mock('../../shared/api/httpClient', () => ({
  default: { post: (...args) => approvePost(...args) },
}));

vi.mock('../../shared/api', () => ({
  agent: {
    orgStatus: (...a) => orgStatus(...a),
    rolesBreakdown: (...a) => rolesBreakdown(...a),
    needsReevalCount: (...a) => needsReevalCount(...a),
    listDecisions: (...a) => listDecisions(...a),
  },
  agentChat: {
    listConversations: (...a) => listConversations(...a),
  },
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { full_name: 'Sam Patel' } }),
}));
vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

// Render only the numbers we assert on — the cache logic under test lives in
// HomePage, and these props are exactly what it computes and hands down.
vi.mock('../../shared/layout/AgentHeader', () => ({
  AgentHeader: ({ kicker, className }) => (
    <div data-testid="kicker" className={className}>{kicker}</div>
  ),
  buildAgentPropFromStatus: () => ({}),
}));
vi.mock('./HomeNow', () => ({
  HomeNow: (props) => {
    capturedHomeNowProps.current = props;
    const {
      loading,
      staleCount,
      rolesBreakdown: roles,
      pendingOrdered,
    } = props;
    return (
      <div data-testid="hn">
        {`loading:${loading} stale:${staleCount} roles:${roles.length} pending:${pendingOrdered.length}`}
      </div>
    );
  },
}));
vi.mock('./HomeAnalyticsSummary', () => ({ HomeAnalyticsSummary: () => null }));
vi.mock('./agentchat/AgentSidebar', () => ({ AgentSidebar: () => null }));
vi.mock('./agentchat/AgentChatDock', () => ({ AgentChatDock: () => null }));

import { HomePage } from './HomePage';
import { agent as liveAgentApi } from '../../shared/api/agentClient';

const renderHome = (initialEntry = '/home') =>
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <HomePage />
    </MemoryRouter>,
  );

const primeFirstMount = () => {
  orgStatus.mockResolvedValue({ data: { pending_decisions: 7, active_role_count: 3 } });
  rolesBreakdown.mockResolvedValue({ data: [{ role_id: 1 }, { role_id: 2 }] });
  needsReevalCount.mockResolvedValue({ data: { count: 4 } });
  listDecisions.mockResolvedValue({
    data: [
      { id: 1, status: 'pending', created_at: '2026-06-07T10:00:00Z', taali_score: 80 },
      { id: 2, status: 'pending', created_at: '2026-06-07T11:00:00Z', taali_score: 70 },
    ],
  });
  listConversations.mockResolvedValue({ data: { agents: [] } });
};

// After the first mount cached the numbers, a re-mount must not re-fetch to show
// them — so make every endpoint hang and prove the values still render.
const hangAllEndpoints = () => {
  const pending = () => new Promise(() => {});
  orgStatus.mockImplementation(pending);
  rolesBreakdown.mockImplementation(pending);
  needsReevalCount.mockImplementation(pending);
  listDecisions.mockImplementation(pending);
  listConversations.mockImplementation(pending);
};

beforeEach(() => {
  clearCache();
  vi.clearAllMocks();
  capturedHomeNowProps.current = null;
  approvePost.mockResolvedValue({ data: { decision_id: 1, status: 'processing' } });
});

test('cached home numbers paint instantly on re-mount without a loading flash', async () => {
  primeFirstMount();

  // First mount: fetch, render real numbers, populate the SWR cache.
  const first = renderHome();
  await waitFor(() => {
    expect(first.getByTestId('kicker').textContent).toContain('7 AWAITING YOU · 3 ACTIVE ROLES');
  });
  // The header opts into the shared Motion entrance wrapper on mount.
  expect(first.getByTestId('kicker').closest('[data-motion-reveal]'))
    .toHaveAttribute('data-motion-reveal', 'vertical');
  await waitFor(() => {
    expect(first.getByTestId('hn').textContent).toBe('loading:false stale:4 roles:2 pending:2');
  });
  first.unmount();

  // Second mount with every endpoint hanging: the numbers come from cache, so
  // they must appear on the very first render — no 0/empty flash, no spinner.
  hangAllEndpoints();
  const second = renderHome();
  expect(second.getByTestId('kicker').textContent).toContain('7 AWAITING YOU · 3 ACTIVE ROLES');
  expect(second.getByTestId('hn').textContent).toBe('loading:false stale:4 roles:2 pending:2');
});

test('cold home (empty cache) still shows the loading state on first mount', () => {
  clearCache();
  hangAllEndpoints();
  const { getByTestId } = renderHome();
  // No cache to seed from -> the queue reports loading and counts are empty.
  expect(getByTestId('hn').textContent).toBe('loading:true stale:0 roles:0 pending:0');
});

test('an uncached filter scope clears the prior stale count even when its read fails', async () => {
  primeFirstMount();
  renderHome();
  await waitFor(() => expect(capturedHomeNowProps.current?.staleCount).toBe(4));

  needsReevalCount.mockRejectedValue(new Error('count unavailable'));
  await act(async () => {
    capturedHomeNowProps.current.setFilters((filters) => ({ ...filters, role_id: 53 }));
  });

  await waitFor(() => {
    expect(Number(capturedHomeNowProps.current.filters.role_id)).toBe(53);
    expect(capturedHomeNowProps.current.staleCount).toBe(0);
  });
});

test('manual reload refreshes the stale count with the decision queue', async () => {
  primeFirstMount();
  renderHome();
  await waitFor(() => expect(capturedHomeNowProps.current?.staleCount).toBe(4));

  needsReevalCount.mockResolvedValue({ data: { count: 9 } });
  await act(async () => {
    await capturedHomeNowProps.current.reload();
  });

  await waitFor(() => expect(capturedHomeNowProps.current.staleCount).toBe(9));
});

test('decision loads publish a same-scope ticket for optimistic reconciliation', async () => {
  primeFirstMount();
  renderHome();

  await waitFor(() => expect(capturedHomeNowProps.current?.decisionRevision).toBeGreaterThan(0));
  expect(capturedHomeNowProps.current.decisionRevisionScopeKey)
    .toBe(capturedHomeNowProps.current.decisionScopeKey);

  // The prior test intentionally left the shared org-status request hanging.
  // A manual reload must still return the decision ticket independently.
  let receipt;
  await act(async () => {
    receipt = await capturedHomeNowProps.current.reload();
  });
  expect(receipt).toMatchObject({
    applied: true,
    scopeKey: capturedHomeNowProps.current.decisionScopeKey,
  });
});

test('Pending and Needs re-eval share one reconciliation scope', async () => {
  primeFirstMount();
  const pending = renderHome('/home');
  await waitFor(() => expect(capturedHomeNowProps.current?.decisionRevision).toBeGreaterThan(0));
  const pendingScope = capturedHomeNowProps.current.decisionScopeKey;
  pending.unmount();

  // Both views fetch the same pending API snapshot, so they must also share the
  // cache. Hold revalidation open and prove Needs re-eval paints Pending's rows
  // immediately instead of reviving a separate stale cache.
  listDecisions.mockImplementation(() => new Promise(() => {}));
  const stale = renderHome('/home?status=stale');
  await waitFor(() => expect(capturedHomeNowProps.current?.filters?.status).toBe('stale'));
  expect(capturedHomeNowProps.current.decisionScopeKey).toBe(pendingScope);
  expect(capturedHomeNowProps.current.loading).toBe(false);
  expect(capturedHomeNowProps.current.pendingOrdered).toHaveLength(2);
  stale.unmount();
});

test('switching back to cached rows resets their revision until revalidation', async () => {
  primeFirstMount();
  renderHome('/home');
  await waitFor(() => expect(capturedHomeNowProps.current?.decisionRevision).toBeGreaterThan(0));
  const allScope = capturedHomeNowProps.current.decisionScopeKey;

  // Publish another scope so its globally newer ticket is the last revision.
  await act(async () => {
    capturedHomeNowProps.current.setFilters((filters) => ({ ...filters, role_id: 53 }));
  });
  await waitFor(() => {
    expect(capturedHomeNowProps.current.decisionScopeKey).not.toBe(allScope);
    expect(capturedHomeNowProps.current.decisionRevisionScopeKey)
      .toBe(capturedHomeNowProps.current.decisionScopeKey);
  });

  // Hold the fresh all-scope request open. The cached all-scope rows repaint
  // immediately, but must carry revision 0 rather than the other scope's newer
  // ticket; HomeNow therefore cannot mistake the cache for a worker return.
  listDecisions.mockImplementation(() => new Promise(() => {}));
  await act(async () => {
    capturedHomeNowProps.current.setFilters((filters) => ({ ...filters, role_id: null }));
  });
  await waitFor(() => expect(capturedHomeNowProps.current.decisionScopeKey).toBe(allScope));
  await waitFor(() => {
    expect(capturedHomeNowProps.current.pendingOrdered).toHaveLength(2);
    expect(capturedHomeNowProps.current.decisionRevision).toBe(0);
    expect(capturedHomeNowProps.current.decisionRevisionScopeKey).toBe(allScope);
  });
});

test('a reload captured by an old scope cannot repaint over the current scope', async () => {
  primeFirstMount();
  renderHome('/home');
  await waitFor(() => expect(capturedHomeNowProps.current?.decisionRevision).toBeGreaterThan(0));
  const oldReload = capturedHomeNowProps.current.reload;
  const oldScope = capturedHomeNowProps.current.decisionScopeKey;

  await act(async () => {
    capturedHomeNowProps.current.setFilters((filters) => ({ ...filters, role_id: 53 }));
  });
  await waitFor(() => expect(capturedHomeNowProps.current.decisionScopeKey).not.toBe(oldScope));
  await waitFor(() => {
    expect(capturedHomeNowProps.current.decisionRevisionScopeKey)
      .toBe(capturedHomeNowProps.current.decisionScopeKey);
  });
  const currentScope = capturedHomeNowProps.current.decisionScopeKey;
  const currentRevision = capturedHomeNowProps.current.decisionRevision;

  let receipt;
  await act(async () => { receipt = await oldReload(); });
  expect(receipt).toMatchObject({
    applied: false,
    reason: 'scope-changed',
    scopeKey: oldScope,
  });
  expect(capturedHomeNowProps.current.decisionScopeKey).toBe(currentScope);
  expect(capturedHomeNowProps.current.decisionRevisionScopeKey).toBe(currentScope);
  expect(capturedHomeNowProps.current.decisionRevision).toBe(currentRevision);
});

test('an unmounted HomePage cannot write a stale decision response back to cache', async () => {
  primeFirstMount();
  let resolveDecisions;
  listDecisions.mockImplementation(() => new Promise((resolve) => { resolveDecisions = resolve; }));
  const first = renderHome('/home');
  await waitFor(() => expect(listDecisions).toHaveBeenCalled());
  first.unmount();

  await act(async () => {
    resolveDecisions({
      data: [{ id: 99, status: 'pending', created_at: '2026-06-07T10:00:00Z' }],
    });
    await Promise.resolve();
  });
  expect(readCache('home:decisions:{"role":null,"type":null,"status":"pending"}'))
    .toBeNull();
});

test('pending work stays ahead of higher-scoring processing receipts', async () => {
  primeFirstMount();
  listDecisions.mockResolvedValue({
    data: [
      { id: 1, status: 'pending', created_at: '2026-06-07T10:00:00Z', taali_score: 60 },
      { id: 2, status: 'processing', created_at: '2026-06-07T11:00:00Z', taali_score: 99 },
    ],
  });

  const home = renderHome();
  await waitFor(() => {
    expect(capturedHomeNowProps.current?.pendingOrdered?.map((decision) => decision.id))
      .toEqual([1, 2]);
  });
  home.unmount();
});

test('a pre-approval GET cannot refill stale decision cache after approval settles', async () => {
  orgStatus.mockResolvedValue({ data: { pending_decisions: 1, active_role_count: 1 } });
  rolesBreakdown.mockResolvedValue({ data: [] });
  needsReevalCount.mockResolvedValue({ data: { count: 0 } });
  listConversations.mockResolvedValue({ data: { agents: [] } });

  let resolveOldGet;
  listDecisions.mockImplementationOnce(() => new Promise((resolve) => {
    resolveOldGet = resolve;
  }));

  const first = renderHome();
  await waitFor(() => expect(listDecisions).toHaveBeenCalled());

  // The mutation starts and settles while the older GET is still in flight.
  // Both lifecycle invalidations happen before that GET tries to publish.
  await liveAgentApi.approveDecision(1);
  expect(approvePost).toHaveBeenCalledWith('/agent-decisions/1/approve', {});

  await act(async () => {
    resolveOldGet({
      data: [
        { id: 1, status: 'pending', created_at: '2026-06-07T10:00:00Z', taali_score: 80 },
      ],
    });
    await Promise.resolve();
  });
  await waitFor(() => expect(capturedHomeNowProps.current?.loading).toBe(false));
  expect(capturedHomeNowProps.current.pendingOrdered).toEqual([]);
  expect(readCache('home:decisions:{"role":null,"type":null,"status":"pending"}')).toBeNull();
  first.unmount();

  // A remount while revalidation is pending must start cold: the late old GET
  // did not resurrect the actionable decision in the module-level cache.
  hangAllEndpoints();
  const second = renderHome();
  expect(capturedHomeNowProps.current.loading).toBe(true);
  second.unmount();
});
