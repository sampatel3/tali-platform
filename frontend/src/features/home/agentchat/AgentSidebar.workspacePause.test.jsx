import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AgentSidebar } from './AgentSidebar';

const heldAgents = [
  {
    role_id: 1,
    role_name: 'Desired running',
    group: 'on_paused',
    agent_enabled: true,
    agent_effective_paused: true,
    agent_pause_scope: 'workspace',
    agent_paused: true,
    role_paused: false,
    workspace_paused: true,
    workspace_paused_by: { name: 'Sam Patel', is_current_user: true },
  },
  {
    role_id: 2,
    role_name: 'Locally paused',
    group: 'on_paused',
    agent_enabled: true,
    agent_effective_paused: true,
    agent_pause_scope: 'workspace',
    agent_paused: true,
    role_paused: true,
    role_paused_reason: 'paused by recruiter',
    workspace_paused: true,
    workspace_paused_by: { name: 'Sam Patel', is_current_user: true },
  },
  {
    role_id: 3,
    role_name: 'Agent off',
    group: 'active',
    agent_enabled: false,
    agent_effective_paused: false,
    agent_pause_scope: null,
    agent_paused: false,
    role_paused: false,
    workspace_paused: true,
  },
];

describe('AgentSidebar workspace pause overlay', () => {
  it('renders enabled roles as held without an ON animation and preserves local pause intent', () => {
    const { rerender } = render(
      <AgentSidebar agents={heldAgents} activeRoleId={null} onSelect={vi.fn()} />,
    );

    expect(screen.getByText('0 running · 2 held')).toBeInTheDocument();
    const desired = screen.getByText('Desired running').closest('button');
    const local = screen.getByText('Locally paused').closest('button');
    const off = screen.getByText('Agent off').closest('button');

    expect(desired).toHaveAttribute('data-agent-state', 'held');
    expect(within(desired).getByText('Held · All agents paused by Sam Patel (you)')).toBeInTheDocument();
    expect(desired.querySelector('.ac-stat-on')).toBeNull();
    expect(local).toHaveAttribute('data-agent-state', 'held');
    expect(within(local).getByText(/Role stays paused after resume/)).toBeInTheDocument();
    expect(local.querySelector('.ac-stat-on')).toBeNull();
    expect(off).toHaveAttribute('data-agent-state', 'off');

    const resumed = heldAgents.map((agent) => ({
      ...agent,
      workspace_paused: false,
      workspace_paused_by: null,
      agent_pause_scope: agent.role_paused ? 'role' : null,
      agent_effective_paused: Boolean(agent.role_paused),
      agent_paused: Boolean(agent.role_paused),
      agent_paused_reason: agent.role_paused_reason || null,
    }));
    rerender(<AgentSidebar agents={resumed} activeRoleId={null} onSelect={vi.fn()} />);

    const resumedDesired = screen.getByText('Desired running').closest('button');
    const resumedLocal = screen.getByText('Locally paused').closest('button');
    expect(resumedDesired).toHaveAttribute('data-agent-state', 'on');
    expect(resumedDesired.querySelector('.ac-stat-on')).not.toBeNull();
    expect(resumedLocal).toHaveAttribute('data-agent-state', 'paused');
    expect(within(resumedLocal).getByText('Paused manually')).toBeInTheDocument();
  });
});
