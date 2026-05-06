import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../api', () => ({
  agent: {
    status: vi.fn(),
  },
  roles: {
    list: vi.fn(),
  },
}));

import { AgentBar } from './AgentBar';
import { agent, roles } from '../api';

describe('AgentBar — org fan-out', () => {
  beforeEach(() => {
    agent.status.mockReset();
    roles.list.mockReset();
  });

  it('renders nothing when no roles have the agent enabled', async () => {
    roles.list.mockResolvedValue({ data: [
      { id: 1, name: 'Role A', agentic_mode_enabled: false },
      { id: 2, name: 'Role B', agentic_mode_enabled: false },
    ] });

    const { container } = render(<AgentBar />);
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
    expect(agent.status).not.toHaveBeenCalled();
  });

  it('aggregates monthly spend + pending decisions across active roles', async () => {
    roles.list.mockResolvedValue({ data: [
      { id: 1, name: 'Role A', agentic_mode_enabled: true },
      { id: 2, name: 'Role B', agentic_mode_enabled: true },
      { id: 3, name: 'Role C', agentic_mode_enabled: false },
    ] });
    agent.status.mockImplementation((roleId) => {
      if (roleId === 1) {
        return Promise.resolve({ data: {
          paused: false,
          pending_decisions: 2,
          monthly_spent_cents: 1500,
          monthly_budget_cents: 5000,
          last_activity: { summary: 'Advanced Maya Chen', at: '2026-05-06T10:00:00Z' },
        } });
      }
      if (roleId === 2) {
        return Promise.resolve({ data: {
          paused: false,
          pending_decisions: 1,
          monthly_spent_cents: 800,
          monthly_budget_cents: 2500,
          last_activity: { summary: 'Rejected Alex P', at: '2026-05-06T11:30:00Z' },
        } });
      }
      return Promise.resolve({ data: {} });
    });

    render(<AgentBar />);

    // $1500 + $800 = $2300 cents → "$23.00 / $75.00"
    await screen.findByText(/\$23\.00 \/ \$75\.00/);
    // 2 + 1 = 3 awaiting review
    expect(screen.getByText(/3 awaiting your review/)).toBeInTheDocument();
    // Most recent activity (role B at 11:30) bubbles up with role name appended.
    expect(screen.getByText(/Rejected Alex P · Role B/)).toBeInTheDocument();
    // Only the 2 enabled roles get fan-out calls.
    expect(agent.status).toHaveBeenCalledTimes(2);
  });

  it('flips amber when monthly spend crosses 80% of budget', async () => {
    roles.list.mockResolvedValue({ data: [
      { id: 1, name: 'Role A', agentic_mode_enabled: true },
    ] });
    agent.status.mockResolvedValue({ data: {
      paused: false,
      pending_decisions: 0,
      monthly_spent_cents: 4500,
      monthly_budget_cents: 5000,
    } });

    const { container } = render(<AgentBar />);
    await waitFor(() => {
      const bar = container.querySelector('.mc-agent-bar');
      expect(bar).not.toBeNull();
      expect(bar.className).toContain('is-amber');
    });
  });
});
