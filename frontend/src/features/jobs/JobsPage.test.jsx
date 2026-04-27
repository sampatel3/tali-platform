import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
}));

import * as apiClient from '../../shared/api';
import { JobsPage } from './JobsPage';

const baseRoles = [
  {
    id: 101,
    name: 'Backend Engineer',
    source: 'workable',
    stage_counts: { applied: 3, invited: 1, in_assessment: 1, review: 0 },
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
});
