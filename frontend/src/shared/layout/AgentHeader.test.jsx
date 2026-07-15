import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { AgentHeader, buildAgentPropFromStatus } from './AgentHeader';

const defaultMatchMedia = window.matchMedia;

afterEach(() => {
  window.matchMedia = defaultMatchMedia;
});

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

  it('shows Agent paused (not Auto-paused), the full actor, and Resume after a manual pause', () => {
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
    expect(screen.getByLabelText('Agent paused')).toBeInTheDocument();
    expect(screen.queryByText('Auto-paused')).not.toBeInTheDocument();
    const manualState = container.querySelector('.ab-state-manual');
    expect(manualState).not.toBeNull();
    expect(screen.getByLabelText('3 items awaiting review')).toHaveTextContent('3 to review');
    expect(within(manualState).getByLabelText('Paused by Sam Patel (you)')).toHaveAttribute(
      'aria-label',
      'Paused by Sam Patel (you)',
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

    expect(screen.getByLabelText('Paused by Aisha Khan')).toHaveAttribute(
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

    const manualState = container.querySelector('.ab-state-manual');
    expect(manualState).not.toBeNull();
    expect(screen.getByLabelText('148 items awaiting review')).toHaveTextContent('148 to review');
    expect(
      within(manualState).getByLabelText('Paused by Alexandra Montgomery-Smythe'),
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

    expect(screen.getByLabelText('Pause owner not recorded')).toHaveAttribute(
      'title',
      expect.stringMatching(/before actor tracking/i),
    );
    expect(screen.queryByText(/by you/i)).not.toBeInTheDocument();
  });

  it('presents a workspace override as its own attributed control', () => {
    const onResume = vi.fn();
    const agent = buildAgentPropFromStatus({
      workspace_paused: true,
      workspace_control_version: 4,
      workspace_paused_at: new Date().toISOString(),
      workspace_paused_reason: 'paused by recruiter',
      workspace_paused_by: {
        user_id: 7,
        name: 'Sam Patel',
        is_current_user: true,
        attribution: 'verified',
        source: 'workspace_control',
      },
      pending_decisions: 14,
      org_budget_spent_cents: 52300,
      org_budget_cap_cents: 300000,
    }, { isEnabled: true, controlScope: 'workspace' });

    render(
      <AgentHeader
        title="Jobs"
        agent={agent}
        onResumeAgent={onResume}
      />,
    );

    expect(screen.getByLabelText('Workspace agent paused')).toBeInTheDocument();
    expect(screen.getByLabelText(/Paused by Sam Patel \(you\)/i)).toBeInTheDocument();
    expect(screen.queryByText(/Pause owner not recorded/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Resume workspace' }));
    expect(onResume).toHaveBeenCalledTimes(1);
  });

  it('does not confuse locally paused roles with the workspace override', () => {
    const agent = buildAgentPropFromStatus({
      workspace_paused: false,
      workspace_control_version: 5,
      active_role_count: 2,
      paused_role_count: 3,
      local_paused_role_count: 3,
      pending_decisions: 3,
    }, { isEnabled: true, controlScope: 'workspace' });

    render(<AgentHeader title="Jobs" agent={agent} onPauseAgent={() => {}} />);

    expect(screen.getByLabelText('Workspace agent on')).toBeInTheDocument();
    expect(screen.getByText('2 running · 3 role-paused')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pause workspace' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Resume workspace' })).not.toBeInTheDocument();
  });

  it('keeps a zero-role workspace hold visible without inventing a $50 budget', () => {
    const agent = buildAgentPropFromStatus({
      workspace_paused: true,
      workspace_control_version: 6,
      workspace_paused_at: new Date().toISOString(),
      workspace_paused_reason: 'workspace paused by recruiter',
      workspace_paused_by: { user_id: 7, name: 'Sam Patel', is_current_user: true },
      active_role_count: 0,
      paused_role_count: 0,
      org_budget_cap_cents: 0,
    }, { isEnabled: false, controlScope: 'workspace' });

    render(<AgentHeader title="Jobs" agent={agent} onResumeAgent={() => {}} />);

    expect(screen.getByLabelText('Workspace agent paused')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume workspace' })).not.toBeDisabled();
    expect(screen.queryByText('AI spend')).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('shows the effective workspace hold while preserving an on role underneath', () => {
    const onPauseRole = vi.fn();
    const agent = buildAgentPropFromStatus({
      enabled: true,
      paused: true,
      pause_scope: 'workspace',
      paused_at: new Date().toISOString(),
      paused_reason: 'paused by recruiter',
      paused_by: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
      role_paused_at: null,
      role_paused_reason: null,
      role_paused_by: null,
      workspace_paused: true,
      workspace_control_version: 6,
      workspace_paused_at: new Date().toISOString(),
      workspace_paused_reason: 'paused by recruiter',
      workspace_paused_by: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
    });

    render(
      <AgentHeader
        title="Role"
        agent={agent}
        onPauseAgent={onPauseRole}
        onResumeAgent={() => {}}
      />,
    );

    expect(screen.getByLabelText('Workspace paused')).toBeInTheDocument();
    expect(screen.getByLabelText(/Paused by Aisha Khan/i)).toBeInTheDocument();
    expect(screen.getByText('This role remains on and will resume automatically.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^resume$/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Pause this role' }));
    expect(onPauseRole).toHaveBeenCalledTimes(1);
  });

  it('labels a locally paused role action beneath a workspace hold', () => {
    const onResumeRole = vi.fn();
    const now = new Date().toISOString();
    const agent = buildAgentPropFromStatus({
      enabled: true,
      paused: true,
      pause_scope: 'workspace',
      paused_at: now,
      paused_reason: 'paused by recruiter',
      paused_by: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
      role_paused_at: now,
      role_paused_reason: 'paused by recruiter',
      role_paused_by: {
        user_id: 11,
        name: 'Jade Malik',
        is_current_user: false,
        changed_at: now,
      },
      workspace_paused: true,
      workspace_paused_at: now,
      workspace_paused_reason: 'paused by recruiter',
      workspace_paused_by: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
    });

    render(<AgentHeader title="Role" agent={agent} onResumeAgent={onResumeRole} />);

    expect(screen.getByText(/Will remain paused after workspace resumes · Paused by Jade Malik/i))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Resume role later' }));
    expect(onResumeRole).toHaveBeenCalledTimes(1);
  });

  it('keeps workspace controls visible but owner-gated for non-owners', () => {
    const agent = buildAgentPropFromStatus({
      workspace_paused: true,
      workspace_control_version: 2,
      workspace_paused_reason: 'paused by recruiter',
      workspace_paused_by: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
    }, { isEnabled: true, controlScope: 'workspace' });

    render(<AgentHeader title="Jobs" agent={agent} />);

    expect(screen.getByRole('button', { name: 'Resume workspace' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Resume workspace' })).toHaveAttribute(
      'title',
      'Workspace owners can pause or resume all agents.',
    );
    expect(screen.getByRole('button', { name: 'Resume workspace' })).toHaveAttribute(
      'aria-description',
      'Workspace owners can pause or resume all agents.',
    );
  });

  it('retains the named workspace actor after that team member is removed', () => {
    const agent = buildAgentPropFromStatus({
      workspace_paused: true,
      workspace_control_version: 7,
      workspace_paused_at: new Date().toISOString(),
      workspace_paused_reason: 'workspace paused by recruiter',
      workspace_paused_by: {
        user_id: null,
        name: 'Jade Malik',
        is_current_user: false,
        attribution: 'unavailable',
        source: 'workspace_control',
      },
    }, { isEnabled: true, controlScope: 'workspace' });

    render(<AgentHeader title="Jobs" agent={agent} />);

    expect(screen.getByLabelText(/Paused by Jade Malik \(former team member\)/i)).toHaveAttribute(
      'title',
      expect.stringMatching(/actor snapshot/i),
    );
    expect(screen.queryByText(/owner not recorded/i)).not.toBeInTheDocument();
  });

  it('names a uniquely inferable legacy actor without presenting it as verified', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={{
          ...runningAgent,
          on: false,
          paused: true,
          pausedAt: '2026-06-01T15:21:42Z',
          pausedReason: 'paused by recruiter',
          pausedBy: {
            user_id: 1,
            name: 'Sam Patel',
            is_current_user: true,
            changed_at: '2026-06-01T15:21:42Z',
            attribution: 'inferred',
            source: 'legacy_unique_member',
          },
        }}
        onResumeAgent={() => {}}
      />,
    );

    const attribution = screen.getByLabelText(/Paused by Sam Patel \(you\)/i);
    expect(attribution).toHaveAttribute('title', expect.stringMatching(/only member present at the time/i));
  });

  it('keeps paused AI spend labelled and available to assistive technology', () => {
    render(
      <AgentHeader
        title="Jobs"
        agent={{
          ...runningAgent,
          on: false,
          paused: true,
          spentCents: 4313,
          pausedReason: 'paused by recruiter',
          pausedBy: { user_id: 7, name: 'Sam Patel', is_current_user: true },
        }}
        onResumeAgent={() => {}}
      />,
    );

    expect(screen.getByText('AI spend')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'AI spend $43.13 of $50' }))
      .toHaveAttribute('aria-valuenow', '86');
  });

  it('keeps new status and attribution semantics immediate while the visual state transitions', () => {
    const { rerender } = render(
      <AgentHeader title="Jobs" agent={runningAgent} onPauseAgent={() => {}} />,
    );

    rerender(
      <AgentHeader
        title="Jobs"
        agent={{
          ...runningAgent,
          on: false,
          paused: true,
          pending: 148,
          pausedReason: 'paused by recruiter',
          pausedBy: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
        }}
        onResumeAgent={() => {}}
      />,
    );

    expect(screen.getByLabelText('Agent paused')).toBeInTheDocument();
    expect(screen.getByLabelText('148 items awaiting review')).toBeInTheDocument();
    expect(screen.getByLabelText('Paused by Aisha Khan')).toBeInTheDocument();
  });

  it('settles count and copy changes directly at their final values under reduced motion', () => {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: String(query).includes('prefers-reduced-motion'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }));

    const { rerender } = render(
      <AgentHeader title="Jobs" agent={runningAgent} onPauseAgent={() => {}} />,
    );
    rerender(
      <AgentHeader
        title="Jobs"
        agent={{
          ...runningAgent,
          on: false,
          paused: true,
          pending: 148,
          pausedReason: 'paused by recruiter',
          pausedBy: { user_id: 9, name: 'Aisha Khan', is_current_user: false },
        }}
        onResumeAgent={() => {}}
      />,
    );

    expect(screen.getByLabelText('148 items awaiting review')).toHaveTextContent('148 to review');
    expect(screen.getByLabelText('Paused by Aisha Khan')).toHaveTextContent('Paused by Aisha Khan');
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

    const pending = screen.getByLabelText(
      '176 awaiting review: 175 candidate decisions and 1 agent question',
    );
    expect(pending).toHaveAttribute(
      'aria-label',
      '176 awaiting review: 175 candidate decisions and 1 agent question',
    );
    expect(pending).toHaveTextContent('176 to review');
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

  it('keeps role controls read-only with the hiring-team permission explanation', () => {
    const reason = 'Only workspace owners, hiring managers, and recruiters assigned to this role can change its agent controls.';
    const onSettings = vi.fn();
    const { rerender } = render(
      <AgentHeader
        title="Role"
        agent={runningAgent}
        onPauseAgent={() => {}}
        onTurnOffAgent={() => {}}
        onAgentSettings={onSettings}
        controlsDisabledReason={reason}
      />,
    );

    expect(screen.getByRole('button', { name: /^pause$/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^pause$/i })).toHaveAttribute('aria-description', reason);
    expect(screen.getByRole('button', { name: /turn off agent/i })).toBeDisabled();
    const settings = screen.getByRole('button', { name: 'Configure agent' });
    expect(settings).not.toBeDisabled();
    expect(settings.getAttribute('title')).toContain('read-only');
    fireEvent.click(settings);
    expect(onSettings).toHaveBeenCalledTimes(1);

    rerender(
      <AgentHeader
        title="Role"
        agent={{ ...runningAgent, on: false, paused: false }}
        onActivateAgent={() => {}}
        controlsDisabledReason={reason}
      />,
    );
    expect(screen.getByRole('button', { name: /turn on/i })).toBeDisabled();
    expect(screen.getByRole('spinbutton', { name: 'Role monthly budget in USD' })).toBeDisabled();
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

    it('acknowledges a bulk pause immediately and locks both controls', () => {
      render(
        <AgentHeader
          title="Jobs"
          agent={{ ...runningAgent, controlAction: 'pause' }}
          onPauseAgent={() => {}}
          onResumeAgent={() => {}}
          pauseAllCount={1}
          resumeAllCount={2}
        />,
      );
      expect(screen.getByRole('button', { name: /pausing/i })).toBeDisabled();
      expect(screen.getByRole('button', { name: /^resume$/i })).toBeDisabled();
    });
  });
});
