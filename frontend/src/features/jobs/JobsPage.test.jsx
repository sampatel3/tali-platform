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

import * as apiClient from '../../shared/api';
import { JobsPage } from './JobsPage';

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
  workable_connected: true,
  workable_subdomain: 'deeplight',
  workable_config: { sync_interval_minutes: 30 },
  workable_last_sync_at: '2026-04-25T13:00:00Z',
  workable_last_sync_status: 'success',
  workable_last_sync_summary: { jobs_seen: 79, candidates_seen: 83217, errors: [] },
};

describe('JobsPage Workable sync states', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiClient.roles.list.mockResolvedValue({ data: baseRoles });
    apiClient.organizations.get.mockResolvedValue({ data: baseOrg });
    apiClient.tasks.list.mockResolvedValue({ data: [] });
    apiClient.agent.status.mockResolvedValue({ data: {} });
    apiClient.agent.rolesBreakdown.mockResolvedValue({ data: [] });
    apiClient.agent.orgStatus.mockResolvedValue({
      data: { org_budget_spent_cents: 4200, org_budget_cap_cents: 9000 },
    });
  });

  it('reattaches to an active sync on first load', async () => {
    apiClient.organizations.getWorkableSyncStatus.mockResolvedValue({
      data: {
        run_id: 77,
        sync_in_progress: true,
        workable_last_sync_at: '2026-04-25T13:00:00Z',
        workable_last_sync_status: 'success',
        workable_last_sync_summary: { jobs_seen: 80, candidates_seen: 83217, errors: [] },
      },
    });

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    expect(await screen.findByRole('button', { name: /Syncing/i })).toBeDisabled();
    expect(apiClient.organizations.getWorkableSyncStatus).toHaveBeenCalledWith();
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
    expect(apiClient.organizations.getWorkableSyncStatus).toHaveBeenCalledWith(88);
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

  it('opens the new role sheet from the jobs hub', async () => {
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

    fireEvent.click(await screen.findByRole('button', { name: '+ New role' }));

    expect(await screen.findByText('Set up a role in three quick steps.')).toBeInTheDocument();
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
