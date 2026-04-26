import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const showToast = vi.fn();

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

vi.mock('../../shared/api', () => ({
  roles: {
    list: vi.fn(),
    listApplicationsGlobal: vi.fn(),
    listPipeline: vi.fn(),
    getApplication: vi.fn(),
    listApplicationEvents: vi.fn(),
    listTasks: vi.fn(),
    updateApplicationStage: vi.fn(),
    updateApplicationOutcome: vi.fn(),
    createAssessment: vi.fn(),
    retakeAssessment: vi.fn(),
  },
  assessments: {
    get: vi.fn(),
  },
}));

vi.mock('./CandidateScoreSummarySheet', () => ({
  CandidateScoreSummarySheet: () => null,
}));

vi.mock('./RetakeAssessmentDialog', () => ({
  RetakeAssessmentDialog: () => null,
}));

import * as apiClient from '../../shared/api';
import { CandidatesDirectoryPage } from './CandidatesDirectoryPage';

const baseRole = { id: 9, name: 'Backend Engineer' };

const makeApplication = (overrides = {}) => ({
  id: 1,
  role_id: baseRole.id,
  role_name: baseRole.name,
  candidate_id: 101,
  candidate_email: 'candidate@example.com',
  candidate_name: 'Candidate Example',
  candidate_position: 'Backend Engineer',
  pipeline_stage: 'applied',
  application_outcome: 'open',
  version: 1,
  workable_candidate_id: '',
  created_at: '2026-04-24T10:00:00Z',
  updated_at: '2026-04-24T10:00:00Z',
  pipeline_stage_updated_at: '2026-04-24T10:00:00Z',
  pre_screen_score: 78,
  score_summary: null,
  pipeline_external_drift: false,
  ...overrides,
});

const buildApplicationsPayload = (items, params = {}) => {
  const filtered = (items || []).filter((application) => {
    const outcomeFilter = params.application_outcome;
    if (outcomeFilter && outcomeFilter !== 'all') {
      return application.application_outcome === outcomeFilter;
    }
    return true;
  });
  return {
    items: filtered,
    total: filtered.length,
    limit: 50,
    offset: 0,
    stage_counts: {
      all: filtered.length,
      applied: filtered.filter((item) => item.pipeline_stage === 'applied').length,
      invited: filtered.filter((item) => item.pipeline_stage === 'invited').length,
      in_assessment: filtered.filter((item) => item.pipeline_stage === 'in_assessment').length,
      review: filtered.filter((item) => item.pipeline_stage === 'review').length,
    },
  };
};

describe('CandidatesDirectoryPage', () => {
  let applicationStore;
  let confirmMock;

  beforeEach(() => {
    vi.clearAllMocks();
    applicationStore = [
      makeApplication({
        id: 1,
        candidate_id: 111,
        candidate_email: 'alice@example.com',
        candidate_name: 'Alice Workable',
        workable_candidate_id: 'wk-1',
      }),
      makeApplication({
        id: 2,
        candidate_id: 222,
        candidate_email: 'bob@example.com',
        candidate_name: 'Bob Local',
        workable_candidate_id: '',
      }),
    ];

    confirmMock = vi.fn(() => true);
    vi.stubGlobal('confirm', confirmMock);

    apiClient.roles.list.mockResolvedValue({ data: [baseRole] });
    apiClient.roles.listTasks.mockResolvedValue({ data: [] });
    apiClient.roles.getApplication.mockImplementation(async (applicationId) => ({
      data: { ...applicationStore.find((item) => Number(item.id) === Number(applicationId)) },
    }));
    apiClient.roles.listApplicationEvents.mockResolvedValue({ data: [] });
    apiClient.roles.listApplicationsGlobal.mockImplementation(async (params = {}) => ({
      data: buildApplicationsPayload(applicationStore, params),
    }));
    apiClient.roles.updateApplicationStage.mockResolvedValue({ data: applicationStore[0] });
    apiClient.roles.updateApplicationOutcome.mockImplementation(async (applicationId) => {
      const target = applicationStore.find((item) => Number(item.id) === Number(applicationId));
      const updated = {
        ...target,
        application_outcome: 'rejected',
        version: Number(target?.version || 1) + 1,
      };
      applicationStore = applicationStore.map((item) => (
        Number(item.id) === Number(applicationId) ? updated : item
      ));
      return { data: updated };
    });
  });

  it('bulk rejects sequentially and reports partial failures', async () => {
    const updateOrder = [];
    apiClient.roles.updateApplicationOutcome.mockImplementation(async (applicationId) => {
      updateOrder.push(Number(applicationId));
      if (Number(applicationId) === 2) {
        const error = new Error('Request failed');
        error.response = { data: { detail: 'Workable timeout' } };
        throw error;
      }
      const target = applicationStore.find((item) => Number(item.id) === Number(applicationId));
      const updated = {
        ...target,
        application_outcome: 'rejected',
        version: Number(target?.version || 1) + 1,
      };
      applicationStore = applicationStore.map((item) => (
        Number(item.id) === Number(applicationId) ? updated : item
      ));
      return { data: updated };
    });

    render(<CandidatesDirectoryPage onNavigate={vi.fn()} />);

    await screen.findAllByText('Alice Workable');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Select page' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Select page' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reject selected (2)' }));

    expect(confirmMock).toHaveBeenCalledWith(expect.stringContaining('1 linked candidate will also be disqualified in Workable.'));

    await waitFor(() => {
      expect(updateOrder).toEqual([1, 2]);
    });

    expect(screen.getByText('Bulk reject finished')).toBeInTheDocument();
    expect(screen.getByText('1 updated, 1 failed')).toBeInTheDocument();
    expect(screen.getByText(/Rejected: Alice Workable/)).toBeInTheDocument();
    expect(screen.getByText(/Failed: Bob Local \(Workable timeout\)/)).toBeInTheDocument();
    expect(showToast).toHaveBeenCalledWith('Bulk reject finished. 1/2 updated, 1 failed.', 'error');
  }, 10000);

  it('rejects a Workable-linked candidate from the inline drawer with two-step confirmation', async () => {
    render(<CandidatesDirectoryPage onNavigate={vi.fn()} />);

    const aliceName = await screen.findByText('Alice Workable');
    const aliceRow = aliceName.closest('[role="button"]');
    expect(aliceRow).toBeTruthy();

    fireEvent.click(aliceRow);

    expect(await screen.findByText('Send Taali assessment')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm reject' }));

    expect(confirmMock).not.toHaveBeenCalled();

    await waitFor(() => {
      expect(apiClient.roles.updateApplicationOutcome).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          application_outcome: 'rejected',
          reason: 'Rejected from candidate triage drawer',
        })
      );
    });
    expect(showToast).toHaveBeenCalledWith('Candidate rejected.', 'success');
  });
});
