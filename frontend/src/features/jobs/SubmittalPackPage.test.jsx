import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Routes, Route } from 'react-router-dom';

import TestMemoryRouter from '../../test/TestMemoryRouter';

vi.mock('../../shared/api/httpClient', () => ({
  viewSubmittalPack: vi.fn(),
}));

import { viewSubmittalPack } from '../../shared/api/httpClient';
import SubmittalPackPage from './SubmittalPackPage';

const renderAt = (path) =>
  render(
    <TestMemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/submittal/:submittalToken" element={<SubmittalPackPage />} />
      </Routes>
    </TestMemoryRouter>,
  );

describe('SubmittalPackPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the frozen submittal snapshot from the public payload', async () => {
    viewSubmittalPack.mockResolvedValue({
      data: {
        title: 'Shortlist for Acme',
        role: { title: 'Backend Engineer' },
        organization: { name: 'Talent Co' },
        created_at: '2026-07-10T10:00:00Z',
        candidates: [
          {
            application_id: 1,
            candidate_name: 'Alice One',
            verdict: 'Strong match — recommended',
            verdict_band: 'strong',
            score_100: 88,
            highlights: ['Led platform migration', '8 years backend'],
            note: 'Best systems-design signal in the pool.',
          },
          {
            application_id: 2,
            candidate_name: 'Bob Two',
            verdict: 'Good fit — recommended',
            verdict_band: 'good',
            score_100: 74,
            highlights: [],
            note: null,
          },
        ],
      },
    });

    renderAt('/submittal/sub_test_token');

    await waitFor(() => {
      expect(viewSubmittalPack).toHaveBeenCalledWith('sub_test_token');
    });

    expect(await screen.findByText('Shortlist for Acme')).toBeInTheDocument();
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText('Alice One')).toBeInTheDocument();
    expect(screen.getByText('Bob Two')).toBeInTheDocument();
    expect(screen.getByText('Best systems-design signal in the pool.')).toBeInTheDocument();
    expect(screen.getByText('Led platform migration')).toBeInTheDocument();
    expect(screen.getByText('88')).toBeInTheDocument();
  });

  it('shows an expired/revoked empty state on 410', async () => {
    viewSubmittalPack.mockRejectedValue({ response: { status: 410 } });
    renderAt('/submittal/sub_gone');
    expect(await screen.findByText(/expired or been revoked/i)).toBeInTheDocument();
  });
});
