import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  viewTopReport: vi.fn(),
}));

vi.mock('../../shared/api/httpClient', () => ({
  default: {},
  viewTopReport: mocks.viewTopReport,
}));

import TopReportPage from './TopReportPage';

const renderReport = () => render(
  <MemoryRouter initialEntries={['/report/report-token']}>
    <Routes>
      <Route path="/report/:reportToken" element={<TopReportPage />} />
    </Routes>
  </MemoryRouter>,
);

beforeEach(() => {
  mocks.viewTopReport.mockReset();
});

describe('TopReportPage', () => {
  it('renders the frozen grounded evidence snapshot without a nested share link', async () => {
    mocks.viewTopReport.mockResolvedValue({
      data: {
        query: 'Top platform engineers',
        created_at: '2026-07-15T08:00:00Z',
        snapshot: {
          role_name: 'Platform Engineer',
          shown: 1,
          evidence_model: 'grounder-v1',
          database_matches: 1,
          criteria_requested: ['Platform ownership'],
          criteria_checked: ['Platform ownership'],
          criteria_unchecked: [],
          deep_checked: 1,
          evidence_succeeded: 1,
          qualified: 1,
          capped: false,
          report_url: '/report/report-token',
          candidates: [{
            application_id: 77,
            rank: 1,
            candidate_name: 'Lena Ortiz',
            candidate_headline: 'Platform lead',
            criteria: [{
              criterion: 'Platform ownership',
              status: 'met',
              grounded: true,
              evidence: [{ quote: 'Owned the platform roadmap and launch.', source: 'cv' }],
            }],
          }],
        },
      },
    });

    renderReport();

    expect(await screen.findByRole('heading', { name: 'Top candidates' })).toBeInTheDocument();
    expect(mocks.viewTopReport).toHaveBeenCalledWith('report-token');
    expect(screen.getByText('Platform Engineer')).toBeInTheDocument();
    expect(screen.getByText(/Owned the platform roadmap and launch/)).toBeInTheDocument();
    expect(screen.getByText(/grounded vs CV \+ notes/)).toBeInTheDocument();
    expect(screen.getByText('Shared from Taali · read-only snapshot')).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Open shareable grounded candidate report' }),
    ).not.toBeInTheDocument();
  });

  it('shows an explicit expiry message for a revoked report', async () => {
    mocks.viewTopReport.mockRejectedValue({ response: { status: 410 } });

    renderReport();

    expect(
      await screen.findByText('This report has expired or been revoked.'),
    ).toBeInTheDocument();
  });
});
