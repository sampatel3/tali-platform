import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import {
  act,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

const authState = vi.hoisted(() => ({ user: { role: 'owner' } }));
const jobStatusState = vi.hoisted(() => ({ current: {} }));
const rolesApiMocks = vi.hoisted(() => ({
  backgroundJobsRuns: vi.fn(),
  workableSyncRuns: vi.fn(),
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => authState,
}));

vi.mock('../../contexts/JobStatusContext', () => ({
  useJobStatus: () => jobStatusState.current,
}));

vi.mock('../../shared/api', () => ({
  roles: rolesApiMocks,
}));

vi.mock('./GraphIngestReconciliationPanel', () => ({
  default: () => <div>Owner graph reconciliation surface</div>,
}));

import BackgroundJobsPanel from './BackgroundJobsPanel';

const backgroundJobsCss = fs.readFileSync(
  path.join(process.cwd(), 'src/styles/10-search-graph.css'),
  'utf8',
);

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
};

describe('background jobs owner reconciliation surface', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = { role: 'owner' };
    jobStatusState.current = {};
    rolesApiMocks.backgroundJobsRuns.mockResolvedValue({ data: { runs: [] } });
    rolesApiMocks.workableSyncRuns.mockResolvedValue({ data: { runs: [] } });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('mounts the graph reconciliation surface for owners', async () => {
    render(<BackgroundJobsPanel />);
    expect(
      await screen.findByText('Owner graph reconciliation surface'),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('No background jobs running.')).toBeInTheDocument();
    });
  });

  it('does not mount the privileged surface for members', async () => {
    authState.user = { role: 'member' };
    render(<BackgroundJobsPanel />);
    expect(screen.queryByText('Owner graph reconciliation surface')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('No background jobs running.')).toBeInTheDocument();
    });
  });

  it('reports each failed history source without presenting a cold failure as empty', async () => {
    authState.user = { role: 'member' };
    rolesApiMocks.backgroundJobsRuns.mockRejectedValue(new Error('background unavailable'));
    rolesApiMocks.workableSyncRuns.mockRejectedValue(new Error('workable unavailable'));

    render(<BackgroundJobsPanel />);

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Infrastructure run history could not be refreshed.',
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Workable sync history could not be refreshed.',
    );
    expect(screen.getByText('Background job history could not be loaded.')).toBeInTheDocument();
    expect(screen.queryByText('No background jobs running.')).not.toBeInTheDocument();
    expect(screen.getByText(/Auto-refreshing every 5s/).parentElement).toHaveAttribute(
      'title',
      expect.stringMatching(/Infrastructure history: refresh failed.*Workable history: refresh failed/),
    );
  });

  it('keeps successful source freshness while identifying the stale source', async () => {
    authState.user = { role: 'member' };
    rolesApiMocks.backgroundJobsRuns.mockRejectedValue(new Error('background unavailable'));

    render(<BackgroundJobsPanel />);

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Infrastructure run history could not be refreshed.',
    );
    expect(screen.getByText('No background jobs found in the available sources.')).toBeInTheDocument();
    expect(screen.getByText(/Auto-refreshing every 5s/).parentElement).toHaveAttribute(
      'title',
      expect.stringMatching(/Infrastructure history: refresh failed.*Workable history: updated/),
    );
  });

  it('pauses hidden-section polling and refreshes immediately when reactivated', async () => {
    authState.user = { role: 'member' };
    vi.useFakeTimers();
    const { rerender } = render(<BackgroundJobsPanel active={false} />);

    expect(rolesApiMocks.backgroundJobsRuns).not.toHaveBeenCalled();
    expect(rolesApiMocks.workableSyncRuns).not.toHaveBeenCalled();

    rerender(<BackgroundJobsPanel active />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(rolesApiMocks.backgroundJobsRuns).toHaveBeenCalledTimes(1);
    expect(rolesApiMocks.workableSyncRuns).toHaveBeenCalledTimes(1);

    rerender(<BackgroundJobsPanel active={false} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(rolesApiMocks.backgroundJobsRuns).toHaveBeenCalledTimes(1);
    expect(rolesApiMocks.workableSyncRuns).toHaveBeenCalledTimes(1);

    rerender(<BackgroundJobsPanel active />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(rolesApiMocks.backgroundJobsRuns).toHaveBeenCalledTimes(2);
    expect(rolesApiMocks.workableSyncRuns).toHaveBeenCalledTimes(2);
  });

  it('keeps visibility refocus refreshes on one poll loop while a request is in flight', async () => {
    authState.user = { role: 'member' };
    vi.useFakeTimers();
    const background = deferred();
    const workable = deferred();
    rolesApiMocks.backgroundJobsRuns.mockReturnValueOnce(background.promise);
    rolesApiMocks.workableSyncRuns.mockReturnValueOnce(workable.promise);
    let hidden = false;
    const hiddenSpy = vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);

    try {
      render(<BackgroundJobsPanel />);
      await act(async () => { await Promise.resolve(); });
      expect(rolesApiMocks.backgroundJobsRuns).toHaveBeenCalledTimes(1);

      hidden = true;
      document.dispatchEvent(new Event('visibilitychange'));
      hidden = false;
      document.dispatchEvent(new Event('visibilitychange'));
      expect(rolesApiMocks.backgroundJobsRuns).toHaveBeenCalledTimes(1);

      await act(async () => {
        background.resolve({ data: { runs: [] } });
        workable.resolve({ data: { runs: [] } });
        await background.promise;
        await workable.promise;
        await Promise.resolve();
      });
      expect(rolesApiMocks.backgroundJobsRuns).toHaveBeenCalledTimes(2);
      expect(rolesApiMocks.workableSyncRuns).toHaveBeenCalledTimes(2);

      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
      expect(rolesApiMocks.backgroundJobsRuns).toHaveBeenCalledTimes(3);
      expect(rolesApiMocks.workableSyncRuns).toHaveBeenCalledTimes(3);
    } finally {
      hiddenSpy.mockRestore();
    }
  });

  it('exposes table semantics and responsive labels without hiding column headers', async () => {
    authState.user = { role: 'member' };
    jobStatusState.current = {
      jobs: {
        8: {
          status: 'running',
          role_name: 'Platform Engineer',
          total: 3,
          scored: 1,
          started_at: '2026-07-17T01:00:00Z',
        },
      },
    };

    render(<BackgroundJobsPanel />);

    expect(await screen.findByRole('table', { name: 'Background job runs' })).toBeInTheDocument();
    expect(screen.getAllByRole('columnheader')).toHaveLength(7);
    expect(screen.getByRole('columnheader', { name: 'Started' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: 'Role: Platform Engineer' })).toHaveAttribute(
      'data-label',
      'Scope',
    );
    expect(backgroundJobsCss).toMatch(/content:\s*attr\(data-label\)/);
    expect(backgroundJobsCss).not.toMatch(/\.bg-jobs-panel-head\s*\{\s*display:\s*none/);
  });
});
