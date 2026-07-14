import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../shared/api', () => ({
  roles: {
    list: vi.fn(),
    create: vi.fn(),
    uploadJobSpec: vi.fn(),
    addTask: vi.fn(),
  },
  organizations: {
    get: vi.fn(),
    syncWorkable: vi.fn(),
    getWorkableSyncStatus: vi.fn(),
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
import { useJobStatus } from '../../contexts/JobStatusContext';
import { MotionSystemProvider } from '../../shared/motion';
import { JobsPage } from './JobsPage';

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
    useJobStatus.mockReturnValue({ workableSyncJob: null, trackWorkableSync });
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
    const closedCard = screen.getByText('Closed Role').closest('.job-card');
    const manualCard = screen.getByText('Manual Role').closest('.job-card');
    const pausedPublishedCard = screen.getByText('Paused Published Role').closest('.job-card');
    const unknownStateCard = screen.getByText('Unknown Workable State').closest('.job-card');
    const inactiveSisterCard = screen.getByText('Inactive Sister Role').closest('.job-card');

    expect(closedCard).toHaveClass('not-live');
    expect(closedCard).toHaveClass('agent-on');
    expect(publishedCard).not.toHaveClass('not-live');
    expect(manualCard).not.toHaveClass('not-live');
    expect(pausedPublishedCard).not.toHaveClass('not-live');
    expect(unknownStateCard).not.toHaveClass('not-live');
    expect(inactiveSisterCard).toHaveClass('not-live');
    expect(publishedCard).toHaveClass('agent-on');
    expect(pausedPublishedCard).not.toHaveClass('agent-on');
    await waitFor(() => expect(closedCard).toHaveStyle({ opacity: '0.55' }));
    expect(publishedCard).toHaveStyle({ opacity: '1' });
    expect(manualCard).toHaveStyle({ opacity: '1' });
    expect(pausedPublishedCard).toHaveStyle({ opacity: '1' });
    expect(unknownStateCard).toHaveStyle({ opacity: '1' });
    expect(inactiveSisterCard).toHaveStyle({ opacity: '0.55' });

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
    const nativeDraftCard = screen.getByText('Native Draft').closest('.job-card');
    expect(nativeOpenCard).not.toHaveClass('not-live');
    expect(nativeDraftCard).toHaveClass('not-live');
    expect(screen.getByRole('button', { name: /^Live2$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Draft1$/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Live2$/i }));
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
    expect(apiClient.roles.list).toHaveBeenCalledWith({ include_pipeline_stats: true, limit: 24 });
    // ...then the background unlimited fetch lands and the tail role appears.
    expect(await screen.findByText('Tail Role Zeta')).toBeInTheDocument();
    expect(apiClient.roles.list).toHaveBeenCalledWith({ include_pipeline_stats: true });
  });

  it('does not background-fetch when the first page is not full', async () => {
    // baseRoles has a single role (< limit) → only the limited call fires.
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: { run_id: null, sync_in_progress: false, workable_last_sync_at: '2026-04-25T13:00:00Z', workable_last_sync_status: 'success', workable_last_sync_summary: {} },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    await screen.findByText('Backend Engineer');
    expect(apiClient.roles.list).toHaveBeenCalledWith({ include_pipeline_stats: true, limit: 24 });
    expect(apiClient.roles.list).not.toHaveBeenCalledWith({ include_pipeline_stats: true });
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
    const closedCard = screen.getByText('Closed Preview').closest('.job-card');
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
