import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { FleetView } from './FleetTab';

const minutesAgo = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString();

const panel = {
  pulse: {
    last_cycle_at: minutesAgo(8),
    last_activity_at: minutesAgo(2),
  },
  kpis: {
    agents_running: 2,
    agents_paused: 1,
    pending: 12,
    pending_decisions: 10,
    cycles_24h: 31,
    errors_24h: 1,
    budget_spent_cents: 6100,
    budget_cap_cents: 18000,
    oldest_pending_age_seconds: 3600,
  },
  agents: [
    {
      role_id: 1,
      name: 'Data Engineer',
      running: true,
      budget_spent_cents: 2400,
      budget_cap_cents: 5000,
      last_run_at: minutesAgo(8),
      pending: 7,
      cycles_24h: 14,
      activity: { label: 'WORKING', text: 'scoring 3 candidates' },
    },
    {
      role_id: 2,
      name: 'Product Designer',
      running: true,
      budget_spent_cents: 700,
      budget_cap_cents: 5000,
      last_run_at: minutesAgo(11),
      pending: 4,
      cycles_24h: 10,
      activity: { label: 'IDLE', text: 'idle' },
    },
    {
      role_id: 3,
      name: 'Platform Engineer',
      running: false,
      paused_reason: 'monthly_budget_reached',
      budget_spent_cents: 4900,
      budget_cap_cents: 5000,
      last_run_at: minutesAgo(45),
      pending: 1,
      cycles_24h: 7,
      activity: { label: 'PAUSED', text: 'budget cap reached' },
    },
  ],
  // The fleet view must not reproduce this data as a second decision table.
  recent_decisions: [
    { id: 99, candidate_name: 'Hidden Candidate', decision_type: 'advance_to_interview' },
  ],
};

const activity = [
  {
    id: 20,
    kind: 'decision',
    role_name: 'Data Engineer',
    title: 'Recommended an interview',
    detail: 'Candidate meets the role threshold.',
    created_at: minutesAgo(2),
  },
];

const renderView = (props = {}) => render(
  <FleetView panel={panel} activity={activity} {...props} />,
);

describe('FleetView', () => {
  it('uses one shared four-tile fleet summary', () => {
    const { container } = renderView();
    const summary = container.querySelector('.an-fleet-summary');

    expect(summary).toBeTruthy();
    expect(summary.querySelectorAll('.kpi-tile')).toHaveLength(4);
    expect(within(summary).getByText('Active agents')).toBeInTheDocument();
    expect(within(summary).getByText('Needs review')).toBeInTheDocument();
    expect(within(summary).getByText('Workspace spend')).toBeInTheDocument();
    expect(within(summary).getByText('Fleet health')).toBeInTheDocument();
    expect(summary.querySelector('.kpi-bar')).toBeTruthy();
  });

  it('gives each role one unified status and one status glyph', () => {
    const { container } = renderView();
    const workingCard = screen.getByRole('heading', { name: 'Data Engineer' }).closest('article');
    const idleCard = screen.getByRole('heading', { name: 'Product Designer' }).closest('article');
    const pausedCard = screen.getByRole('heading', { name: 'Platform Engineer' }).closest('article');

    expect(within(workingCard).getByText('Working · scoring 3 candidates')).toHaveClass('an-agent-status', 'work');
    expect(within(idleCard).getByText(/Idle · next run/)).toHaveClass('an-agent-status', 'idle');
    expect(within(pausedCard).getByText('Paused · monthly budget reached')).toHaveClass('an-agent-status', 'paused');

    for (const card of [workingCard, idleCard, pausedCard]) {
      expect(card.querySelectorAll('.an-agent-glyph')).toHaveLength(1);
      expect(card.querySelectorAll('.an-agent-status')).toHaveLength(1);
    }
    expect(container.querySelector('.an-apill')).toBeNull();
    expect(container.querySelector('.an-actbadge')).toBeNull();
    expect(screen.queryByText(/^ON$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^WORKING$/)).not.toBeInTheDocument();
  });

  it('shows recent activity without embedding another decision-log table', () => {
    renderView();

    expect(screen.getByRole('heading', { name: 'Recent activity' })).toBeInTheDocument();
    expect(screen.getByText('Recommended an interview')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByText('Hidden Candidate')).not.toBeInTheDocument();
  });

  it('opens the dedicated decision log from the activity card', () => {
    const onOpenDecisionLog = vi.fn();
    renderView({ onOpenDecisionLog });

    fireEvent.click(screen.getByRole('button', { name: 'View decision log' }));

    expect(onOpenDecisionLog).toHaveBeenCalledTimes(1);
  });
});
