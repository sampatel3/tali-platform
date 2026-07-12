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
  it('renders the Pause button and fires onPauseAgent when running', () => {
    const onPause = vi.fn();
    render(<AgentHeader title="Jobs" agent={runningAgent} onPauseAgent={onPause} />);
    const btn = screen.getByRole('button', { name: /^pause$/i });
    expect(btn).not.toBeDisabled();
    expect(btn).toHaveClass('taali-btn', 'taali-btn-inverse', 'taali-btn-sm');
    fireEvent.click(btn);
    expect(onPause).toHaveBeenCalledTimes(1);
  });

  it('disables the Pause button when no onPauseAgent is wired', () => {
    render(<AgentHeader title="Jobs" agent={runningAgent} />);
    expect(screen.getByRole('button', { name: /^pause$/i })).toBeDisabled();
  });

  it('shows Paused (not Auto-paused) and the Resume button after a manual pause', () => {
    const onResume = vi.fn();
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'paused by recruiter' }}
        onResumeAgent={onResume}
      />,
    );
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.queryByText('Auto-paused')).not.toBeInTheDocument();
    expect(screen.getByText(/paused by you/i)).toBeInTheDocument();

    const btn = screen.getByRole('button', { name: /^resume$/i });
    expect(btn).toHaveClass('taali-btn', 'taali-btn-primary', 'taali-btn-sm');
    fireEvent.click(btn);
    expect(onResume).toHaveBeenCalledTimes(1);
  });

  it('keeps Auto-paused wording for a budget-triggered pause', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'monthly usd cap reached: 5000c >= 5000c' }}
        onResumeAgent={() => {}}
      />,
    );
    expect(screen.getByText('Auto-paused')).toBeInTheDocument();
    expect(screen.getByText(/monthly budget reached/i)).toBeInTheDocument();
  });

  it('uses the canonical primary small action when the agent is off', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: false }}
        onActivateAgent={() => {}}
      />,
    );

    expect(screen.getByRole('button', { name: /turn on/i })).toHaveClass(
      'taali-btn-primary',
      'taali-btn-sm',
    );
  });

  it('renders a Turn off control only when onTurnOffAgent is wired, and fires it', () => {
    const onTurnOff = vi.fn();
    const { rerender } = render(
      <AgentHeader title="Jobs" agent={runningAgent} onPauseAgent={() => {}} />,
    );
    // No Turn off button without a handler.
    expect(screen.queryByRole('button', { name: /turn off agent/i })).not.toBeInTheDocument();

    rerender(
      <AgentHeader title="Jobs" agent={runningAgent} onPauseAgent={() => {}} onTurnOffAgent={onTurnOff} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /turn off agent/i }));
    expect(screen.getByRole('button', { name: /turn off agent/i })).toHaveClass(
      'taali-btn-inverse',
      'taali-btn-sm',
      'taali-btn-icon-only',
    );
    expect(onTurnOff).toHaveBeenCalledTimes(1);
  });

  it('offers Turn off alongside Resume while paused', () => {
    const onTurnOff = vi.fn();
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'paused by recruiter' }}
        onResumeAgent={() => {}}
        onTurnOffAgent={onTurnOff}
      />,
    );
    expect(screen.getByRole('button', { name: /^resume$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /turn off agent/i })).toBeInTheDocument();
  });

  it('does not show Turn off in org bulk mode', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={runningAgent}
        onPauseAgent={() => {}}
        onResumeAgent={() => {}}
        onTurnOffAgent={() => {}}
        pauseAllCount={3}
        resumeAllCount={2}
      />,
    );
    expect(screen.queryByRole('button', { name: /turn off agent/i })).not.toBeInTheDocument();
  });

  describe('org bulk mode (mixed org = both Pause and Resume)', () => {
    it('shows BOTH Pause and Resume — and states the split — in a mixed org', () => {
      const onPause = vi.fn();
      const onResume = vi.fn();
      render(
        <AgentHeader
          title="Jobs"
          agent={runningAgent}
          onPauseAgent={onPause}
          onResumeAgent={onResume}
          pauseAllCount={1}
          resumeAllCount={10}
        />,
      );
      // The split lives in the tick, so the buttons stay short ("Pause"/"Resume").
      expect(screen.getByText(/1 running · 10 paused/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /^pause$/i }));
      fireEvent.click(screen.getByRole('button', { name: /^resume$/i }));
      expect(onPause).toHaveBeenCalledTimes(1);
      expect(onResume).toHaveBeenCalledTimes(1);
    });

    it('shows only Pause when nothing is paused', () => {
      render(
        <AgentHeader
          title="Jobs"
          agent={runningAgent}
          onPauseAgent={() => {}}
          onResumeAgent={() => {}}
          pauseAllCount={3}
          resumeAllCount={0}
        />,
      );
      expect(screen.getByRole('button', { name: /^pause$/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^resume$/i })).not.toBeInTheDocument();
    });

    it('shows only Resume when every agent is paused', () => {
      render(
        <AgentHeader
          title="Jobs"
          agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'paused by recruiter' }}
          onPauseAgent={() => {}}
          onResumeAgent={() => {}}
          pauseAllCount={0}
          resumeAllCount={5}
        />,
      );
      expect(screen.getByRole('button', { name: /^resume$/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^pause$/i })).not.toBeInTheDocument();
    });
  });
});
