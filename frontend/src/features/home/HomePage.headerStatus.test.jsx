import { render, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

import TestMemoryRouter from '../../test/TestMemoryRouter';
import { clearCache } from '../../shared/api/resourceCache';

const harness = vi.hoisted(() => ({
  payload: null,
  refetch: vi.fn(),
  showToast: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
}));

vi.mock('../../shared/layout/AgentBar', () => ({
  useAgentStatusOrg: () => ({
    payload: harness.payload,
    refetch: harness.refetch,
  }),
}));

vi.mock('../../shared/api', () => ({
  agent: {
    rolesBreakdown: vi.fn().mockResolvedValue({ data: [] }),
    needsReevalCount: vi.fn().mockResolvedValue({ data: { count: 0 } }),
    listDecisions: vi.fn().mockResolvedValue({ data: [] }),
  },
  agentChat: {
    listConversations: vi.fn().mockResolvedValue({ data: { agents: [] } }),
  },
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { full_name: 'Sam Patel', role: 'owner' } }),
}));
vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast: harness.showToast }),
}));
vi.mock('../../shared/motion', () => ({
  Reveal: ({ children }) => children,
}));
vi.mock('./useWorkspaceAgentControl', () => ({
  useWorkspaceAgentControl: () => ({
    action: 'idle',
    pause: harness.pause,
    resume: harness.resume,
  }),
}));

vi.mock('../../shared/layout/AgentHeader', () => ({
  AgentHeader: ({ agent }) => (
    <div
      data-testid="agent-status"
      data-in-flight={agent?.inFlight ? 'true' : 'false'}
      data-tick={agent?.tick || ''}
    />
  ),
  buildAgentPropFromStatus: (status) => ({
    inFlight: Boolean(status?.current_run),
    tick: status?.last_activity?.summary || 'Monitoring roles',
    runningRoleCount: Number(status?.active_role_count || 0),
    localPausedRoleCount: Number(status?.paused_role_count || 0),
  }),
}));

vi.mock('./HomeAgentWorkspace', () => ({
  HomeAgentWorkspace: ({ children }) => children,
}));
vi.mock('./HomeNow', () => ({ HomeNow: () => null }));
vi.mock('./HomeAnalyticsSummary', () => ({ HomeAnalyticsSummary: () => null }));

import { HomePage } from './HomePage';

const renderHome = () => (
  <TestMemoryRouter initialEntries={['/home']}>
    <HomePage />
  </TestMemoryRouter>
);

beforeEach(() => {
  clearCache();
  harness.refetch.mockReset();
  harness.showToast.mockReset();
  harness.pause.mockReset();
  harness.resume.mockReset();
  harness.payload = {
    active_role_count: 2,
    paused_role_count: 0,
    pending_decisions: 0,
    org_budget_spent_cents: 100,
    org_budget_cap_cents: 5000,
    current_run: null,
    last_activity: { summary: 'Initial activity' },
  };
});

test('header refreshes when only current run and latest activity change', async () => {
  const view = render(renderHome());
  await waitFor(() => {
    expect(view.getByTestId('agent-status')).toHaveAttribute(
      'data-tick',
      'Initial activity',
    );
  });
  expect(view.getByTestId('agent-status')).toHaveAttribute('data-in-flight', 'false');

  harness.payload = {
    ...harness.payload,
    current_run: { id: 91, status: 'running' },
    last_activity: { summary: 'Scoring a candidate' },
  };
  view.rerender(renderHome());

  await waitFor(() => {
    expect(view.getByTestId('agent-status')).toHaveAttribute(
      'data-tick',
      'Scoring a candidate',
    );
  });
  expect(view.getByTestId('agent-status')).toHaveAttribute('data-in-flight', 'true');
});
