import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { analytics as analyticsApi } from '../../shared/api';
import { PipelineAnalyticsPage } from './PipelineAnalyticsPage';

vi.mock('../../shared/api', () => ({
  analytics: { pipelineFunnel: vi.fn(), timeToFill: vi.fn() },
}));

describe('PipelineAnalyticsPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders funnel stages and time-to-fill stats', async () => {
    // The shared api client resolves with the full Axios response ({ data }),
    // so the mocks are axios-shaped — the page must unwrap `.data`.
    analyticsApi.pipelineFunnel.mockResolvedValue({
      data: {
        total: 3,
        stages: [
          { slug: 'applied', name: 'Applied', kind: 'applied', count: 2 },
          { slug: 'advanced', name: 'Advanced', kind: 'interview', count: 1 },
        ],
        outcomes: { open: 2, hired: 1 },
      },
    });
    analyticsApi.timeToFill.mockResolvedValue({
      data: {
        overall: { count: 1, avg: 15, median: 15, min: 15, max: 15 },
        by_role: [],
      },
    });

    render(<PipelineAnalyticsPage />);

    expect(await screen.findByText('Applied')).toBeInTheDocument();
    expect(screen.getByText('Advanced')).toBeInTheDocument();
    expect(screen.getByText(/3 in pipeline/)).toBeInTheDocument();
    expect(screen.getByText('Hires')).toBeInTheDocument();
    expect(screen.getByText('Median')).toBeInTheDocument();
    expect(screen.getAllByText('15 days').length).toBeGreaterThan(0);
  });

  it('shows empty states when there is no data', async () => {
    analyticsApi.pipelineFunnel.mockResolvedValue({
      data: { total: 0, stages: [], outcomes: {} },
    });
    analyticsApi.timeToFill.mockResolvedValue({
      data: { overall: { count: 0 }, by_role: [] },
    });

    render(<PipelineAnalyticsPage />);
    expect(await screen.findByText(/No applications yet/)).toBeInTheDocument();
    expect(screen.getByText(/No accepted offers yet/)).toBeInTheDocument();
  });
});
