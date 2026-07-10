import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
  agent: {
    status: vi.fn(),
    orgStatus: vi.fn(),
  },
}));

import { AgentBar } from './AgentBar';
import { agent } from '../api';

describe('AgentBar — org rollup', () => {
  beforeEach(() => {
    agent.status.mockReset();
    agent.orgStatus.mockReset();
  });

  it('renders nothing when no roles have the agent enabled', async () => {
    // org-status reports zero enabled roles (no running, none paused).
    agent.orgStatus.mockResolvedValue({ data: {
      active_role_count: 0,
      paused_role_count: 0,
      pending_decisions: 0,
      org_budget_spent_cents: 0,
      org_budget_cap_cents: 0,
    } });

    const { container } = render(<AgentBar />);
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
    // The bar reads the single org aggregate — never the per-role fan-out.
    expect(agent.status).not.toHaveBeenCalled();
  });

  it('renders the org budget + pending totals from the aggregate', async () => {
    // Two running enabled roles; org-status already sums spend/budget/pending.
    agent.orgStatus.mockResolvedValue({ data: {
      active_role_count: 2,
      paused_role_count: 0,
      pending_decisions: 3,
      org_budget_spent_cents: 2300,
      org_budget_cap_cents: 7500,
      last_activity: { summary: 'Rejected Alex P · Role B', created_at: '2026-05-06T11:30:00Z' },
    } });

    render(<AgentBar />);

    // $2300 cents → "$23.00 / $75.00"
    await screen.findByText(/\$23\.00 \/ \$75\.00/);
    expect(screen.getByText(/3 awaiting your review/)).toBeInTheDocument();
    // last_activity.summary drives the tick line.
    expect(screen.getByText(/Rejected Alex P · Role B/)).toBeInTheDocument();
    // Single org aggregate call — no per-role fan-out.
    expect(agent.status).not.toHaveBeenCalled();
  });

  it('flips amber when monthly spend crosses 80% of budget', async () => {
    agent.orgStatus.mockResolvedValue({ data: {
      active_role_count: 1,
      paused_role_count: 0,
      pending_decisions: 0,
      org_budget_spent_cents: 4500,
      org_budget_cap_cents: 5000,
    } });

    const { container } = render(<AgentBar />);
    await waitFor(() => {
      const bar = container.querySelector('.mc-agent-bar');
      expect(bar).not.toBeNull();
      expect(bar.className).toContain('is-amber');
    });
  });

  it('reads "Agent mode paused" when every enabled role is paused', async () => {
    agent.orgStatus.mockResolvedValue({ data: {
      active_role_count: 0,
      paused_role_count: 2,
      pending_decisions: 0,
      org_budget_spent_cents: 0,
      org_budget_cap_cents: 5000,
    } });

    render(<AgentBar />);
    await screen.findByText(/Agent mode paused/);
  });
});
