import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../shared/api', () => ({
  roles: {
    list: vi.fn(),
    star: vi.fn(),
    unstar: vi.fn(),
    create: vi.fn(),
    uploadJobSpec: vi.fn(),
    addTask: vi.fn(),
  },
  organizations: {
    get: vi.fn(),
    syncWorkable: vi.fn(),
    getWorkableSyncStatus: vi.fn(),
    syncBullhorn: vi.fn(),
    getBullhornSyncStatus: vi.fn(),
  },
  tasks: {
    list: vi.fn(),
  },
  agent: {
    status: vi.fn(),
    rolesBreakdown: vi.fn(),
    orgStatus: vi.fn(),
    pauseAll: vi.fn(),
    resumeAll: vi.fn(),
  },
}));

vi.mock('../../contexts/JobStatusContext', () => ({
  useJobStatus: vi.fn(),
}));

import * as apiClient from '../../shared/api';
import AuthContext from '../../context/AuthContext';
import { useJobStatus } from '../../contexts/JobStatusContext';
import { MotionSystemProvider } from '../../shared/motion';
import { JobsPage, rollupRolesByStatus } from './JobsPage';

// matchMedia is absent in jsdom; stub it so useReducedMotionSync can read a
// deterministic prefers-reduced-motion value per test.
const setReducedMotion = (reduce) => {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: query.includes('reduce') ? reduce : false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
};

const baseRoles = [
  {
    id: 101,
    name: 'Backend Engineer',
    source: 'workable',
    stage_counts: {
      applied: 3, invited: 1, in_assessment: 1, review: 0, advanced: 2, rejected: 4,
    },
    active_candidates_count: 5,
  },
];

const baseOrg = {
  id: 1,
  name: 'Deeplight AI',
  slug: 'deeplight-ai',
  workable_connected: true,
  workable_subdomain: 'deeplight',
  workable_config: { sync_interval_minutes: 30 },
  workable_last_sync_at: '2026-04-25T13:00:00Z',
  workable_last_sync_status: 'success',
  workable_last_sync_summary: { jobs_seen: 79, candidates_seen: 83217, errors: [] },
};

describe('JobsPage Workable sync states', () => {
  const trackWorkableSync = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // Default to reduced motion so the per-stage count-up tickers render their
    // final values synchronously (jsdom has no rAF-driven layout). Motion-
    // specific tests below override this to exercise the entrance animations.
    setReducedMotion(true);
    apiClient.roles.list.mockResolvedValue({ data: baseRoles });
    apiClient.organizations.get.mockResolvedValue({ data: baseOrg });
    apiClient.tasks.list.mockResolvedValue({ data: [] });
    apiClient.agent.status.mockResolvedValue({ data: {} });
    apiClient.agent.rolesBreakdown.mockResolvedValue({ data: [] });
    apiClient.agent.orgStatus.mockResolvedValue({
      data: { org_budget_spent_cents: 4200, org_budget_cap_cents: 9000 },
    });
    trackWorkableSync.mockReset();
    useJobStatus.mockReturnValue({
      workableSyncJob: null,
      bullhornSyncJob: null,
      trackWorkableSync,
      trackBullhornSync: vi.fn(),
    });
  });

  it('reattaches to an active sync on first load', async () => {
    useJobStatus.mockReturnValue({
      trackWorkableSync,
      workableSyncJob: {
        run_id: 77,
        sync_in_progress: true,
        workable_last_sync_at: '2026-04-25T13:00:00Z',
        workable_last_sync_status: 'running',
        workable_last_sync_summary: { jobs_seen: 80, candidates_seen: 83217, errors: [] },
      },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    expect(await screen.findByRole('button', { name: /Syncing/i })).toBeDisabled();
    // The context's active job can paint the Syncing state before the async
    // organization load confirms that Workable is connected. Reattachment is
    // intentionally gated on that confirmation, so wait for the effect rather
    // than treating the already-visible status as proof it has run.
    await waitFor(() => {
      expect(trackWorkableSync).toHaveBeenCalledTimes(1);
    });
  });

  it('treats an already-running sync response as active work instead of an error', async () => {
    apiClient.organizations.getWorkableSyncStatus
      .mockResolvedValueOnce({
        data: {
          run_id: null,
          sync_in_progress: false,
          workable_last_sync_at: '2026-04-25T13:00:00Z',
          workable_last_sync_status: 'success',
          workable_last_sync_summary: { jobs_seen: 79, candidates_seen: 83217, errors: [] },
        },
      })
      .mockResolvedValue({
        data: {
          run_id: 88,
          sync_in_progress: true,
          workable_last_sync_at: '2026-04-25T13:00:00Z',
          workable_last_sync_status: 'success',
          workable_last_sync_summary: { jobs_seen: 80, candidates_seen: 83217, errors: [] },
        },
      });
    apiClient.organizations.syncWorkable.mockResolvedValue({
      data: {
        status: 'already_running',
        run_id: 88,
        message: 'A sync is already in progress (run_id=88). Polling the existing background run instead of starting a new one.',
      },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    fireEvent.click(await screen.findByRole('button', { name: /^Sync now$/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Syncing/i })).toBeDisabled();
    });
    expect(screen.queryByText('Workable sync could not be started.')).not.toBeInTheDocument();
    expect(trackWorkableSync).toHaveBeenCalled();
  });

  it('surfaces the advanced and rejected counts on the job card', async () => {
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: {
        run_id: null,
        sync_in_progress: false,
        workable_last_sync_at: '2026-04-25T13:00:00Z',
        workable_last_sync_status: 'success',
        workable_last_sync_summary: { jobs_seen: 79, candidates_seen: 83217, errors: [] },
      },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const advancedCell = (await screen.findByText('Advanced')).closest('.js-cell');
    expect(within(advancedCell).getByText('2')).toBeInTheDocument();
    const rejectedCell = screen.getByText('Rejected').closest('.js-cell');
    expect(within(rejectedCell).getByText('4')).toBeInTheDocument();
  });

  it('routes "+ Create job" to the job-creation flow', async () => {
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: {
        run_id: null,
        sync_in_progress: false,
        workable_last_sync_at: '2026-04-25T13:00:00Z',
        workable_last_sync_status: 'success',
        workable_last_sync_summary: { jobs_seen: 79, candidates_seen: 83217, errors: [] },
      },
    });

    const onNavigate = vi.fn();
    render(<MemoryRouter><JobsPage onNavigate={onNavigate} /></MemoryRouter>);

    fireEvent.click(await screen.findByRole('button', { name: '+ Create job' }));

    expect(onNavigate).toHaveBeenCalledWith('requisitions');
  });

  it('links to the organization job board beside Create job', async () => {
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: {
        run_id: null,
        sync_in_progress: false,
        workable_last_sync_at: '2026-04-25T13:00:00Z',
        workable_last_sync_status: 'success',
        workable_last_sync_summary: {},
      },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const jobBoardLink = await screen.findByRole('link', { name: 'Job board' });
    expect(jobBoardLink).toHaveAttribute('href', '/careers/deeplight-ai');
    expect(jobBoardLink).toHaveAttribute('target', '_blank');
    expect(jobBoardLink).toHaveAttribute('rel', 'noreferrer');
    expect(screen.getByRole('button', { name: '+ Create job' })).toBeInTheDocument();
  });

  it('shows AGENT PAUSED (not AGENT ON) for an enabled-but-paused role', async () => {
    // Soft pause keeps agentic_mode_enabled=true and stamps agent_paused_at.
    apiClient.roles.list.mockResolvedValue({
      data: [{
        ...baseRoles[0],
        agentic_mode_enabled: true,
        agent_paused_at: '2026-05-30T18:53:00Z',
        monthly_usd_budget_cents: 5000,
      }],
    });
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: { run_id: null, sync_in_progress: false, workable_last_sync_at: '2026-04-25T13:00:00Z', workable_last_sync_status: 'success', workable_last_sync_summary: {} },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    // The role card now shows the unified agent pill — "PAUSED" (amber),
    // not the dark-purple "ON" pill — for an enabled-but-paused role.
    expect(await screen.findByText('PAUSED')).toBeInTheDocument();
    expect(document.querySelector('.job-agent-pill.is-on')).toBeNull();
  });

  it('keeps the global control separate from each role state', async () => {
    localStorage.setItem('taali_user', JSON.stringify({ id: 7, organization_id: 701 }));
    apiClient.roles.list.mockResolvedValue({
      data: [
        {
          ...baseRoles[0],
          id: 201,
          name: 'Ready Role',
          agentic_mode_enabled: true,
          agent_paused_at: null,
        },
        {
          ...baseRoles[0],
          id: 202,
          name: 'Locally Paused Role',
          agentic_mode_enabled: true,
          agent_paused_at: '2026-07-14T10:00:00Z',
        },
        {
          ...baseRoles[0],
          id: 203,
          name: 'Off Role',
          agentic_mode_enabled: false,
          agent_paused_at: null,
        },
      ],
    });
    apiClient.agent.orgStatus.mockResolvedValue({
      data: {
        active_role_count: 0,
        paused_role_count: 2,
        local_paused_role_count: 2,
        workspace_paused: false,
        workspace_control_version: 12,
        paused_reason: 'paused by workspace control',
        pending_decisions: 4,
        org_budget_spent_cents: 100,
        org_budget_cap_cents: 5000,
      },
    });
    apiClient.agent.resumeAll.mockResolvedValue({ data: { affected: 1, enabled_count: 2 } });

    render(
      <AuthContext.Provider value={{ user: { id: 7, role: 'owner' } }}>
        <MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>
      </AuthContext.Provider>,
    );

    expect(await screen.findByLabelText('All agents paused')).toBeInTheDocument();
    const readyCard = screen.getByText('Ready Role').closest('.job-card');
    const locallyPausedCard = screen.getByText('Locally Paused Role').closest('.job-card');
    const offCard = screen.getByText('Off Role').closest('.job-card');
    expect(within(readyCard).getByText('ON')).toBeInTheDocument();
    expect(within(locallyPausedCard).getByText('PAUSED')).toBeInTheDocument();
    expect(within(offCard).getByText('OFF')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Resume workspace' }));
    await waitFor(() => expect(apiClient.agent.resumeAll).toHaveBeenCalledWith(12));
    expect(apiClient.agent.pauseAll).not.toHaveBeenCalled();
  });

  it('shows workspace status to members without exposing a mutation', async () => {
    localStorage.setItem('taali_user', JSON.stringify({ id: 8, organization_id: 702 }));
    apiClient.roles.list.mockResolvedValue({
      data: [{ ...baseRoles[0], agentic_mode_enabled: true }],
    });
    apiClient.agent.orgStatus.mockResolvedValue({
      data: {
        active_role_count: 0,
        paused_role_count: 1,
        local_paused_role_count: 1,
        workspace_paused: false,
        workspace_control_version: 3,
        paused_reason: 'paused by workspace control',
      },
    });

    render(
      <AuthContext.Provider value={{ user: { id: 8, role: 'member' } }}>
        <MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>
      </AuthContext.Provider>,
    );

    expect(await screen.findByLabelText('All agents paused')).toBeInTheDocument();
    const resume = screen.getByRole('button', { name: 'Resume workspace' });
    expect(resume).toBeDisabled();
    expect(resume).toHaveAttribute('title', 'Workspace owners can pause or resume all agents.');
    expect(resume).toHaveAttribute('aria-description', 'Workspace owners can pause or resume all agents.');
    expect(apiClient.agent.resumeAll).not.toHaveBeenCalled();
  });

  it('refetches and explains a stale workspace control version', async () => {
    localStorage.setItem('taali_user', JSON.stringify({ id: 7, organization_id: 703 }));
    apiClient.roles.list.mockResolvedValue({
      data: [{ ...baseRoles[0], agentic_mode_enabled: true }],
    });
    const initial = {
      active_role_count: 1,
      paused_role_count: 0,
      workspace_paused: false,
      workspace_control_version: 4,
    };
    const latest = {
      active_role_count: 0,
      paused_role_count: 1,
      local_paused_role_count: 1,
      workspace_paused: false,
      workspace_control_version: 5,
      paused_reason: 'paused by workspace control',
    };
    apiClient.agent.orgStatus
      .mockResolvedValueOnce({ data: initial })
      .mockResolvedValue({ data: latest });
    apiClient.agent.pauseAll.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            current: {
              changed_by: { action: 'paused', name: 'Aisha Khan', is_current_user: false },
            },
          },
        },
      },
    });

    render(
      <AuthContext.Provider value={{ user: { id: 7, role: 'owner' } }}>
        <MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>
      </AuthContext.Provider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Pause workspace' }));
    await waitFor(() => expect(apiClient.agent.pauseAll).toHaveBeenCalledWith(4));
    expect(await screen.findByText(/workspace agent was paused by Aisha Khan/i)).toBeInTheDocument();
    expect(await screen.findByLabelText('All agents paused')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume workspace' })).not.toBeDisabled();
  });

  it('accepts a pause click when the cached status predates the control version', async () => {
    localStorage.setItem('taali_user', JSON.stringify({ id: 7, organization_id: 705 }));
    apiClient.roles.list.mockResolvedValue({
      data: [{ ...baseRoles[0], agentic_mode_enabled: true }],
    });
    const incomplete = {
      active_role_count: 1,
      paused_role_count: 0,
      workspace_paused: false,
    };
    const current = { ...incomplete, workspace_control_version: 6 };
    const paused = {
      active_role_count: 0,
      paused_role_count: 1,
      local_paused_role_count: 1,
      workspace_paused: false,
      workspace_control_version: 7,
      paused_reason: 'paused by workspace control',
    };
    apiClient.agent.orgStatus
      .mockReset()
      .mockResolvedValueOnce({ data: incomplete })
      .mockResolvedValueOnce({ data: current })
      .mockResolvedValue({ data: paused });
    apiClient.agent.pauseAll.mockResolvedValue({
      data: { workspace_paused: false, workspace_control_version: 7 },
    });

    render(
      <AuthContext.Provider value={{ user: { id: 7, role: 'owner' } }}>
        <MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>
      </AuthContext.Provider>,
    );

    const pause = await screen.findByRole('button', { name: 'Pause workspace' });
    expect(pause).not.toBeDisabled();
    fireEvent.click(pause);

    await waitFor(() => expect(apiClient.agent.pauseAll).toHaveBeenCalledWith(6));
    expect(await screen.findByRole('button', { name: 'Resume workspace' })).toBeInTheDocument();
  });

  it('does not reuse a pre-mutation workspace poll after Pause succeeds', async () => {
    localStorage.setItem('taali_user', JSON.stringify({ id: 7, organization_id: 704 }));
    apiClient.roles.list.mockResolvedValue({
      data: [{ ...baseRoles[0], agentic_mode_enabled: true }],
    });
    const initial = {
      active_role_count: 1,
      paused_role_count: 0,
      workspace_paused: false,
      workspace_control_version: 10,
    };
    const latest = {
      active_role_count: 0,
      paused_role_count: 1,
      local_paused_role_count: 1,
      workspace_paused: false,
      workspace_control_version: 11,
      paused_reason: 'paused by workspace control',
    };
    let resolveOldPoll;
    apiClient.agent.orgStatus
      .mockReset()
      .mockResolvedValueOnce({ data: initial })
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveOldPoll = resolve;
      }))
      .mockResolvedValueOnce({ data: latest });
    apiClient.agent.pauseAll.mockReset().mockResolvedValue({
      data: { workspace_paused: false, workspace_control_version: 11 },
    });

    render(
      <AuthContext.Provider value={{ user: { id: 7, role: 'owner' } }}>
        <MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>
      </AuthContext.Provider>,
    );

    const pause = await screen.findByRole('button', { name: 'Pause workspace' });
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await waitFor(() => expect(apiClient.agent.orgStatus).toHaveBeenCalledTimes(2));

    fireEvent.click(pause);
    await waitFor(() => expect(apiClient.agent.pauseAll).toHaveBeenCalledWith(10));
    // Forced reconciliation bypasses the still-pending request that began
    // before the mutation, instead of joining it and staying stale for 30s.
    await waitFor(() => expect(apiClient.agent.orgStatus).toHaveBeenCalledTimes(3));
    expect(await screen.findByLabelText('All agents paused')).toBeInTheDocument();

    await act(async () => {
      resolveOldPoll({ data: initial });
    });
    expect(screen.getByRole('button', { name: 'Resume workspace' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pause workspace' })).not.toBeInTheDocument();
  });

  it('keeps durable Turn-on progress visible without presenting the agent as ON', async () => {
    apiClient.roles.list.mockResolvedValue({
      data: [
        {
          ...baseRoles[0],
          id: 110,
          name: 'Queued Role',
          agentic_mode_enabled: false,
          assessment_task_provisioning: {
            activation_intent: { status: 'pending', last_error: null },
          },
        },
        {
          ...baseRoles[0],
          id: 111,
          name: 'Blocked Role',
          agentic_mode_enabled: false,
          assessment_task_provisioning: {
            activation_intent: {
              status: 'blocked',
              last_error: 'Confirm the preserved assessment task.',
            },
          },
        },
      ],
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const queuedCard = (await screen.findByText('Queued Role')).closest('.job-card');
    const blockedCard = screen.getByText('Blocked Role').closest('.job-card');
    expect(within(queuedCard).getByText('TURN-ON QUEUED')).toBeInTheDocument();
    expect(within(queuedCard).queryByText(/^ON/)).not.toBeInTheDocument();
    expect(within(blockedCard).getByText('NEEDS INPUT')).toHaveAttribute(
      'title',
      'Confirm the preserved assessment task.',
    );
    expect(document.querySelectorAll('.job-agent-pill.is-on')).toHaveLength(0);
  });

  it('preserves the deterministic server agent-first order instead of re-sorting each snapshot', async () => {
    apiClient.roles.list.mockResolvedValue({
      data: [
        {
          ...baseRoles[0],
          id: 401,
          name: 'Zulu Engineer',
          source: 'manual',
          job_status: 'open',
          agentic_mode_enabled: true,
        },
        {
          ...baseRoles[0],
          id: 402,
          name: 'Aardvark Archived',
          workable_job_state: 'archived',
        },
        {
          ...baseRoles[0],
          id: 403,
          name: 'alpha Engineer',
          source: 'manual',
          job_status: 'open',
        },
        {
          ...baseRoles[0],
          id: 404,
          name: 'Middle Engineer',
          workable_job_state: 'published',
        },
      ],
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    await screen.findByText('Zulu Engineer');
    expect(
      Array.from(document.querySelectorAll('.job-card .role-name'), (node) => node.textContent),
    ).toEqual(['Zulu Engineer', 'alpha Engineer', 'Middle Engineer']);
    expect(apiClient.roles.list).toHaveBeenCalledWith(expect.objectContaining({ sort_by: 'agent_on_name' }));
    expect(screen.queryByText('Aardvark Archived')).not.toBeInTheDocument();
  });

  it('collapses inactive roles by default and expands them as compact cards', async () => {
    apiClient.roles.list.mockResolvedValue({
      data: [
        {
          ...baseRoles[0],
          id: 411,
          name: 'Active Engineer',
          source: 'manual',
          job_status: 'open',
        },
        {
          ...baseRoles[0],
          id: 412,
          name: 'Archived Engineer',
          job_status: 'open',
          workable_job_state: 'archived',
        },
        {
          ...baseRoles[0],
          id: 413,
          name: 'Cancelled Engineer',
          source: 'manual',
          job_status: 'cancelled',
        },
      ],
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    expect(await screen.findByText('Active Engineer')).toBeInTheDocument();
    const inactiveToggle = screen.getByRole('button', {
      name: 'Show archived and inactive roles (2)',
    });
    expect(inactiveToggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Archived Engineer')).not.toBeInTheDocument();
    expect(screen.queryByText('Cancelled Engineer')).not.toBeInTheDocument();

    fireEvent.click(inactiveToggle);

    const collapseInactive = screen.getByRole('button', {
      name: 'Hide archived and inactive roles (2)',
    });
    expect(collapseInactive).toHaveAttribute('aria-expanded', 'true');
    const archivedCard = (await screen.findByText('Archived Engineer')).closest('.job-card');
    const cancelledCard = screen.getByText('Cancelled Engineer').closest('.job-card');
    [archivedCard, cancelledCard].forEach((card) => {
      expect(card).toHaveClass('not-live', 'is-compact');
      expect(card.querySelector('.job-stats')).toBeNull();
      expect(card.querySelector('.job-foot')).toBeNull();
    });
    expect(within(archivedCard).getByText('Archived')).toBeInTheDocument();
    expect(within(archivedCard).queryByText('Open')).toBeNull();
    expect(within(cancelledCard).getByText('Archived')).toBeInTheDocument();

    fireEvent.click(collapseInactive);
    expect(screen.getByRole('button', {
      name: 'Show archived and inactive roles (2)',
    })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Archived Engineer')).not.toBeInTheDocument();
    expect(screen.queryByText('Cancelled Engineer')).not.toBeInTheDocument();
  });

  it.each([
    ['workable', 'archived', 'Archived'],
    ['bullhorn', 'on_hold_client', 'On Hold Client'],
  ])(
    'presents the authoritative %s lifecycle for inactive ATS roles',
    async (provider, externalState, expectedLabel) => {
      apiClient.roles.list.mockResolvedValue({
        data: [{
          ...baseRoles[0],
          id: provider === 'workable' ? 421 : 422,
          name: `${provider} inactive role`,
          source: null,
          ats_provider: provider,
          external_job_id: provider === 'workable' ? 'WK-421' : 'BH-422',
          external_job_state: externalState,
          external_job_live: false,
          // A linked/adopted role can retain this native bridge value. It must
          // never override the provider's lifecycle in the catalogue.
          job_status: 'open',
        }],
      });

      render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

      fireEvent.click(await screen.findByRole('button', {
        name: 'Show archived and inactive roles (1)',
      }));
      const card = screen.getByText(`${provider} inactive role`).closest('.job-card');
      expect(within(card).getByText(expectedLabel)).toBeInTheDocument();
      expect(within(card).queryByText('Open')).toBeNull();
    },
  );

  it('greys only explicitly non-live ATS roles, independently of agent state', async () => {
    apiClient.roles.list.mockResolvedValue({
      data: [
        {
          ...baseRoles[0],
          id: 101,
          name: 'Published Role',
          workable_job_state: 'published',
          agentic_mode_enabled: true,
        },
        {
          ...baseRoles[0],
          id: 102,
          name: 'Closed Role',
          workable_job_state: 'closed',
          agentic_mode_enabled: true,
        },
        {
          ...baseRoles[0],
          id: 103,
          name: 'Manual Role',
          source: 'manual',
          workable_job_state: null,
        },
        {
          ...baseRoles[0],
          id: 104,
          name: 'Paused Published Role',
          workable_job_state: 'published',
          agentic_mode_enabled: true,
          agent_paused_at: '2026-05-30T18:53:00Z',
        },
        {
          ...baseRoles[0],
          id: 105,
          name: 'Unknown Workable State',
          workable_job_state: null,
        },
        {
          ...baseRoles[0],
          id: 106,
          name: 'Inactive Sister Role',
          source: 'manual',
          role_kind: 'sister',
          ats_owner_role_id: 302,
          ats_owner_role_name: 'Original Data Role',
          role_family: {
            owner: { id: 302, name: 'Original Data Role' },
            related: [{ id: 106, name: 'Inactive Sister Role' }],
          },
          workable_job_state: null,
          workable_job_live: false,
        },
      ],
    });
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: { run_id: null, sync_in_progress: false, workable_last_sync_at: '2026-04-25T13:00:00Z', workable_last_sync_status: 'success', workable_last_sync_summary: {} },
    });
    const onNavigate = vi.fn();

    render(<MemoryRouter><JobsPage onNavigate={onNavigate} /></MemoryRouter>);

    const publishedCard = (await screen.findByText('Published Role')).closest('.job-card');
    const manualCard = screen.getByText('Manual Role').closest('.job-card');
    const pausedPublishedCard = screen.getByText('Paused Published Role').closest('.job-card');
    const unknownStateCard = screen.getByText('Unknown Workable State').closest('.job-card');
    expect(screen.queryByText('Closed Role')).not.toBeInTheDocument();
    expect(screen.queryByText('Inactive Sister Role')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {
      name: 'Show archived and inactive roles (2)',
    }));
    const closedCard = (await screen.findByText('Closed Role')).closest('.job-card');
    const inactiveSisterCard = screen.getByText('Inactive Sister Role').closest('.job-card');

    expect(closedCard).toHaveClass('not-live');
    expect(closedCard).toHaveClass('is-compact');
    expect(closedCard).toHaveClass('agent-on');
    expect(publishedCard).not.toHaveClass('not-live');
    expect(manualCard).not.toHaveClass('not-live');
    expect(pausedPublishedCard).not.toHaveClass('not-live');
    expect(unknownStateCard).not.toHaveClass('not-live');
    expect(inactiveSisterCard).toHaveClass('not-live');
    expect(inactiveSisterCard).toHaveClass('is-compact');
    expect(within(inactiveSisterCard).getByText('Shared pool · Original: Original Data Role #302'))
      .toBeInTheDocument();
    expect(publishedCard).toHaveClass('agent-on');
    expect(pausedPublishedCard).not.toHaveClass('agent-on');
    expect(publishedCard).toHaveStyle({ opacity: '1' });
    expect(manualCard).toHaveStyle({ opacity: '1' });
    expect(pausedPublishedCard).toHaveStyle({ opacity: '1' });
    expect(unknownStateCard).toHaveStyle({ opacity: '1' });

    expect(closedCard).toHaveAttribute('role', 'button');
    expect(closedCard).toHaveAttribute('tabindex', '0');
    expect(closedCard).not.toHaveAttribute('aria-disabled');
    fireEvent.click(closedCard);
    expect(onNavigate).toHaveBeenCalledWith('job-pipeline', { roleId: 102 });
  });

  it('marks each role as Workable or Full ATS on its card', async () => {
    apiClient.roles.list.mockResolvedValue({
      data: [
        { ...baseRoles[0], id: 101, name: 'Synced Role', source: 'workable' },
        { ...baseRoles[0], id: 103, name: 'Native Role', source: 'manual', workable_job_state: null },
      ],
    });
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: { run_id: null, sync_in_progress: false, workable_last_sync_at: '2026-04-25T13:00:00Z', workable_last_sync_status: 'success', workable_last_sync_summary: {} },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const nativeCard = (await screen.findByText('Native Role')).closest('.job-card');
    const syncedCard = screen.getByText('Synced Role').closest('.job-card');
    // The native role reads as a first-class "Full ATS" identity, not a
    // generic 'Role' chip.
    expect(within(nativeCard).getByText('Full ATS')).toBeInTheDocument();
    expect(within(nativeCard).queryByText('Role')).toBeNull();
    expect(within(syncedCard).getByText('Workable')).toBeInTheDocument();
    expect(within(syncedCard).queryByText('Full ATS')).toBeNull();
  });

  it.each([
    ['workable', 'Workable', 'WK-900'],
    ['bullhorn', 'Bullhorn', 'BH-900'],
  ])(
    'classifies a provider-neutral %s role under its own source filter',
    async (provider, label, externalJobId) => {
      apiClient.roles.list.mockResolvedValue({
        data: [{
          ...baseRoles[0],
          id: provider === 'workable' ? 301 : 302,
          name: `${label} Neutral Role`,
          source: null,
          ats_provider: provider,
          external_job_id: externalJobId,
          external_job_state: 'open',
          external_job_live: true,
          job_status: 'cancelled',
        }],
      });
      apiClient.organizations.get.mockResolvedValue({
        data: provider === 'bullhorn'
          ? {
            id: 1,
            name: 'Deeplight AI',
            active_ats: 'bullhorn',
            bullhorn_connected: true,
            bullhorn_last_sync_at: '2026-04-25T13:00:00Z',
            bullhorn_last_sync_status: 'success',
          }
          : { ...baseOrg, active_ats: 'workable' },
      });

      render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

      const roleCard = (await screen.findByText(`${label} Neutral Role`)).closest('.job-card');
      expect(within(roleCard).getByText(label)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: new RegExp(`^From ${label}1$`, 'i') })).toBeInTheDocument();
      expect(roleCard).not.toHaveClass('not-live');
      expect(within(roleCard).getByText('Open')).toBeInTheDocument();
      expect(within(roleCard).queryByText('Archived')).toBeNull();
    },
  );

  it('rolls up ATS and Full ATS roles using their authoritative lifecycle', () => {
    expect(rollupRolesByStatus([
      {
        ...baseRoles[0],
        id: 431,
        source: null,
        ats_provider: 'bullhorn',
        external_job_id: 'BH-431',
        external_job_state: 'open',
        external_job_live: true,
        job_status: 'cancelled',
      },
      {
        ...baseRoles[0],
        id: 432,
        source: null,
        ats_provider: 'workable',
        external_job_id: 'WK-432',
        external_job_state: 'archived',
        external_job_live: false,
        job_status: 'open',
      },
      {
        ...baseRoles[0],
        id: 433,
        source: 'manual',
        ats_provider: null,
        job_status: null,
      },
    ])).toEqual({
      active: 2,
      filled: 0,
      filled_external: 0,
      cancelled: 1,
      total: 3,
    });
  });

  it('runs Bullhorn sync through the same Jobs hub control', async () => {
    const trackBullhornSync = vi.fn();
    apiClient.roles.list.mockResolvedValue({
      data: [{
        ...baseRoles[0],
        source: null,
        ats_provider: 'bullhorn',
        external_job_id: 'BH-900',
        external_job_live: true,
      }],
    });
    apiClient.organizations.get.mockResolvedValue({
      data: {
        id: 1,
        name: 'Deeplight AI',
        active_ats: 'bullhorn',
        bullhorn_connected: true,
        bullhorn_last_sync_at: '2026-04-25T13:00:00Z',
        bullhorn_last_sync_status: 'success',
        bullhorn_last_sync_summary: { candidates_upserted: 4 },
      },
    });
    apiClient.organizations.syncBullhorn.mockResolvedValue({
      data: { status: 'started', run_id: 55 },
    });
    useJobStatus.mockReturnValue({
      workableSyncJob: null,
      bullhornSyncJob: null,
      trackWorkableSync,
      trackBullhornSync,
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    expect(await screen.findByText(/Synced from Bullhorn · 1 role/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Sync now$/i }));

    await waitFor(() => expect(apiClient.organizations.syncBullhorn).toHaveBeenCalledTimes(1));
    expect(apiClient.organizations.syncWorkable).not.toHaveBeenCalled();
    expect(trackBullhornSync).toHaveBeenCalled();
  });

  it('labels a Bullhorn related role with its actual owning provider', async () => {
    apiClient.roles.list.mockResolvedValue({
      data: [{
        ...baseRoles[0],
        id: 303,
        name: 'Bullhorn Related Role',
        source: 'sister',
        role_kind: 'sister',
        ats_owner_role_id: 302,
        ats_owner_role_name: 'Original Bullhorn Role',
        ats_provider: 'bullhorn',
        external_job_id: 'BH-900',
        external_job_live: true,
      }],
    });
    apiClient.organizations.get.mockResolvedValue({
      data: {
        id: 1,
        active_ats: 'bullhorn',
        bullhorn_connected: true,
        bullhorn_last_sync_status: 'success',
      },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const roleCard = (await screen.findByText('Bullhorn Related Role')).closest('.job-card');
    const familyGroup = roleCard.closest('.job-family-group');
    expect(within(roleCard).getByText('Related · Bullhorn')).toBeInTheDocument();
    expect(within(familyGroup).getByText('Shared candidate pool')).toBeInTheDocument();
    expect(within(familyGroup).getByText('Original Bullhorn Role #302 · Bullhorn Related Role #303'))
      .toBeInTheDocument();
    expect(within(roleCard).queryByText('Shared candidate pool')).not.toBeInTheDocument();
    expect(within(roleCard).queryByText(/in Workable/i)).not.toBeInTheDocument();
  });

  it('keeps an original and its related full cards together with exact role references', async () => {
    const roleFamily = {
      owner: { id: 501, name: 'Data Engineer' },
      related: [{ id: 503, name: 'Alternative Data Engineer' }],
    };
    apiClient.roles.list.mockResolvedValue({
      data: [
        { ...baseRoles[0], id: 501, name: 'Data Engineer', sister_role_count: 1, role_family: roleFamily },
        { ...baseRoles[0], id: 502, name: 'Middle Standalone Role' },
        {
          ...baseRoles[0],
          id: 503,
          name: 'Alternative Data Engineer',
          role_kind: 'sister',
          source: 'sister',
          ats_owner_role_id: 501,
          ats_owner_role_name: 'Data Engineer',
          role_family: roleFamily,
        },
      ],
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    await screen.findByText('Data Engineer');
    expect(
      Array.from(document.querySelectorAll('.job-card .role-name'), (node) => node.textContent),
    ).toEqual(['Data Engineer', 'Alternative Data Engineer', 'Middle Standalone Role']);

    const originalCard = screen.getByText('Data Engineer', { selector: '.role-name' }).closest('.job-card');
    const relatedCard = screen.getByText('Alternative Data Engineer', { selector: '.role-name' }).closest('.job-card');
    const familyGroup = originalCard.closest('.job-family-group');
    expect(familyGroup).toHaveClass('is-size-2');
    expect(familyGroup).toHaveAttribute('data-family-size', '2');
    expect(familyGroup).toContainElement(relatedCard);
    expect(familyGroup).not.toContainElement(
      screen.getByText('Middle Standalone Role', { selector: '.role-name' }).closest('.job-card'),
    );
    expect(within(familyGroup).getByText('Data Engineer #501 · Alternative Data Engineer #503'))
      .toBeInTheDocument();
    expect(within(familyGroup).getAllByText('Shared candidate pool')).toHaveLength(1);
    expect(familyGroup.querySelector('.job-family-context')).not.toBeInTheDocument();
    expect(within(originalCard).queryByText('Shared candidate pool')).not.toBeInTheDocument();
    expect(within(relatedCard).queryByText('Shared candidate pool')).not.toBeInTheDocument();
    expect(originalCard).toHaveAttribute('data-role-family', '501');
    expect(relatedCard).toHaveAttribute('data-role-family', '501');
  });

  it('never renders a dangling relationship from incomplete family metadata', async () => {
    apiClient.roles.list.mockResolvedValue({
      data: [{
        ...baseRoles[0],
        id: 601,
        name: 'Incomplete Family Owner',
        sister_role_count: 1,
        role_family: {
          owner: { id: 601, name: 'Incomplete Family Owner' },
          related: [{ id: 602, name: null }],
        },
      }],
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const card = (await screen.findByText('Incomplete Family Owner')).closest('.job-card');
    const familyGroup = card.closest('.job-family-group');
    expect(within(familyGroup).getByText('Linked role details unavailable')).toBeInTheDocument();
    expect(within(card).queryByText('Linked role details unavailable')).not.toBeInTheDocument();
    expect(within(card).queryByText(/^Related:\s*$/)).not.toBeInTheDocument();
  });

  it('uses native job_status for Live and Draft filters', async () => {
    apiClient.roles.list.mockResolvedValue({
      data: [
        { ...baseRoles[0], id: 201, name: 'Native Open', source: 'manual', job_status: 'open', workable_job_state: null },
        { ...baseRoles[0], id: 202, name: 'Native Draft', source: 'manual', job_status: 'draft', job_spec_present: true, workable_job_state: null },
        { ...baseRoles[0], id: 203, name: 'Provider Live', source: 'workable', job_status: null, workable_job_state: 'published' },
      ],
    });
    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const nativeOpenCard = (await screen.findByText('Native Open')).closest('.job-card');
    expect(screen.queryByText('Native Draft')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {
      name: 'Show archived and inactive roles (1)',
    }));
    const nativeDraftCard = (await screen.findByText('Native Draft')).closest('.job-card');
    expect(screen.queryByRole('button', { name: 'Filter' })).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Filter jobs' })).toBeInTheDocument();
    expect(nativeOpenCard).not.toHaveClass('not-live');
    expect(nativeDraftCard).toHaveClass('not-live');
    expect(screen.getByRole('button', { name: /^Live2$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Draft1$/i })).toBeInTheDocument();

    const liveFilter = screen.getByRole('button', { name: /^Live2$/i });
    expect(liveFilter).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(liveFilter);
    expect(liveFilter).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Native Open')).toBeInTheDocument();
    expect(screen.getByText('Provider Live')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Native Draft')).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /^Draft1$/i }));
    expect(await screen.findByText('Native Draft')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Native Open')).not.toBeInTheDocument());
    expect(screen.queryByText('Provider Live')).not.toBeInTheDocument();
  });

  it('paints the first page, then swaps in the full role list in the background', async () => {
    // A full first page (== the limit) signals there may be more roles, so the
    // hub follows up with an unlimited fetch and replaces the list. A small org
    // (first page not full) must NOT trigger the background fetch.
    const firstPage = Array.from({ length: 24 }, (_, i) => ({
      ...baseRoles[0], id: 200 + i, name: `Role ${200 + i}`,
    }));
    const fullList = [...firstPage, { ...baseRoles[0], id: 999, name: 'Tail Role Zeta' }];
    apiClient.roles.list.mockImplementation((params) =>
      Promise.resolve({ data: params && params.limit ? firstPage : fullList }),
    );
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: { run_id: null, sync_in_progress: false, workable_last_sync_at: '2026-04-25T13:00:00Z', workable_last_sync_status: 'success', workable_last_sync_summary: {} },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    // First page paints from the limited fetch...
    await screen.findByText('Role 200');
    expect(apiClient.roles.list).toHaveBeenCalledWith({ include_pipeline_stats: true, sort_by: 'agent_on_name', limit: 24 });
    // ...then the background unlimited fetch lands and the tail role appears.
    expect(await screen.findByText('Tail Role Zeta')).toBeInTheDocument();
    expect(apiClient.roles.list).toHaveBeenCalledWith({ include_pipeline_stats: true, sort_by: 'agent_on_name' });
  });

  it('preserves a star mutation when the delayed full list lands', async () => {
    let resolveFullList;
    const firstPage = Array.from({ length: 24 }, (_, index) => ({
      ...baseRoles[0],
      id: 700 + index,
      name: `Native Role ${700 + index}`,
      source: 'manual',
      job_status: 'open',
      is_published: false,
      starred_for_auto_sync: false,
    }));
    apiClient.roles.list.mockImplementation((params) => (
      params?.limit
        ? Promise.resolve({ data: firstPage })
        : new Promise((resolve) => { resolveFullList = resolve; })
    ));
    apiClient.roles.star.mockResolvedValue({
      data: { ...firstPage[0], starred_for_auto_sync: true },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const firstCard = (await screen.findByText('Native Role 700')).closest('.job-card');
    const star = within(firstCard).getByRole('button', {
      name: 'Star role to enable auto-sync and real-time scoring',
    });
    fireEvent.click(star);
    await waitFor(() => expect(star).toHaveAttribute('aria-pressed', 'true'));

    await act(async () => {
      resolveFullList({ data: firstPage });
    });

    expect(within(screen.getByText('Native Role 700').closest('.job-card'))
      .getByRole('button', { name: 'Unstar role (stop auto-sync)' }))
      .toHaveAttribute('aria-pressed', 'true');
  });

  it('does not background-fetch when the first page is not full', async () => {
    // baseRoles has a single role (< limit) → only the limited call fires.
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: { run_id: null, sync_in_progress: false, workable_last_sync_at: '2026-04-25T13:00:00Z', workable_last_sync_status: 'success', workable_last_sync_summary: {} },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    await screen.findByText('Backend Engineer');
    expect(apiClient.roles.list).toHaveBeenCalledWith({ include_pipeline_stats: true, sort_by: 'agent_on_name', limit: 24 });
    expect(apiClient.roles.list).not.toHaveBeenCalledWith({ include_pipeline_stats: true, sort_by: 'agent_on_name' });
  });
});

describe('JobsPage entrance motion', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setReducedMotion(true);
    apiClient.roles.list.mockResolvedValue({ data: baseRoles });
    apiClient.organizations.get.mockResolvedValue({ data: baseOrg });
    apiClient.tasks.list.mockResolvedValue({ data: [] });
    apiClient.agent.status.mockResolvedValue({ data: {} });
    apiClient.agent.rolesBreakdown.mockResolvedValue({ data: [] });
    apiClient.agent.orgStatus.mockResolvedValue({
      data: { org_budget_spent_cents: 4200, org_budget_cap_cents: 9000 },
    });
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: { run_id: null, sync_in_progress: false, workable_last_sync_at: '2026-04-25T13:00:00Z', workable_last_sync_status: 'success', workable_last_sync_summary: {} },
    });
    useJobStatus.mockReturnValue({
      workableSyncJob: null,
      bullhornSyncJob: null,
      trackWorkableSync: vi.fn(),
      trackBullhornSync: vi.fn(),
    });
  });

  it('uses Motion-native reveals and a capped card stagger on first mount', async () => {
    setReducedMotion(false);
    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    await screen.findByText('Backend Engineer');
    expect(document.querySelector('.wk-strip')).toHaveAttribute('data-motion-reveal', 'vertical');
    expect(document.querySelector('.filter-row')).toHaveAttribute('data-motion-reveal', 'vertical');
    expect(document.querySelectorAll('[data-motion-reveal]').length).toBeGreaterThanOrEqual(3);
    const grid = document.querySelector('.jobs-grid');
    expect(grid).toHaveAttribute('data-motion-stagger');
    expect(grid.querySelector('.job-card')).toHaveAttribute('data-motion-index', '0');
  });

  it('renders the final stage counts immediately under reduced motion', async () => {
    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const advancedCell = (await screen.findByText('Advanced')).closest('.js-cell');
    // No tween under reduced motion: the settled stage_counts render at once.
    expect(within(advancedCell).getByText('2')).toBeInTheDocument();
    const rejectedCell = screen.getByText('Rejected').closest('.js-cell');
    expect(within(rejectedCell).getByText('4')).toBeInTheDocument();
  });

  it('shows a "Live" badge only on roles with a live public job page', async () => {
    apiClient.roles.list.mockResolvedValue({
      data: [
        { ...baseRoles[0], id: 101, name: 'Published Role', is_published: true, workable_job_state: 'published' },
        { ...baseRoles[0], id: 102, name: 'Unpublished Role', is_published: false },
        { ...baseRoles[0], id: 103, name: 'Closed Preview', is_published: true, workable_job_state: 'closed' },
      ],
    });
    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    const liveCard = (await screen.findByText('Published Role')).closest('.job-card');
    const draftCard = screen.getByText('Unpublished Role').closest('.job-card');
    expect(screen.queryByText('Closed Preview')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {
      name: 'Show archived and inactive roles (1)',
    }));
    const closedCard = (await screen.findByText('Closed Preview')).closest('.job-card');
    expect(within(liveCard).getByText('Live')).toBeInTheDocument();
    expect(within(draftCard).queryByText('Live')).not.toBeInTheDocument();
    expect(within(closedCard).queryByText('Live')).not.toBeInTheDocument();
  });

  it('does not re-fire the card stagger when a filter changes', async () => {
    setReducedMotion(false);
    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    await screen.findByText('Backend Engineer');
    const grid = document.querySelector('.jobs-grid');
    await waitFor(() => expect(grid).toHaveAttribute('data-motion-stagger', 'settled'), { timeout: 2000 });

    // Changing a filter re-renders the grid but must NOT re-arm the stagger.
    fireEvent.click(screen.getByRole('button', { name: /With open candidates/i }));
    await screen.findByText('Backend Engineer');
    expect(document.querySelector('.jobs-grid')).toHaveAttribute('data-motion-stagger', 'settled');
  });

  it('keeps filtered cards present for their exit and preserves the surviving card', async () => {
    setReducedMotion(false);
    apiClient.roles.list.mockResolvedValue({
      data: [
        baseRoles[0],
        {
          ...baseRoles[0],
          id: 102,
          name: 'Dormant Role',
          active_candidates_count: 0,
        },
      ],
    });
    render(
      <MotionSystemProvider>
        <MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>
      </MotionSystemProvider>,
    );

    await screen.findByText('Dormant Role');
    const grid = document.querySelector('.jobs-grid');
    const survivingCard = screen.getByText('Backend Engineer').closest('.job-card');
    await waitFor(() => expect(grid).toHaveAttribute('data-motion-stagger', 'settled'), { timeout: 2000 });

    fireEvent.click(screen.getByRole('button', { name: /With open candidates/i }));

    // AnimatePresence retains the removed role just long enough to complete the
    // shared exit, while the keyed survivor remains the same layout node.
    expect(screen.getByText('Dormant Role')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Dormant Role')).not.toBeInTheDocument());
    expect(screen.getByText('Backend Engineer').closest('.job-card')).toBe(survivingCard);
    expect(document.querySelector('.jobs-grid')).toBe(grid);
    expect(grid).toHaveAttribute('data-motion-stagger', 'settled');
  });
});
