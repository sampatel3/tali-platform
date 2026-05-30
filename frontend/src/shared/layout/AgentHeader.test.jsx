import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { AgentHeader } from './AgentHeader';

const runningAgent = {
  on: true,
  paused: false,
  pending: 3,
  spentCents: 1820,
  budgetCents: 5000,
  tick: 'Scoring candidates',
  inFlight: true,
};

describe('AgentHeader — Pause/Resume panel', () => {
  it('renders the custom pauseLabel and fires onPauseAgent when running', () => {
    const onPause = vi.fn();
    render(
      <AgentHeader title="Jobs" agent={runningAgent} onPauseAgent={onPause} pauseLabel="Pause all" />,
    );
    const btn = screen.getByRole('button', { name: /pause all/i });
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(onPause).toHaveBeenCalledTimes(1);
  });

  it('disables the Pause button when no onPauseAgent is wired', () => {
    render(<AgentHeader title="Jobs" agent={runningAgent} pauseLabel="Pause all" />);
    expect(screen.getByRole('button', { name: /pause all/i })).toBeDisabled();
  });

  it('shows PAUSED (not AUTO-PAUSED) and the custom resumeLabel after a manual pause', () => {
    const onResume = vi.fn();
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'paused by recruiter' }}
        onResumeAgent={onResume}
        resumeLabel="Resume all"
      />,
    );
    expect(screen.getByText('PAUSED')).toBeInTheDocument();
    expect(screen.queryByText('AUTO-PAUSED')).not.toBeInTheDocument();
    expect(screen.getByText(/paused by you/i)).toBeInTheDocument();

    const btn = screen.getByRole('button', { name: /resume all/i });
    fireEvent.click(btn);
    expect(onResume).toHaveBeenCalledTimes(1);
  });

  it('keeps AUTO-PAUSED wording for a budget-triggered pause', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'monthly usd cap reached: 5000c >= 5000c' }}
        onResumeAgent={() => {}}
        resumeLabel="Resume all"
      />,
    );
    expect(screen.getByText('AUTO-PAUSED')).toBeInTheDocument();
    expect(screen.getByText(/monthly budget reached/i)).toBeInTheDocument();
  });
});
