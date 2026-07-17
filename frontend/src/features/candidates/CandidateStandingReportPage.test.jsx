import React from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import {
  MemoryRouter,
  Route,
  Routes,
  useNavigate,
} from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  approveDecision: vi.fn(),
  getAssessment: vi.fn(),
  getApplication: vi.fn(),
  getWorkableStages: vi.fn(),
  listApplicationEvents: vi.fn(),
  listDecisions: vi.fn(),
  reEvaluateDecision: vi.fn(),
  removeAssessment: vi.fn(),
  showToast: vi.fn(),
  snoozeDecision: vi.fn(),
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast: mocks.showToast }),
}));

vi.mock('../../shared/api', () => ({
  viewShareLink: vi.fn(),
  roles: {
    getApplication: mocks.getApplication,
    listApplicationEvents: mocks.listApplicationEvents,
  },
  assessments: {
    get: mocks.getAssessment,
    remove: mocks.removeAssessment,
  },
  candidates: {},
  organizations: { getWorkableStages: mocks.getWorkableStages },
  agent: {
    approveDecision: mocks.approveDecision,
    listDecisions: mocks.listDecisions,
    reEvaluateDecision: mocks.reEvaluateDecision,
    snoozeDecision: mocks.snoozeDecision,
  },
}));

import { CandidateStandingReportPage } from './CandidateStandingReportPage';

const application = {
  id: 42,
  role_id: 8,
  role_name: 'Platform Engineer',
  candidate_id: 142,
  candidate_name: 'Ada Lovelace',
  candidate_email: 'ada@example.com',
  pipeline_stage: 'review',
  status: 'applied',
  application_outcome: 'open',
  cv_match_score: 78,
  cv_match_details: {
    score_scale: '0-100',
    summary: 'Strong platform evidence.',
    requirements_assessment: [],
  },
  created_at: '2026-07-16T08:00:00Z',
  updated_at: '2026-07-16T09:00:00Z',
};

const displayedRoleFamily = {
  owner: { id: 8, name: 'Platform Engineer' },
  related: [
    { id: 9, name: 'Infrastructure Engineer' },
    { id: 10, name: 'Site Reliability Engineer' },
  ],
};

const rejectDecision = {
  id: 91,
  role_id: 8,
  application_id: 42,
  candidate_name: 'Ada Lovelace',
  decision_type: 'reject',
  status: 'pending',
  role_family: displayedRoleFamily,
};

const refreshedDecision = {
  ...rejectDecision,
  id: 92,
  decision_type: 'send_assessment',
};

const nextApplication = {
  ...application,
  id: 43,
  candidate_id: 143,
  candidate_name: 'Grace Hopper',
  candidate_email: 'grace@example.com',
};

const reconciliationEvent = {
  id: 301,
  event_type: 'auto_reject_manual_reconciliation_required',
  reason: 'The provider outcome could not be confirmed safely.',
  created_at: '2026-07-16T10:00:00Z',
};

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
};

function RouteControls() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate('/c/43')}>
      Open candidate 43
    </button>
  );
}

const renderReport = (onNavigate = vi.fn()) => render(
  <MemoryRouter initialEntries={['/c/42']}>
    <RouteControls />
    <Routes>
      <Route
        path="/c/:applicationId"
        element={<CandidateStandingReportPage onNavigate={onNavigate} />}
      />
    </Routes>
  </MemoryRouter>,
);

describe('CandidateStandingReportPage authority and reconciliation contracts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getApplication.mockResolvedValue({ data: application });
    mocks.getWorkableStages.mockResolvedValue({ data: { stages: [] } });
    mocks.listApplicationEvents.mockResolvedValue({ data: [reconciliationEvent] });
    mocks.listDecisions
      .mockResolvedValueOnce({ data: [rejectDecision] })
      .mockResolvedValue({ data: [refreshedDecision] });
    mocks.snoozeDecision.mockResolvedValue({ data: {} });
    mocks.reEvaluateDecision.mockResolvedValue({ data: {} });
  });

  it.each(['DECISION_CHANGED', 'ROLE_FAMILY_CHANGED'])(
    'refreshes the decision and report when reject authority returns %s',
    async (code) => {
      mocks.approveDecision.mockRejectedValue({
        response: { status: 409, data: { detail: { code } } },
      });
      renderReport();

      fireEvent.click(await screen.findByRole('button', { name: /^Reject$/i }));

      await waitFor(() => {
        expect(mocks.approveDecision).toHaveBeenCalledWith(91, {
          expected_decision_type: 'reject',
          expected_role_family: displayedRoleFamily,
        });
        expect(mocks.getApplication).toHaveBeenCalledTimes(2);
        expect(mocks.listDecisions).toHaveBeenCalledTimes(2);
      });
      expect(mocks.approveDecision).toHaveBeenCalledTimes(1);
      expect(mocks.showToast).toHaveBeenCalledWith(
        'The recommendation or linked role family changed. Report refreshed — review the current action before trying again.',
        'warning',
      );
      expect(await screen.findByRole('button', { name: /^Send assessment$/i })).toBeEnabled();
    },
  );

  it('keeps the current reject action when the authoritative refresh fails', async () => {
    mocks.listDecisions.mockReset()
      .mockResolvedValueOnce({ data: [rejectDecision] })
      .mockRejectedValueOnce(new Error('decision refresh unavailable'));
    mocks.approveDecision.mockRejectedValue({
      response: { status: 409, data: { detail: { code: 'DECISION_CHANGED' } } },
    });
    renderReport();

    fireEvent.click(await screen.findByRole('button', { name: /^Reject$/i }));

    await waitFor(() => {
      expect(mocks.getApplication).toHaveBeenCalledTimes(2);
      expect(mocks.listDecisions).toHaveBeenCalledTimes(2);
      expect(mocks.showToast).toHaveBeenCalledWith(
        "The recommendation or linked role family changed, but the current report couldn't be refreshed. Nothing was retried.",
        'error',
      );
    });
    expect(mocks.approveDecision).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /^Reject$/i })).toBeEnabled();
    expect(mocks.showToast).not.toHaveBeenCalledWith(
      'The recommendation or linked role family changed. Report refreshed — review the current action before trying again.',
      'warning',
    );
  });

  it('renders a human-readable manual-reconciliation activity label', async () => {
    renderReport();

    expect(await screen.findByText('ATS rejection needs manual reconciliation')).toBeInTheDocument();
    expect(screen.queryByText('Auto reject manual reconciliation required')).not.toBeInTheDocument();
    expect(screen.getByText('The provider outcome could not be confirmed safely.')).toBeInTheDocument();
  });

  it('never lets an older route response overwrite the current candidate report', async () => {
    const firstRoute = deferred();
    const secondRoute = deferred();
    mocks.getApplication.mockReset().mockImplementation((applicationId) => (
      Number(applicationId) === 42 ? firstRoute.promise : secondRoute.promise
    ));
    renderReport();

    await waitFor(() => {
      expect(mocks.getApplication.mock.calls.some(([id]) => Number(id) === 42)).toBe(true);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Open candidate 43' }));
    await waitFor(() => {
      expect(mocks.getApplication.mock.calls.some(([id]) => Number(id) === 43)).toBe(true);
    });

    await act(async () => {
      secondRoute.resolve({
        data: {
          ...application,
          id: 43,
          candidate_id: 143,
          candidate_name: 'Grace Hopper',
          candidate_email: 'grace@example.com',
        },
      });
    });
    expect(await screen.findAllByText('Grace Hopper')).not.toHaveLength(0);

    await act(async () => {
      firstRoute.resolve({ data: application });
      await firstRoute.promise;
    });
    await waitFor(() => {
      expect(screen.getAllByText('Grace Hopper')).not.toHaveLength(0);
      expect(screen.queryAllByText('Ada Lovelace')).toHaveLength(0);
    });
  });

  it('does not carry a decision modal into another candidate route', async () => {
    mocks.getApplication.mockImplementation((applicationId) => ({
      data: Number(applicationId) === 43 ? nextApplication : application,
    }));
    renderReport();

    fireEvent.click(await screen.findByRole('button', { name: 'Teach' }));
    expect(screen.getByRole('dialog', { name: /What did the agent get wrong/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open candidate 43' }));

    expect(await screen.findAllByText('Grace Hopper')).not.toHaveLength(0);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('drops a stage lookup that finishes after the candidate route changes', async () => {
    const stages = deferred();
    const linkedDecision = { ...rejectDecision, workable_job_id: 'platform-role' };
    mocks.getApplication.mockImplementation((applicationId) => ({
      data: Number(applicationId) === 43 ? nextApplication : application,
    }));
    mocks.listDecisions.mockReset()
      .mockResolvedValueOnce({ data: [linkedDecision] })
      .mockResolvedValue({ data: [refreshedDecision] });
    mocks.getWorkableStages.mockReturnValueOnce(stages.promise);
    renderReport();

    fireEvent.click(await screen.findByRole('button', { name: 'Advance instead' }));
    await waitFor(() => expect(mocks.getWorkableStages).toHaveBeenCalledWith({
      shortcode: 'platform-role',
    }));
    fireEvent.click(screen.getByRole('button', { name: 'Open candidate 43' }));
    await act(async () => {
      stages.resolve({ data: { stages: [{ slug: 'interview', name: 'Interview' }] } });
      await stages.promise;
    });

    expect(await screen.findAllByText('Grace Hopper')).not.toHaveLength(0);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('drops a decision-only response that finishes after the candidate route changes', async () => {
    const staleDecision = deferred();
    mocks.getApplication.mockImplementation((applicationId) => ({
      data: Number(applicationId) === 43 ? nextApplication : application,
    }));
    mocks.listDecisions.mockReset()
      .mockResolvedValueOnce({ data: [rejectDecision] })
      .mockReturnValueOnce(staleDecision.promise)
      .mockResolvedValue({ data: [refreshedDecision] });
    const { container } = renderReport();

    fireEvent.click(await screen.findByRole('button', { name: 'Snooze' }));
    await waitFor(() => expect(mocks.listDecisions).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('button', { name: 'Open candidate 43' }));
    await waitFor(() => expect(mocks.listDecisions).toHaveBeenCalledTimes(3));
    expect(await screen.findAllByText('Grace Hopper')).not.toHaveLength(0);

    await act(async () => {
      staleDecision.resolve({ data: [rejectDecision] });
      await staleDecision.promise;
    });
    await waitFor(() => expect(container.querySelector('.dr-rec-btn')).toHaveTextContent(
      'Send assessment',
    ));
  });

  it('never lets an old mutation start a stale full-report load after navigation', async () => {
    const oldMutation = deferred();
    mocks.reEvaluateDecision.mockReturnValueOnce(oldMutation.promise);
    mocks.getApplication.mockImplementation((applicationId) => ({
      data: Number(applicationId) === 43 ? nextApplication : application,
    }));
    renderReport();

    fireEvent.click(await screen.findByRole('button', { name: 'Re-evaluate' }));
    await waitFor(() => expect(mocks.reEvaluateDecision).toHaveBeenCalledWith(91));
    fireEvent.click(screen.getByRole('button', { name: 'Open candidate 43' }));
    expect(await screen.findAllByText('Grace Hopper')).not.toHaveLength(0);

    await act(async () => {
      oldMutation.resolve({ data: {} });
      await oldMutation.promise;
    });
    await waitFor(() => {
      expect(screen.getAllByText('Grace Hopper')).not.toHaveLength(0);
      expect(screen.queryAllByText('Ada Lovelace')).toHaveLength(0);
    });
    expect(mocks.getApplication).toHaveBeenCalledTimes(2);
    expect(mocks.showToast).not.toHaveBeenCalledWith(
      'Re-evaluating with fresh inputs…',
      'success',
    );
  });

  it('does not inherit an old candidate rescore transition into the next route', async () => {
    const nextRoute = deferred();
    mocks.getApplication.mockReset().mockImplementation((applicationId) => (
      Number(applicationId) === 42
        ? Promise.resolve({ data: application })
        : nextRoute.promise
    ));
    mocks.listDecisions.mockReset()
      .mockResolvedValueOnce({ data: [{ ...rejectDecision, rescore_in_flight: true }] })
      .mockResolvedValue({ data: [refreshedDecision] });
    renderReport();

    expect(await screen.findByRole('button', { name: /^Reject$/i })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Open candidate 43' }));
    await waitFor(() => expect(
      mocks.getApplication.mock.calls.filter(([id]) => Number(id) === 43),
    ).toHaveLength(1));
    await act(async () => { await Promise.resolve(); });
    await act(async () => {
      nextRoute.resolve({ data: nextApplication });
      await nextRoute.promise;
    });

    expect(await screen.findAllByText('Grace Hopper')).not.toHaveLength(0);
    expect(mocks.getApplication.mock.calls.filter(([id]) => Number(id) === 43)).toHaveLength(1);
  });

  it('does not carry candidate-scoped note and link drafts into another route', async () => {
    mocks.getApplication.mockImplementation((applicationId) => ({
      data: Number(applicationId) === 43 ? nextApplication : application,
    }));
    renderReport();

    fireEvent.click(await screen.findByRole('tab', { name: 'Notes & timeline' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Add a hiring team note' }), {
      target: { value: 'Ada-only context' },
    });
    fireEvent.click(screen.getByRole('button', { name: /ADD SUPPORTING LINK/i }));
    fireEvent.change(screen.getByLabelText('URL'), {
      target: { value: 'https://example.com/ada-only' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Open candidate 43' }));
    expect(await screen.findAllByText('Grace Hopper')).not.toHaveLength(0);
    fireEvent.click(screen.getByRole('tab', { name: 'Notes & timeline' }));
    expect(screen.getByRole('textbox', { name: 'Add a hiring team note' })).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: /ADD SUPPORTING LINK/i }));
    expect(screen.getByLabelText('URL')).toHaveValue('');
  });

  it('archives an assessment with truthful reversible-data copy', async () => {
    const onNavigate = vi.fn();
    mocks.getApplication.mockResolvedValue({
      data: {
        ...application,
        valid_assessment_id: 77,
        valid_assessment_status: 'completed',
      },
    });
    mocks.getAssessment.mockResolvedValue({
      data: {
        id: 77,
        status: 'completed',
        candidate_name: application.candidate_name,
        score: 8,
        tests_passed: 4,
        tests_total: 5,
        timeline: [],
      },
    });
    mocks.removeAssessment.mockResolvedValue({ status: 204 });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderReport(onNavigate);

    fireEvent.click(await screen.findByRole('tab', { name: 'Assessment' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Actions' }));
    const archive = screen.getByRole('menuitem', { name: 'Archive assessment' });
    expect(screen.queryByText('Delete assessment')).toBeNull();
    fireEvent.click(archive);

    await waitFor(() => {
      expect(confirm).toHaveBeenCalledWith(
        'Archive this assessment? It will be hidden from active assessment views.',
      );
      expect(mocks.removeAssessment).toHaveBeenCalledWith(77);
      expect(mocks.showToast).toHaveBeenCalledWith('Assessment archived.', 'success');
      expect(onNavigate).toHaveBeenCalledWith('jobs');
    });
  });
});
