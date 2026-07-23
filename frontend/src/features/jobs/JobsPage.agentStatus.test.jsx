import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

// The org-status store is module-level and keeps its last good snapshot warm,
// so these tests live in their own file: they need a store that has never
// held a successful response.
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
  tasks: { list: vi.fn() },
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
import { JobsPage } from './JobsPage';

const OFF_STATE_COPY = /Open a role and turn on agent mode there/i;

const agentRoles = [
  {
    id: 101,
    name: 'Backend Engineer',
    source: 'workable',
    agentic_mode_enabled: true,
    monthly_usd_budget_cents: 10000,
    stage_counts: { applied: 3, review: 1, advanced: 2, rejected: 4 },
    active_candidates_count: 5,
  },
];

describe('JobsPage agent header when org-status does not land', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query.includes('reduce'),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    apiClient.roles.list.mockResolvedValue({ data: agentRoles });
    apiClient.organizations.get.mockResolvedValue({ data: { id: 1, name: 'Deeplight AI' } });
    apiClient.tasks.list.mockResolvedValue({ data: [] });
    apiClient.agent.status.mockResolvedValue({ data: {} });
    apiClient.agent.rolesBreakdown.mockResolvedValue({ data: [] });
    useJobStatus.mockReturnValue({
      workableSyncJob: null,
      bullhornSyncJob: null,
      trackWorkableSync: vi.fn(),
      trackBullhornSync: vi.fn(),
    });
  });

  it('reports the workspace agent as unknown, not off, when the poll fails', async () => {
    // A poll that times out is what put "AGENTS OFF" over a workspace with
    // roles actively running. Absence of an answer is not an answer.
    apiClient.agent.orgStatus.mockRejectedValue(new Error('timeout of 10000ms exceeded'));

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    expect(await screen.findByText(/Agent status unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(OFF_STATE_COPY)).not.toBeInTheDocument();
  });

  it('does not report an org budget or a clear queue it has not been told', async () => {
    apiClient.agent.orgStatus.mockRejectedValue(new Error('timeout of 10000ms exceeded'));
    apiClient.agent.rolesBreakdown.mockRejectedValue(new Error('boom'));

    render(<MemoryRouter><JobsPage onNavigate={vi.fn()} /></MemoryRouter>);

    await screen.findByText(/Agent status unavailable/i);
    await waitFor(() => {
      expect(screen.getAllByText('status unavailable').length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.queryByText('no cap set')).not.toBeInTheDocument();
    expect(screen.queryByText('queue clear')).not.toBeInTheDocument();
  });
});
