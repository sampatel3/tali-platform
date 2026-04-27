import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../shared/api', () => ({
  roles: {
    batchScoreStatus: vi.fn(),
    fetchCvsStatus: vi.fn(),
    cancelBatchScore: vi.fn(),
    cancelFetchCvs: vi.fn(),
  },
}));

import * as apiClient from '../../shared/api';
import { BackgroundJobsToaster } from './BackgroundJobsToaster';

describe('BackgroundJobsToaster', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when no roleId is provided', () => {
    const { container } = render(<BackgroundJobsToaster roleId={null} />);
    expect(container.firstChild).toBeNull();
    expect(apiClient.roles.batchScoreStatus).not.toHaveBeenCalled();
  });

  it('renders nothing when both jobs are idle', async () => {
    apiClient.roles.batchScoreStatus.mockResolvedValue({
      data: { status: 'idle', total: 0, scored: 0, errors: 0 },
    });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({
      data: { status: 'idle', total: 0, fetched: 0 },
    });
    const { container } = render(<BackgroundJobsToaster roleId={42} />);
    await waitFor(() => {
      expect(apiClient.roles.batchScoreStatus).toHaveBeenCalledWith(42);
    });
    // Allow one tick for state update.
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });

  it('shows scoring progress when a batch is running', async () => {
    apiClient.roles.batchScoreStatus.mockResolvedValue({
      data: { status: 'running', total: 10, scored: 3, errors: 0 },
    });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({
      data: { status: 'idle', total: 0, fetched: 0 },
    });
    render(<BackgroundJobsToaster roleId={101} />);
    await waitFor(() => {
      expect(screen.getByText(/Re-scoring CVs/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/3\/10 scored/)).toBeInTheDocument();
  });

  it('shows fetching progress when a CV fetch is running', async () => {
    apiClient.roles.batchScoreStatus.mockResolvedValue({
      data: { status: 'idle', total: 0, scored: 0, errors: 0 },
    });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({
      data: { status: 'running', total: 50, fetched: 12 },
    });
    render(<BackgroundJobsToaster roleId={101} />);
    await waitFor(() => {
      expect(screen.getByText(/Fetching CVs from Workable/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/12\/50 fetched/)).toBeInTheDocument();
  });

  it('shows completed checkmark when batch finished', async () => {
    apiClient.roles.batchScoreStatus.mockResolvedValue({
      data: { status: 'completed', total: 10, scored: 10, errors: 0 },
    });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({
      data: { status: 'idle', total: 0, fetched: 0 },
    });
    render(<BackgroundJobsToaster roleId={101} />);
    await waitFor(() => {
      expect(screen.getByText(/Re-scoring complete/i)).toBeInTheDocument();
    });
    // Final detail: 10/10 + 0 errors
    expect(screen.getByText(/10\/10 scored/)).toBeInTheDocument();
  });

  it('Cancel button calls the right endpoint and flips status to cancelling', async () => {
    apiClient.roles.batchScoreStatus.mockResolvedValue({
      data: { status: 'running', total: 100, scored: 25, errors: 0 },
    });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({
      data: { status: 'idle', total: 0, fetched: 0 },
    });
    apiClient.roles.cancelBatchScore.mockResolvedValue({
      data: { ok: true, status: 'cancelling' },
    });

    render(<BackgroundJobsToaster roleId={101} />);
    const cancelButton = await screen.findByRole('button', { name: /Cancel re-scoring/i });
    fireEvent.click(cancelButton);

    await waitFor(() => {
      expect(apiClient.roles.cancelBatchScore).toHaveBeenCalledWith(101);
    });
    // Optimistic UI flip
    await waitFor(() => {
      expect(screen.getByText(/Cancelling re-score/i)).toBeInTheDocument();
    });
  });

  it('renders both rows when batch and fetch are running concurrently', async () => {
    apiClient.roles.batchScoreStatus.mockResolvedValue({
      data: { status: 'running', total: 100, scored: 25, errors: 1 },
    });
    apiClient.roles.fetchCvsStatus.mockResolvedValue({
      data: { status: 'running', total: 600, fetched: 480 },
    });
    render(<BackgroundJobsToaster roleId={42} />);
    await waitFor(() => {
      expect(screen.getByText(/Re-scoring CVs/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Fetching CVs from Workable/i)).toBeInTheDocument();
    expect(screen.getByText(/25\/100 scored.*1 error/i)).toBeInTheDocument();
    expect(screen.getByText(/480\/600 fetched/)).toBeInTheDocument();
  });
});
