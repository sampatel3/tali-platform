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

  it('shows Paused (not Auto-paused) and the custom resumeLabel after a manual pause', () => {
    const onResume = vi.fn();
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'paused by recruiter' }}
        onResumeAgent={onResume}
        resumeLabel="Resume all"
      />,
    );
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.queryByText('Auto-paused')).not.toBeInTheDocument();
    expect(screen.getByText(/resume to continue/i)).toBeInTheDocument();

    const btn = screen.getByRole('button', { name: /resume all/i });
    fireEvent.click(btn);
    expect(onResume).toHaveBeenCalledTimes(1);
  });

  it('keeps Auto-paused wording for a budget-triggered pause', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'monthly usd cap reached: 5000c >= 5000c' }}
        onResumeAgent={() => {}}
        resumeLabel="Resume all"
      />,
    );
    expect(screen.getByText('Auto-paused')).toBeInTheDocument();
    expect(screen.getByText(/monthly budget reached/i)).toBeInTheDocument();
  });

  describe('org bulk mode (Pause all / Resume all with counts)', () => {
    it('shows BOTH "Pause all (N)" and "Resume all (M)" in a mixed org', () => {
      const onPause = vi.fn();
      const onResume = vi.fn();
      render(
        <AgentHeader
          title="Jobs"
          agent={runningAgent}
          onPauseAgent={onPause}
          onResumeAgent={onResume}
          pauseLabel="Pause all"
          resumeLabel="Resume all"
          pauseAllCount={1}
          resumeAllCount={10}
        />,
      );
      fireEvent.click(screen.getByRole('button', { name: /pause all \(1\)/i }));
      fireEvent.click(screen.getByRole('button', { name: /resume all \(10\)/i }));
      expect(onPause).toHaveBeenCalledTimes(1);
      expect(onResume).toHaveBeenCalledTimes(1);
    });

    it('shows only "Pause all (N)" when nothing is paused', () => {
      render(
        <AgentHeader
          title="Jobs"
          agent={runningAgent}
          onPauseAgent={() => {}}
          onResumeAgent={() => {}}
          pauseLabel="Pause all"
          resumeLabel="Resume all"
          pauseAllCount={3}
          resumeAllCount={0}
        />,
      );
      expect(screen.getByRole('button', { name: /pause all \(3\)/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /resume all/i })).not.toBeInTheDocument();
    });

    it('shows only "Resume all (M)" when every agent is paused', () => {
      render(
        <AgentHeader
          title="Jobs"
          agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'paused by recruiter' }}
          onPauseAgent={() => {}}
          onResumeAgent={() => {}}
          pauseLabel="Pause all"
          resumeLabel="Resume all"
          pauseAllCount={0}
          resumeAllCount={5}
        />,
      );
      expect(screen.getByRole('button', { name: /resume all \(5\)/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /pause all/i })).not.toBeInTheDocument();
    });
  });
});
