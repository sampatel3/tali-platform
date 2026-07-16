import { render, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

import TestMemoryRouter from '../../test/TestMemoryRouter';
import { clearCache } from '../../shared/api/resourceCache';

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
  useToast: () => ({ showToast: vi.fn() }),
}));

// Render only the numbers we assert on — the cache logic under test lives in
// HomePage, and these props are exactly what it computes and hands down.
vi.mock('../../shared/layout/AgentHeader', () => ({
  AgentHeader: ({ kicker, className }) => (
    <div data-testid="kicker" className={className}>{kicker}</div>
  ),
}));
vi.mock('./HomeNow', () => ({
  HomeNow: ({ loading, staleCount, rolesBreakdown: roles, pendingOrdered }) => (
    <div data-testid="hn">
      {`loading:${loading} stale:${staleCount} roles:${roles.length} pending:${pendingOrdered.length}`}
    </div>
  ),
}));
vi.mock('./HomeAnalyticsSummary', () => ({ HomeAnalyticsSummary: () => null }));
vi.mock('./agentchat/AgentSidebar', () => ({ AgentSidebar: () => null }));
vi.mock('./agentchat/AgentChatDock', () => ({ AgentChatDock: () => null }));

import { HomePage } from './HomePage';

const renderHome = () =>
  render(
    <TestMemoryRouter initialEntries={['/home']}>
      <HomePage />
    </TestMemoryRouter>,
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
