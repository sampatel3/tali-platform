import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { AgentHeader, buildAgentPropFromStatus } from './AgentHeader';

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
    const { container } = render(<AgentHeader title="Jobs" agent={runningAgent} onPauseAgent={onPause} />);
    expect(container.querySelector('.abar')).toHaveAttribute('data-motion-loop', 'glow');
    expect(container.querySelector('.abar-flow-layer')).toHaveAttribute('data-motion-loop', 'flow');
    expect(container.querySelector('.ab-pulse')).toHaveAttribute('data-motion-loop', 'ring');
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
    const { container } = render(
      <AgentHeader
        title="Jobs"
        agent={{
          ...runningAgent,
          on: false,
          paused: true,
          pausedReason: 'paused by recruiter',
          pausedBy: { user_id: 7, name: 'Sam Patel', is_current_user: true },
        }}
        onResumeAgent={onResume}
      />,
    );
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.queryByText('Auto-paused')).not.toBeInTheDocument();
    const manualContext = container.querySelector('.ab-context-manual');
    expect(manualContext).not.toBeNull();
    expect(within(manualContext).getByText('3 awaiting you')).toBeInTheDocument();
    expect(within(manualContext).getByText('Paused by you')).toHaveAttribute(
      'aria-label',
      'Paused by you',
    );
    expect(container.querySelector('.abar')).toHaveAttribute('data-motion-state', 'rest');
    expect(container.querySelector('.abar-flow-layer')).toHaveAttribute('data-motion-state', 'rest');

    const btn = screen.getByRole('button', { name: /^resume$/i });
    expect(btn).toHaveClass('taali-btn', 'taali-btn-primary', 'taali-btn-sm');
    fireEvent.click(btn);
    expect(onResume).toHaveBeenCalledTimes(1);
  });

  it('names the teammate who manually paused the role', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={{
          ...runningAgent,
          on: false,
          paused: true,
          pausedReason: 'paused by recruiter',
          pausedBy: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
        }}
        onResumeAgent={() => {}}
      />,
    );

    expect(screen.getByText('Paused by Aisha Khan')).toHaveAttribute(
      'title',
      'Paused by Aisha Khan',
    );
    expect(screen.queryByText(/by you/i)).not.toBeInTheDocument();
  });

  it('keeps a long teammate identity explicit in the manual-pause context', () => {
    const { container } = render(
      <AgentHeader
        title="Principal Specialist – Vulnerability Management & Cloud Security"
        agent={{
          ...runningAgent,
          on: false,
          paused: true,
          pending: 148,
          pausedReason: 'paused by recruiter',
          pausedBy: {
            user_id: 11,
            name: 'Alexandra Montgomery-Smythe',
            is_current_user: false,
          },
        }}
        onResumeAgent={() => {}}
        onTurnOffAgent={() => {}}
        onAgentSettings={() => {}}
      />,
    );

    const manualContext = container.querySelector('.ab-context-manual');
    expect(manualContext).not.toBeNull();
    expect(within(manualContext).getByText('148 awaiting you')).toBeInTheDocument();
    expect(
      within(manualContext).getByText('Paused by Alexandra Montgomery-Smythe'),
    ).toHaveAttribute('title', 'Paused by Alexandra Montgomery-Smythe');
  });

  it('uses an honest manual fallback when historical pause actor data is unavailable', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: true, pausedReason: 'paused by recruiter' }}
        onResumeAgent={() => {}}
      />,
    );

    expect(screen.getByText('Paused manually')).toBeInTheDocument();
    expect(screen.queryByText(/by you/i)).not.toBeInTheDocument();
  });

  it('labels the actionable total and exposes its decision/question breakdown', () => {
    const agent = buildAgentPropFromStatus({
      enabled: true,
      pending_decisions: 999,
      pending_breakdown: { total: 176, decisions: 175, questions: 1 },
      monthly_spent_cents: 5441,
      monthly_budget_cents: 5000,
    });

    render(<AgentHeader title="Jobs" agent={agent} onPauseAgent={() => {}} />);

    const pending = screen.getByText('176 awaiting you');
    expect(pending).toHaveAttribute(
      'aria-label',
      '176 awaiting you: 175 candidate decisions and 1 agent question',
    );
    expect(agent.pending).toBe(176);
    expect(agent.pendingBreakdown).toEqual({ total: 176, decisions: 175, questions: 1 });
  });

  it('keeps Auto-paused wording for a budget-triggered pause', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={{
          ...runningAgent,
          on: false,
          paused: true,
          pausedReason: 'monthly usd cap reached: 5000c >= 5000c',
          pausedBy: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
        }}
        onResumeAgent={() => {}}
      />,
    );
    expect(screen.getByText('Auto-paused')).toBeInTheDocument();
    expect(screen.getByText(/monthly budget reached/i)).toBeInTheDocument();
    expect(screen.queryByText(/Aisha Khan/i)).not.toBeInTheDocument();
  });

  it('shows an honest starting state until the worker acknowledges activation', () => {
    const agent = buildAgentPropFromStatus({
      enabled: true,
      bootstrap_status: 'starting',
      pending_decisions: 0,
      monthly_spent_cents: 0,
      monthly_budget_cents: 5000,
    });
    render(<AgentHeader title="Jobs" agent={agent} onPauseAgent={() => {}} />);
    expect(screen.getByText('Agent starting')).toBeInTheDocument();
    expect(screen.getByText(/starting first autonomous cycle/i)).toBeInTheDocument();
  });

  it('surfaces an exhausted bootstrap as a retryable auto-pause', () => {
    const agent = buildAgentPropFromStatus({
      enabled: true,
      paused_at: new Date().toISOString(),
      paused_reason: 'agent bootstrap failed after retries: model unavailable',
      bootstrap_status: 'failed',
      bootstrap_error: 'model unavailable',
      pending_decisions: 0,
      monthly_spent_cents: 0,
      monthly_budget_cents: 5000,
    });
    render(<AgentHeader title="Jobs" agent={agent} onResumeAgent={() => {}} />);
    expect(screen.getByText('Auto-paused')).toBeInTheDocument();
    expect(screen.getByText(/startup held.*auto-checking/i)).toBeInTheDocument();
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

  it('keeps Agent settings discoverable before the agent is turned on', () => {
    const onSettings = vi.fn();
    render(
      <AgentHeader
        title="Jobs"
        agent={{ ...runningAgent, on: false, paused: false }}
        onActivateAgent={() => {}}
        onAgentSettings={onSettings}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Configure agent' }));
    expect(onSettings).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /turn on/i })).toBeInTheDocument();
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
