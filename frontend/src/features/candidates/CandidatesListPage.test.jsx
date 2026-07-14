import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api', () => ({
  roles: {
    list: vi.fn(),
    listApplicationsGlobal: vi.fn(),
  },
}));

import { roles as rolesApi } from '../../shared/api';
import CandidatesListPage from './CandidatesListPage';

const sampleItems = [
  {
    id: 5001,
    candidate_id: 42,
    candidate_name: 'Ada Applicant',
    candidate_email: 'ada@example.com',
    role_id: 12,
    role_name: 'Senior API Developer',
    pipeline_stage: 'review',
    application_outcome: 'open',
    source: 'workable',
    taali_score: 88,
    applied_at: '2026-06-01T00:00:00Z',
    created_at: '2026-06-01T00:00:00Z',
  },
  {
    id: 5002,
    candidate_id: 43,
    candidate_name: 'Ben Builder',
    candidate_email: 'ben@example.com',
    role_id: 13,
    role_name: 'Frontend Engineer',
    pipeline_stage: 'applied',
    application_outcome: 'rejected',
    source: 'manual',
    created_at: '2026-06-02T00:00:00Z',
  },
];

const globalResponse = (items = sampleItems) => ({
  data: { items, total: items.length, limit: 25, offset: 0 },
});

beforeEach(() => {
  vi.clearAllMocks();
  rolesApi.list.mockResolvedValue({ data: [{ id: 12, name: 'Senior API Developer', short_name: 'API Dev' }] });
  rolesApi.listApplicationsGlobal.mockResolvedValue(globalResponse());
});

describe('CandidatesListPage', () => {
  it('renders a candidate row per application with role, stage and score', async () => {
    render(<CandidatesListPage />);
    const nameCell = await screen.findByText('Ada Applicant');
    const row = nameCell.closest('tr');
    expect(within(row).getByText('Senior API Developer')).toBeInTheDocument();
    expect(within(row).getByText('Review')).toBeInTheDocument();
    expect(within(row).getByText('88')).toBeInTheDocument();
    expect(screen.getByText('Ben Builder')).toBeInTheDocument();
  });

  it('links each row to the candidate report using the application id', async () => {
    render(<CandidatesListPage />);
    const nameCell = await screen.findByText('Ada Applicant');
    const row = nameCell.closest('tr');
    const link = within(row).getByRole('link', { name: /view/i });
    // The report route is application-scoped — the href must carry the
    // APPLICATION id (5001), never the candidate id (42).
    expect(link).toHaveAttribute('href', '/candidates/5001');
  });

  it('defaults to active (open) candidates on first load', async () => {
    render(<CandidatesListPage />);
    await screen.findByText('Ada Applicant');
    expect(rolesApi.listApplicationsGlobal).toHaveBeenCalledWith(
      expect.objectContaining({ application_outcome: 'open' }),
    );
  });

  it('refetches with a stage filter when the stage select changes', async () => {
    render(<CandidatesListPage />);
    await screen.findByText('Ada Applicant');

    const stageSelect = screen.getByLabelText('Stage');
    fireEvent.change(stageSelect, { target: { value: 'advanced' } });

    await waitFor(() => {
      expect(rolesApi.listApplicationsGlobal).toHaveBeenCalledWith(
        expect.objectContaining({ pipeline_stage: 'advanced' }),
      );
    });
  });

  it('shows a candidate-oriented empty state when there are no rows', async () => {
    rolesApi.listApplicationsGlobal.mockResolvedValue(globalResponse([]));
    render(<CandidatesListPage />);
    expect(await screen.findByText('No candidates yet')).toBeInTheDocument();
  });
});
