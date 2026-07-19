import React, { useEffect } from 'react';
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  getBullhornSyncStatus: vi.fn(),
  workableSyncStatus: vi.fn(),
  syncGraphStatus: vi.fn(),
  activeBatchScores: vi.fn(),
  batchScoreStatus: vi.fn(),
}));
const authState = vi.hoisted(() => ({
  isAuthenticated: false,
  user: { id: 7, organization_id: 11 },
}));

vi.mock('../shared/api', () => ({
  organizations: apiMocks,
  roles: {
    activeBatchScores: apiMocks.activeBatchScores,
    batchScoreStatus: apiMocks.batchScoreStatus,
    workableSyncStatus: apiMocks.workableSyncStatus,
    syncGraphStatus: apiMocks.syncGraphStatus,
  },
}));

vi.mock('../context/AuthContext', () => ({
  useAuth: () => authState,
}));

import { JobStatusProvider, useJobStatus } from './JobStatusContext';

function StartBullhornTracking() {
  const { trackBullhornSync } = useJobStatus();
  useEffect(() => {
    trackBullhornSync();
  }, [trackBullhornSync]);
  return null;
}

function ObserveScoreJobs({ onChange }) {
  const {
    dismissJob,
    jobs,
    trackedRoleIds,
    trackRole,
  } = useJobStatus();
  useEffect(() => {
    onChange({ dismissJob, jobs, trackedRoleIds: [...trackedRoleIds], trackRole });
  }, [dismissJob, jobs, onChange, trackedRoleIds, trackRole]);
  return null;
}

describe('JobStatusProvider visibility-aware polling', () => {
  let hidden = true;

  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    authState.isAuthenticated = false;
    authState.user = { id: 7, organization_id: 11 };
    apiMocks.getBullhornSyncStatus.mockReset();
    apiMocks.getBullhornSyncStatus.mockResolvedValue({
      data: { status: 'running', sync_in_progress: true },
    });
    apiMocks.workableSyncStatus.mockReset();
    apiMocks.workableSyncStatus.mockResolvedValue({
      data: { status: 'idle', sync_in_progress: false },
    });
    apiMocks.syncGraphStatus.mockReset();
    apiMocks.syncGraphStatus.mockResolvedValue({ data: { status: 'idle' } });
    apiMocks.activeBatchScores.mockReset();
    apiMocks.batchScoreStatus.mockReset();
    apiMocks.batchScoreStatus.mockResolvedValue({
      data: { role_id: 42, status: 'running' },
    });
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('does not poll Bullhorn while the tab is hidden and resumes when visible', async () => {
    render(
      <JobStatusProvider>
        <StartBullhornTracking />
      </JobStatusProvider>,
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(apiMocks.getBullhornSyncStatus).not.toHaveBeenCalled();

    hidden = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(apiMocks.getBullhornSyncStatus).toHaveBeenCalledTimes(1);
  });

  it('stops an active Bullhorn poll while hidden and resumes it again', async () => {
    hidden = false;
    render(
      <JobStatusProvider>
        <StartBullhornTracking />
      </JobStatusProvider>,
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(apiMocks.getBullhornSyncStatus).toHaveBeenCalledTimes(1);

    hidden = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(apiMocks.getBullhornSyncStatus).toHaveBeenCalledTimes(1);

    hidden = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(apiMocks.getBullhornSyncStatus).toHaveBeenCalledTimes(2);
  });

  it('keeps terminal discovery visible without re-adding it to status polling', async () => {
    hidden = false;
    localStorage.setItem('taali_access_token', 'test-token');
    const terminal = {
      role_id: 42,
      run_id: 910,
      role_name: 'Platform Engineer',
      status: 'completed',
      total: 8,
      scored: 8,
    };
    apiMocks.activeBatchScores.mockResolvedValue({ data: { active: [terminal] } });
    const snapshots = [];

    render(
      <JobStatusProvider>
        <ObserveScoreJobs onChange={(snapshot) => snapshots.push(snapshot)} />
      </JobStatusProvider>,
    );

    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(apiMocks.activeBatchScores).toHaveBeenCalledTimes(2);
    expect(apiMocks.batchScoreStatus).not.toHaveBeenCalled();
    expect(snapshots.at(-1).jobs[42]).toEqual(terminal);
    expect(snapshots.at(-1).trackedRoleIds).not.toContain(42);

    let resolveRediscovery;
    const rediscovery = new Promise((resolve) => {
      resolveRediscovery = resolve;
    });
    apiMocks.activeBatchScores.mockReturnValueOnce(rediscovery);
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(apiMocks.activeBatchScores).toHaveBeenCalledTimes(3);

    await act(async () => {
      snapshots.at(-1).dismissJob(42);
      await Promise.resolve();
    });
    expect(snapshots.at(-1).jobs[42]).toBeUndefined();

    await act(async () => {
      resolveRediscovery({ data: { active: [terminal] } });
      await rediscovery;
      await Promise.resolve();
    });
    expect(snapshots.at(-1).jobs[42]).toBeUndefined();

    apiMocks.activeBatchScores.mockResolvedValue({
      data: {
        active: [{ ...terminal, run_id: 911, status: 'completed', scored: 8 }],
      },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(snapshots.at(-1).jobs[42]).toMatchObject({
      run_id: 911,
      status: 'completed',
    });
    expect(snapshots.at(-1).trackedRoleIds).not.toContain(42);
  });

  it('does not let a delayed active snapshot resurrect the dismissed run', async () => {
    hidden = false;
    localStorage.setItem('taali_access_token', 'test-token');
    const terminal = {
      role_id: 42,
      run_id: 910,
      role_name: 'Platform Engineer',
      status: 'completed',
      total: 8,
      scored: 8,
    };
    apiMocks.activeBatchScores.mockResolvedValueOnce({ data: { active: [terminal] } });
    const snapshots = [];

    render(
      <JobStatusProvider>
        <ObserveScoreJobs onChange={(snapshot) => snapshots.push(snapshot)} />
      </JobStatusProvider>,
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(snapshots.at(-1).jobs[42]).toEqual(terminal);

    let resolveStaleDiscovery;
    const staleDiscovery = new Promise((resolve) => {
      resolveStaleDiscovery = resolve;
    });
    apiMocks.activeBatchScores.mockReturnValueOnce(staleDiscovery);
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(apiMocks.activeBatchScores).toHaveBeenCalledTimes(2);

    await act(async () => {
      snapshots.at(-1).dismissJob(42);
      await Promise.resolve();
    });
    await act(async () => {
      resolveStaleDiscovery({
        data: {
          active: [{ ...terminal, status: 'running', scored: 7 }],
        },
      });
      await staleDiscovery;
      await Promise.resolve();
    });

    expect(snapshots.at(-1).jobs[42]).toBeUndefined();
    expect(snapshots.at(-1).trackedRoleIds).not.toContain(42);
  });

  it('persists a local run identity so a fast terminal job stays dismissed before discovery', async () => {
    hidden = false;
    apiMocks.batchScoreStatus.mockResolvedValue({
      data: {
        role_id: 42,
        run_id: 1201,
        started_at: '2026-07-19T10:00:00Z',
        status: 'completed',
        total: 1,
        scored: 1,
      },
    });
    apiMocks.activeBatchScores.mockResolvedValue({
      data: {
        active: [{
          role_id: 42,
          run_id: 1201,
          started_at: '2026-07-19T10:00:00Z',
          status: 'completed',
          total: 1,
          scored: 1,
        }],
      },
    });
    const snapshots = [];
    const view = render(
      <JobStatusProvider>
        <ObserveScoreJobs onChange={(snapshot) => snapshots.push(snapshot)} />
      </JobStatusProvider>,
    );

    await act(async () => {
      snapshots.at(-1).trackRole(42);
      await Promise.resolve();
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(snapshots.at(-1).jobs[42]).toMatchObject({ status: 'completed' });
    const identityKey = 'tali_tracked_score_run_identities:org-11:user-7';
    expect(JSON.parse(localStorage.getItem(identityKey))).toHaveProperty('42');

    await act(async () => {
      snapshots.at(-1).dismissJob(42);
      await Promise.resolve();
    });
    expect(snapshots.at(-1).jobs[42]).toBeUndefined();

    localStorage.setItem('taali_access_token', 'test-token');
    authState.isAuthenticated = true;
    view.rerender(
      <JobStatusProvider>
        <ObserveScoreJobs onChange={(snapshot) => snapshots.push(snapshot)} />
      </JobStatusProvider>,
    );
    await act(async () => {
      await Promise.resolve();
    });

    expect(apiMocks.activeBatchScores).toHaveBeenCalledTimes(1);
    expect(snapshots.at(-1).jobs[42]).toBeUndefined();
    expect(snapshots.at(-1).trackedRoleIds).not.toContain(42);
  });

  it('re-discovers organization syncs when the mounted auth scope changes', async () => {
    hidden = false;
    localStorage.setItem('taali_access_token', 'test-token');
    authState.isAuthenticated = true;
    apiMocks.getBullhornSyncStatus.mockResolvedValue({
      data: { status: 'idle', sync_in_progress: false },
    });

    const view = render(
      <JobStatusProvider>
        <div>scope probe</div>
      </JobStatusProvider>,
    );
    await act(async () => {
      await Promise.resolve();
    });

    expect(apiMocks.workableSyncStatus).toHaveBeenCalledTimes(1);
    expect(apiMocks.getBullhornSyncStatus).toHaveBeenCalledTimes(1);
    expect(apiMocks.syncGraphStatus).toHaveBeenCalledTimes(1);

    authState.user = { id: 7, organization_id: 12 };
    view.rerender(
      <JobStatusProvider>
        <div>scope probe</div>
      </JobStatusProvider>,
    );
    await act(async () => {
      await Promise.resolve();
    });

    expect(apiMocks.workableSyncStatus).toHaveBeenCalledTimes(2);
    expect(apiMocks.getBullhornSyncStatus).toHaveBeenCalledTimes(2);
    expect(apiMocks.syncGraphStatus).toHaveBeenCalledTimes(2);
  });
});
