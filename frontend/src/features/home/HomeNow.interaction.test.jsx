import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, vi } from 'vitest';

import { HomeNow } from './HomeNow';
import { resetOptimisticDecisions } from './optimisticDecisionStore';

// Interaction-correctness guards on the hub review queue:
//  - Enter in the bulk-approve modal must not submit until every advancing role
//    has a Workable stage picked (silent skips are forbidden).
//  - a/t/s action shortcuts must not fire while a confirm/override modal is open.

const approveDecision = vi.fn();
const bulkApproveDecisions = vi.fn();
const snoozeDecision = vi.fn();
const getWorkableStages = vi.fn();
const showToast = vi.fn();

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

vi.mock('./RecentDecisions', () => ({
  RecentDecisions: () => null,
}));

vi.mock('../../shared/api', () => ({
  agent: {
    approveDecision: (...a) => approveDecision(...a),
    bulkApproveDecisions: (...a) => bulkApproveDecisions(...a),
    bulkOverrideDecisions: vi.fn().mockResolvedValue({ data: {} }),
    overrideDecision: vi.fn().mockResolvedValue({ data: {} }),
    snoozeDecision: (...a) => snoozeDecision(...a),
    reEvaluateDecision: vi.fn().mockResolvedValue({ data: {} }),
    listDecisions: vi.fn().mockResolvedValue({ data: [] }),
  },
  organizations: {
    getWorkableStages: (...a) => getWorkableStages(...a),
  },
}));

const mkAdvance = (id, name) => ({
  id,
  decision_type: 'advance_to_interview',
  status: 'pending',
  candidate_name: name,
  candidate_email: `${name.split(' ')[0].toLowerCase()}@example.com`,
  application_id: id * 10,
  role_id: 53,
  role_name: 'Data Engineer',
  workable_job_id: 'de-shortcode',
  created_at: '2026-06-07T10:00:00Z',
  applied_at: '2026-06-01T10:00:00Z',
  reasoning: 'Strong fit.',
  taali_score: 80,
});

const renderHome = (overrides = {}) => {
  const decisions = [mkAdvance(1, 'Miguel Parracho')];
  const reload = vi.fn().mockResolvedValue(undefined);
  const utils = render(
    <HomeNow
      decisions={decisions}
      pendingOrdered={decisions}
      selectedId={1}
      setSelectedId={vi.fn()}
      loading={false}
      filters={{ status: 'pending', role_id: null, type: null, q: null }}
      setFilters={vi.fn()}
      rolesBreakdown={[]}
      reload={reload}
      onNavigate={vi.fn()}
      questionsInDock
      {...overrides}
    />,
  );
  return { ...utils, reload };
};

beforeEach(() => {
  resetOptimisticDecisions();
  // Most tests do not exercise stage loading. Keep the lazy request pending so
  // it cannot schedule an unrelated state update after a synchronous assertion.
  getWorkableStages.mockReset().mockReturnValue(new Promise(() => {}));
});

describe('HomeNow — applied-date freshness', () => {
  it('shows when the candidate applied on the queue row and the detail card', () => {
    renderHome();
    // Queue row: relative applied age next to the role · queue-age line.
    expect(screen.getAllByText(/applied .+ ago/i).length).toBeGreaterThan(0);
    // Detail card: absolute date line under the score provenance
    // (locale-agnostic — toLocaleDateString varies by environment).
    expect(screen.getByText(/Applied .*2026/)).toBeInTheDocument();
  });

  it('labels the shared ATS pool date consistently in the queue and detail card', () => {
    const related = {
      ...mkAdvance(1, 'Miguel Parracho'),
      role_family: {
        owner: { id: 31, name: 'Data Platform Lead' },
        related: [{ id: 53, name: 'Data Engineer' }],
      },
    };
    renderHome({ decisions: [related], pendingOrdered: [related] });

    expect(screen.getAllByText(/In shared ATS pool since/i)).toHaveLength(2);
    expect(screen.queryByText(/^Applied .*2026/i)).not.toBeInTheDocument();
  });

  it('keeps the decision role in every candidate-report link', () => {
    renderHome();

    const reportLinks = [
      screen.getByRole('link', { name: 'Candidate report' }),
      ...screen.getAllByRole('link', { name: 'Miguel Parracho' }),
    ];
    expect(reportLinks.length).toBeGreaterThan(1);
    reportLinks.forEach((link) => {
      expect(link).toHaveAttribute('href', '/candidates/10?from=home&view_role_id=53');
    });
  });
});

describe('HomeNow — action and selection semantics', () => {
  it('uses the shared action styles while exposing filters and rows as pressed choices', () => {
    const { container } = renderHome();

    const filterGroup = screen.getByRole('group', { name: /filter by decision type/i });
    expect(within(filterGroup).getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(filterGroup).getByRole('button', { name: 'Advance' })).toHaveAttribute('aria-pressed', 'false');

    const selectedRow = container.querySelector('.rq-qrow');
    expect(selectedRow).toHaveAttribute('aria-pressed', 'true');

    const approveVisible = screen.getByRole('button', { name: /Approve 1 visible/i });
    expect(approveVisible).toHaveClass('taali-btn-primary', 'taali-btn-sm');

    const recommendation = screen.getByText('Advance recommended');
    expect(recommendation.tagName).toBe('SPAN');
    expect(recommendation.closest('button')).toBeNull();
  });

  it('excludes changed-input stale rows from every bulk action', () => {
    const decision = {
      ...mkAdvance(1, 'Miguel Parracho'),
      decision_type: 'send_assessment',
      is_stale: true,
      staleness_reasons: ['score_generation_changed'],
    };
    const { container } = renderHome({
      decisions: [decision],
      pendingOrdered: [decision],
      filters: { status: 'pending', role_id: null, type: 'assessment', q: null },
    });

    expect(screen.queryByRole('button', { name: /Approve 1 visible/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Skip & advance 1 visible/i })).not.toBeInTheDocument();
    expect(container.querySelector('.rq-qstale')).toHaveTextContent('score out of date');
    expect(screen.getByText('Assessment recommended').closest('button')).toBeNull();
  });

  it('keeps old-engine-only rows out of bulk approval but available for bounded single approval', () => {
    const decision = {
      ...mkAdvance(1, 'Miguel Parracho'),
      is_stale: true,
      staleness_reasons: ['engine_outdated'],
    };
    renderHome({ decisions: [decision], pendingOrdered: [decision] });

    expect(screen.queryByRole('button', { name: /Approve 1 visible/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Advance to next stage/i })).toBeEnabled();
  });

  it("does not let the 'a' shortcut bypass changed-input staleness", () => {
    approveDecision.mockClear();
    const decision = {
      ...mkAdvance(1, 'Miguel Parracho'),
      is_stale: true,
      staleness_reasons: ['score_generation_changed'],
    };
    renderHome({ decisions: [decision], pendingOrdered: [decision] });

    act(() => { fireEvent.keyDown(document, { key: 'a' }); });

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(approveDecision).not.toHaveBeenCalled();
  });
});

describe('HomeNow — bulk-approve Enter gate', () => {
  beforeEach(() => {
    bulkApproveDecisions.mockReset().mockResolvedValue({ data: { approved: 1, failures: [] } });
    // A role WITH advanceable stages, so a stage pick is genuinely required.
    getWorkableStages.mockReset().mockResolvedValue({
      data: { stages: [{ slug: 'phone_screen', name: 'Phone screen', kind: 'interview' }] },
    });
  });

  it('Enter does NOT submit the bulk approve while a required stage is unpicked', async () => {
    const { container } = renderHome();

    // Open the bulk-approve modal.
    fireEvent.click(within(container).getByRole('button', { name: /Approve 1 visible/i }));
    // Stages load into the modal.
    await waitFor(() => expect(getWorkableStages).toHaveBeenCalled());
    await within(container).findByText(/Move advancing candidates to which Workable stage/i);

    // Enter with no stage picked — the empty-map submit that used to slip
    // through must be blocked.
    act(() => { fireEvent.keyDown(document, { key: 'Enter' }); });
    expect(bulkApproveDecisions).not.toHaveBeenCalled();

    // Confirm button is disabled too (the gate the button already had).
    const confirmBtn = within(container).getByRole('button', { name: /^Approve 1$/i });
    expect(confirmBtn).toBeDisabled();
  });

  it('Enter submits once every advancing role has a stage picked', async () => {
    const { container } = renderHome();
    fireEvent.click(within(container).getByRole('button', { name: /Approve 1 visible/i }));
    await waitFor(() => expect(getWorkableStages).toHaveBeenCalled());

    // Pick the stage.
    const pill = await within(container).findByRole('radio', { name: /Phone screen/i });
    fireEvent.click(pill);

    act(() => { fireEvent.keyDown(document, { key: 'Enter' }); });
    await waitFor(() => expect(bulkApproveDecisions).toHaveBeenCalledTimes(1));
    // The stage map is sent, not null.
    const [, , stages] = bulkApproveDecisions.mock.calls[0];
    expect(stages).toEqual({ 53: 'phone_screen' });
  });
});

describe('HomeNow — bulk reject blast radius', () => {
  it('names every linked role before approving a reject recommendation', () => {
    const reject = {
      ...mkAdvance(7, 'Aisha Khan'),
      decision_type: 'reject',
      recommendation: 'Reject',
      workable_job_id: 'de-shortcode',
      role_family: {
        owner: { id: 31, name: 'Data Platform Lead' },
        related: [{ id: 47, name: 'AI Engineer' }],
      },
    };
    const { container } = renderHome({
      decisions: [reject],
      pendingOrdered: [reject],
    });

    fireEvent.click(within(container).getByRole('button', { name: /Approve 1 visible/i }));

    expect(within(container).getByRole('alert')).toHaveTextContent(
      'Data Platform Lead #31 (original) and AI Engineer #47 (related)',
    );
  });
});

describe('HomeNow — action shortcuts are suppressed while a modal is open', () => {
  beforeEach(() => {
    showToast.mockReset();
    snoozeDecision.mockReset().mockResolvedValue({ data: {} });
    getWorkableStages.mockReset().mockResolvedValue({
      data: { stages: [{ slug: 'phone_screen', name: 'Phone screen', kind: 'interview' }] },
    });
  });

  it("'s' does not snooze the decision behind an open Override/Advance-confirm modal", async () => {
    const { container } = renderHome();

    // Pressing 'a' on the selected advance decision opens the confirm modal
    // (alternativeFor) — an advance_to_interview approval routes through it.
    act(() => { fireEvent.keyDown(document, { key: 'a' }); });
    await within(container).findByRole('dialog');

    // With the modal open, 's' must not reach the decision underneath.
    act(() => { fireEvent.keyDown(document, { key: 's' }); });
    expect(snoozeDecision).not.toHaveBeenCalled();
  });

  it('keeps a modal-approved advance read-only when its refresh fails', async () => {
    approveDecision.mockReset().mockResolvedValue({
      data: { decision_id: 1, accepted: true },
    });
    const reload = vi.fn().mockRejectedValue(new Error('refresh unavailable'));
    const { container } = renderHome({ reload });

    fireEvent.click(screen.getByRole('button', { name: /advance to next stage/i }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('radio', { name: /phone screen/i }));
    fireEvent.click(within(dialog).getByRole('button', { name: /^advance$/i }));

    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(approveDecision).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ workable_target_stage: 'phone_screen' }),
      { force: false },
    );
    const row = within(container.querySelector('.rq-split-list'))
      .getByText('Miguel Parracho')
      .closest('.rq-qrow');
    expect(row).toHaveClass('is-processing');
    expect(container.querySelector('.rq-action-bar')).not.toBeInTheDocument();
    expect(showToast).toHaveBeenCalledWith('Advance dispatched.', 'success');
  });
});
