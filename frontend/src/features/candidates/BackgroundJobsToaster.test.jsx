import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// BackgroundJobsToaster reads everything from JobStatusContext now (no direct
// API calls). To test it, mock useJobStatus to return whatever job state we
// want and assert on the rendered DOM.
const useJobStatusMock = vi.fn();
vi.mock('../../contexts/JobStatusContext', () => ({
  useJobStatus: () => useJobStatusMock(),
}));

import { BackgroundJobsToaster } from './BackgroundJobsToaster';

const baseCtx = (overrides = {}) => ({
  jobs: {},
  fetchJobs: {},
  preScreenJobs: {},
  processJobs: {},
  graphSyncJob: null,
  workableSyncJob: null,
  dismissJob: vi.fn(),
  dismissFetchJob: vi.fn(),
  dismissPreScreenJob: vi.fn(),
  dismissProcessJob: vi.fn(),
  dismissGraphSyncJob: vi.fn(),
  dismissWorkableSyncJob: vi.fn(),
  cancelBatch: vi.fn(),
  cancelFetchCvs: vi.fn(),
  cancelProcessJob: vi.fn(),
  ...overrides,
});

describe('BackgroundJobsToaster', () => {
  beforeEach(() => {
    useJobStatusMock.mockReset();
  });

  it('renders nothing when context is unavailable', () => {
    useJobStatusMock.mockReturnValue(null);
    const { container } = render(<BackgroundJobsToaster />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when every job is idle', () => {
    useJobStatusMock.mockReturnValue(baseCtx());
    const { container } = render(<BackgroundJobsToaster />);
    expect(container.firstChild).toBeNull();
  });

  it('shows scoring progress when a batch is running', () => {
    useJobStatusMock.mockReturnValue(
      baseCtx({
        jobs: {
          42: { status: 'running', total: 10, scored: 3, errors: 0, role_name: 'Senior PM' },
        },
      }),
    );
    render(<BackgroundJobsToaster />);
    expect(screen.getByText(/Senior PM:.*Scoring CVs/i)).toBeInTheDocument();
    expect(screen.getByText(/3\/10 processed/i)).toBeInTheDocument();
  });

  it('shows fetching progress when a CV fetch is running', () => {
    useJobStatusMock.mockReturnValue(
      baseCtx({
        fetchJobs: {
          42: { status: 'running', total: 50, fetched: 12, role_name: 'Data Engineer' },
        },
      }),
    );
    render(<BackgroundJobsToaster />);
    expect(screen.getByText(/Data Engineer:.*Fetching CVs/i)).toBeInTheDocument();
    expect(screen.getByText(/12\/50 processed/i)).toBeInTheDocument();
  });

  it('shows completed state when batch finishes', () => {
    useJobStatusMock.mockReturnValue(
      baseCtx({
        jobs: {
          42: { status: 'completed', total: 10, scored: 10, errors: 0, role_name: 'Senior PM' },
        },
      }),
    );
    render(<BackgroundJobsToaster />);
    expect(screen.getByText(/Senior PM:.*complete/i)).toBeInTheDocument();
  });

  it('Cancel button calls cancelBatch with the right roleId', () => {
    const cancelBatch = vi.fn();
    useJobStatusMock.mockReturnValue(
      baseCtx({
        jobs: {
          42: { status: 'running', total: 100, scored: 25, errors: 0, role_name: 'Senior PM' },
        },
        cancelBatch,
      }),
    );
    render(<BackgroundJobsToaster />);
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(cancelBatch).toHaveBeenCalledWith(42);
  });

  it('renders both score and fetch rows when running concurrently for different roles', () => {
    useJobStatusMock.mockReturnValue(
      baseCtx({
        jobs: {
          42: { status: 'running', total: 10, scored: 3, errors: 0, role_name: 'Senior PM' },
        },
        fetchJobs: {
          43: { status: 'running', total: 50, fetched: 12, role_name: 'Data Engineer' },
        },
      }),
    );
    render(<BackgroundJobsToaster />);
    expect(screen.getByText(/Senior PM:.*Scoring CVs/i)).toBeInTheDocument();
    expect(screen.getByText(/Data Engineer:.*Fetching CVs/i)).toBeInTheDocument();
  });
});
