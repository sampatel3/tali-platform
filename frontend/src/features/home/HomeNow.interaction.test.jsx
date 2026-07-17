import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { vi } from 'vitest';

import { HomeNow } from './HomeNow';

// Interaction-correctness guards on the hub review queue:
//  - Enter in the bulk-approve modal must not submit until every advancing role
//    has a Workable stage picked (silent skips are forbidden).
//  - a/t/s action shortcuts must not fire while a confirm/override modal is open.

const approveDecision = vi.fn();
const bulkApproveDecisions = vi.fn();
const snoozeDecision = vi.fn();
const getWorkableStages = vi.fn();
const listDecisions = vi.fn().mockResolvedValue({ data: [] });

vi.mock('../../shared/api', () => ({
  agent: {
    approveDecision: (...a) => approveDecision(...a),
    bulkApproveDecisions: (...a) => bulkApproveDecisions(...a),
    bulkOverrideDecisions: vi.fn().mockResolvedValue({ data: {} }),
    overrideDecision: vi.fn().mockResolvedValue({ data: {} }),
    snoozeDecision: (...a) => snoozeDecision(...a),
    reEvaluateDecision: vi.fn().mockResolvedValue({ data: {} }),
    listDecisions: (...a) => listDecisions(...a),
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

const settleHomeMount = async () => {
  const requests = [listDecisions, getWorkableStages]
    .map((mock) => mock.mock.results.at(-1)?.value)
    .filter(Boolean);
  await act(async () => {
    await Promise.all(requests);
  });
};

describe('HomeNow — applied-date freshness', () => {
  it('shows when the candidate applied on the queue row and the detail card', async () => {
    getWorkableStages.mockReset().mockResolvedValue({ data: { stages: [] } });
    renderHome();
    await settleHomeMount();
    // Queue row: relative applied age next to the role · queue-age line.
    expect(screen.getAllByText(/applied .+ ago/i).length).toBeGreaterThan(0);
    // Detail card: absolute date line under the score provenance
    // (locale-agnostic — toLocaleDateString varies by environment).
    expect(screen.getByText(/Applied .*2026/)).toBeInTheDocument();
  });

  it('labels the shared ATS pool date consistently in the queue and detail card', () => {
    getWorkableStages.mockReset().mockResolvedValue({ data: { stages: [] } });
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
    getWorkableStages.mockReset().mockResolvedValue({ data: { stages: [] } });
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
  beforeEach(() => {
    getWorkableStages.mockReset().mockResolvedValue({ data: { stages: [] } });
  });

  it('uses the shared action styles while exposing filters and rows as pressed choices', async () => {
    const { container } = renderHome();
    await settleHomeMount();

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

  it('styles the secondary bulk action canonically and labels stale state as status', async () => {
    const decision = {
      ...mkAdvance(1, 'Miguel Parracho'),
      decision_type: 'send_assessment',
      is_stale: true,
    };
    const { container } = renderHome({
      decisions: [decision],
      pendingOrdered: [decision],
      filters: { status: 'pending', role_id: null, type: 'assessment', q: null },
    });
    await settleHomeMount();

    expect(screen.getByRole('button', { name: /Skip & advance 1 visible/i }))
      .toHaveClass('taali-btn-secondary', 'taali-btn-sm');
    expect(container.querySelector('.rq-qstale')).toHaveTextContent('score out of date');
    expect(screen.getByText('Assessment recommended').closest('button')).toBeNull();
  });

  it('can refresh a cached empty Workable stage list from the single advance action', async () => {
    getWorkableStages.mockReset().mockResolvedValue({ data: { stages: [] } });
    const { container } = renderHome();
    await settleHomeMount();

    const detail = container.querySelector('.rq-hybrid-detail');
    fireEvent.click(within(detail).getByRole('button', { name: 'Advance to next stage' }));
    expect(await screen.findByText(/Advance stays blocked/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Refresh stages' }));
    await waitFor(() => expect(getWorkableStages).toHaveBeenCalledTimes(2));
  });
});

describe('HomeNow — bulk-approve Enter gate', () => {
  beforeEach(() => {
    bulkApproveDecisions.mockReset().mockResolvedValue({ data: { accepted: 1, failures: [] } });
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

  it('blocks a Workable-linked advance when the job has no advanceable stages', async () => {
    getWorkableStages.mockReset().mockResolvedValue({ data: { stages: [] } });
    const { container } = renderHome();

    fireEvent.click(within(container).getByRole('button', { name: /Approve 1 visible/i }));
    expect(await within(container).findByText(/Approval is blocked: this Workable job has no advanceable stage/i))
      .toBeInTheDocument();

    const confirmBtn = within(container).getByRole('button', { name: /^Approve 1$/i });
    expect(confirmBtn).toBeDisabled();
    act(() => { fireEvent.keyDown(document, { key: 'Enter' }); });
    expect(bulkApproveDecisions).not.toHaveBeenCalled();

    fireEvent.click(within(container).getByRole('button', { name: /Refresh stages/i }));
    await waitFor(() => expect(getWorkableStages).toHaveBeenCalledTimes(2));
  });
});

describe('HomeNow — bulk reject blast radius', () => {
  beforeEach(() => {
    bulkApproveDecisions.mockReset().mockResolvedValue({
      data: { accepted: 1, failures: [] },
    });
  });

  it('names and submits every linked role before approving a reject recommendation', async () => {
    const reject = {
      ...mkAdvance(7, 'Aisha Khan'),
      decision_type: 'reject',
      recommendation: 'Reject',
      role_id: 31,
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

    fireEvent.click(within(container).getByRole('button', { name: /^Approve 1$/i }));
    await waitFor(() => expect(bulkApproveDecisions).toHaveBeenCalledTimes(1));
    expect(bulkApproveDecisions).toHaveBeenCalledWith(
      [7],
      null,
      null,
      {
        31: {
          owner: { id: 31, name: 'Data Platform Lead' },
          related: [{ id: 47, name: 'AI Engineer' }],
        },
      },
      { 7: 'reject' },
    );
  });

  it('submits the displayed family with a one-click reject approval', async () => {
    approveDecision.mockReset().mockResolvedValue({ data: { id: 7, status: 'processing' } });
    const reject = {
      ...mkAdvance(7, 'Aisha Khan'),
      decision_type: 'reject',
      recommendation: 'Reject',
      role_id: 31,
      role_family: {
        owner: { id: 31, name: 'Data Platform Lead' },
        related: [{ id: 47, name: 'AI Engineer' }],
      },
    };
    const { container } = renderHome({
      decisions: [reject],
      pendingOrdered: [reject],
    });

    const detail = container.querySelector('.rq-hybrid-detail');
    fireEvent.click(within(detail).getByRole('button', { name: /^Reject$/i }));

    await waitFor(() => expect(approveDecision).toHaveBeenCalledTimes(1));
    expect(approveDecision).toHaveBeenCalledWith(
      7,
      {
        expected_decision_type: 'reject',
        expected_role_family: {
          owner: { id: 31, name: 'Data Platform Lead' },
          related: [{ id: 47, name: 'AI Engineer' }],
        },
      },
      { force: false },
    );
  });
});

describe('HomeNow — action shortcuts are suppressed while a modal is open', () => {
  beforeEach(() => {
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
});
