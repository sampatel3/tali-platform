import { act, fireEvent, render, waitFor, within } from '@testing-library/react';
import { vi } from 'vitest';

import { HomeNow } from './HomeNow';

// Approving a decision is async server-side (the backend flips it to
// ``processing`` and runs the heavy send in a worker), so the Hub reflects the
// action OPTIMISTICALLY: the card leaves the queue the instant you click, and
// only reappears if the send actually fails. These tests pin that behaviour.

const approveDecision = vi.fn();
const bulkApproveDecisions = vi.fn();
const bulkOverrideDecisions = vi.fn();
const listDecisions = vi.fn().mockResolvedValue({ data: [] });

vi.mock('../../shared/api', () => ({
  agent: {
    approveDecision: (...a) => approveDecision(...a),
    bulkApproveDecisions: (...a) => bulkApproveDecisions(...a),
    bulkOverrideDecisions: (...a) => bulkOverrideDecisions(...a),
    snoozeDecision: vi.fn().mockResolvedValue({ data: {} }),
    reEvaluateDecision: vi.fn().mockResolvedValue({ data: {} }),
    listDecisions: (...a) => listDecisions(...a),
  },
  organizations: {
    getWorkableStages: vi.fn().mockResolvedValue({ data: { stages: [] } }),
  },
}));

const mkDecision = (id, name, { role_id = 53, role_name = 'Data Engineer' } = {}) => ({
  id,
  decision_type: 'send_assessment',
  status: 'pending',
  candidate_name: name,
  candidate_email: `${name.split(' ')[0].toLowerCase()}@example.com`,
  application_id: id * 10,
  role_id,
  role_name,
  created_at: '2026-06-07T10:00:00Z',
  reasoning: 'Strong fit.',
  taali_score: 80,
});

const renderHome = (overrides = {}) => {
  const decisions = [mkDecision(1, 'Miguel Parracho'), mkDecision(2, 'Ada Lovelace')];
  const setSelectedId = vi.fn();
  const reload = vi.fn().mockResolvedValue(undefined);
  const utils = render(
    <HomeNow
      decisions={decisions}
      pendingOrdered={decisions}
      selectedId={1}
      setSelectedId={setSelectedId}
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
  return { ...utils, setSelectedId, reload };
};

const sidebarOf = (container) => container.querySelector('.rq-split-list');

describe('HomeNow — optimistic Send assessment', () => {
  beforeEach(() => {
    approveDecision.mockReset();
    bulkApproveDecisions.mockReset();
    bulkOverrideDecisions.mockReset();
    listDecisions.mockReset().mockResolvedValue({ data: [] });
  });

  it('drops the card from the queue and advances selection the instant you click — before the network resolves', async () => {
    let resolveApprove;
    approveDecision.mockImplementation(() => new Promise((r) => { resolveApprove = r; }));

    const { container, setSelectedId, reload } = renderHome();
    const sidebar = sidebarOf(container);

    // Both candidates are in the pending queue to start.
    expect(within(sidebar).getByText('Miguel Parracho')).toBeInTheDocument();
    expect(within(sidebar).getByText('Ada Lovelace')).toBeInTheDocument();

    const detail = container.querySelector('.rq-hybrid-detail');
    const sendBtn = within(detail).getByRole('button', { name: /send assessment/i });
    await act(async () => { fireEvent.click(sendBtn); });

    // The request fired with the focused decision (not stale → force:false)...
    expect(approveDecision).toHaveBeenCalledTimes(1);
    expect(approveDecision).toHaveBeenCalledWith(
      1,
      { expected_decision_type: 'send_assessment' },
      { force: false },
    );
    // ...and the UI already moved on though the promise is still pending:
    // the approved card left the queue and selection advanced to the next.
    expect(within(sidebar).queryByText('Miguel Parracho')).not.toBeInTheDocument();
    expect(within(sidebar).getByText('Ada Lovelace')).toBeInTheDocument();
    expect(setSelectedId).toHaveBeenCalledWith(2);
    // The heavy reload hasn't been awaited yet — the click didn't block on it.
    expect(reload).not.toHaveBeenCalled();

    // Let it settle so the test tears down cleanly.
    await act(async () => { resolveApprove({ data: { id: 1, status: 'processing' } }); });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it('returns the card to the queue when the send fails (never silently dropped)', async () => {
    approveDecision.mockRejectedValue(new Error('boom'));

    const { container, setSelectedId } = renderHome();
    const sidebar = sidebarOf(container);

    const detail = container.querySelector('.rq-hybrid-detail');
    const sendBtn = within(detail).getByRole('button', { name: /send assessment/i });
    await act(async () => { fireEvent.click(sendBtn); });

    // After the failure reconciles, the card is back in the queue and refocused.
    await waitFor(() => {
      expect(within(sidebar).getByText('Miguel Parracho')).toBeInTheDocument();
    });
    expect(setSelectedId).toHaveBeenCalledWith(1);
  });

  it('bulk "Skip & advance" overrides every visible card optimistically', async () => {
    let resolveBulk;
    bulkOverrideDecisions.mockImplementation(() => new Promise((r) => { resolveBulk = r; }));

    // The bulk button only renders on the Send view (type: 'assessment') —
    // there the count matches exactly the cards on screen.
    const { container, reload } = renderHome({
      filters: { status: 'pending', role_id: null, type: 'assessment', q: null },
    });
    const sidebar = sidebarOf(container);
    expect(within(sidebar).getByText('Miguel Parracho')).toBeInTheDocument();
    expect(within(sidebar).getByText('Ada Lovelace')).toBeInTheDocument();

    const skipBtn = within(container).getByRole('button', { name: /skip & advance 2 visible/i });
    await act(async () => { fireEvent.click(skipBtn); });

    // One bulk request with both visible ids + the skip-and-advance action.
    expect(bulkOverrideDecisions).toHaveBeenCalledTimes(1);
    expect(bulkOverrideDecisions).toHaveBeenCalledWith(
      [1, 2],
      'skip_assessment_advance',
      null,
      null,
      null,
      { 1: 'send_assessment', 2: 'send_assessment' },
    );
    // Optimistic: both cards left the queue immediately (promise still pending,
    // reload not yet awaited).
    expect(within(sidebar).queryByText('Miguel Parracho')).not.toBeInTheDocument();
    expect(within(sidebar).queryByText('Ada Lovelace')).not.toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();

    await act(async () => { resolveBulk({ data: { requested: 2, accepted: 2, failures: [] } }); });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it('clamps rows to the Send filter during revalidation, so the bulk count matches the screen', async () => {
    // Stale-while-revalidate window: the parent still holds the previous
    // (mixed "All") rows while filters.type has already moved to 'assessment'.
    // Without a client-side type guard the bulk button would sit over a mixed
    // list and act on an invisible subset — the mismatch this gate removes.
    const mixed = [
      mkDecision(1, 'Sandy Sender'),
      { ...mkDecision(2, 'Andy Advancer'), decision_type: 'advance_to_interview' },
    ];
    const { container } = renderHome({
      decisions: mixed,
      pendingOrdered: mixed,
      filters: { status: 'pending', role_id: null, type: 'assessment', q: null },
    });
    await act(async () => {
      await listDecisions.mock.results.at(-1).value;
    });
    const sidebar = sidebarOf(container);
    expect(within(sidebar).getByText('Sandy Sender')).toBeInTheDocument();
    expect(within(sidebar).queryByText('Andy Advancer')).not.toBeInTheDocument();
    expect(within(container).getByRole('button', { name: /skip & advance 1 visible/i })).toBeInTheDocument();
  });

  it('hides bulk "Skip & advance" outside the Send view — a mixed queue can\'t show which cards it targets', async () => {
    // Same assessment decisions, but the type filter is All (null): the bulk
    // approve stays, the bulk skip & advance is gone. (The per-card
    // "Skip & advance" override in the detail pane is unaffected, so match
    // the bulk button's "N visible" wording.)
    const { container } = renderHome();
    await act(async () => {
      await listDecisions.mock.results.at(-1).value;
    });
    expect(within(container).getByRole('button', { name: /approve 2 visible/i })).toBeInTheDocument();
    expect(within(container).queryByRole('button', { name: /skip & advance \d+ visible/i })).not.toBeInTheDocument();
  });
});

describe('HomeNow — role-scope guard', () => {
  // The parent fetches role-scoped data, but stale-while-revalidate keeps the
  // previous scope's rows on screen while a role switch is in flight. Without a
  // client-side guard the queue shows other roles' candidates under the newly
  // selected role's funnel — what a recruiter sees as "I selected AI Engineer
  // but the list still shows everyone." This pins that the displayed queue +
  // feed only ever contain rows for the selected role.
  it('shows only the selected role’s candidates even when the parent still holds another role’s rows', async () => {
    // Simulates the in-flight window: pendingOrdered/decisions still carry the
    // previous (all-roles) result while filters.role_id has already moved to 31.
    const mixed = [
      mkDecision(1, 'Ada AiEngineer', { role_id: 31, role_name: 'AI Engineer' }),
      mkDecision(2, 'Glen Glue', { role_id: 53, role_name: 'AWS Glue Data Engineer' }),
      mkDecision(3, 'Cleo Cloud', { role_id: 77, role_name: 'Senior Cloud Solutions Architect' }),
    ];
    const { container } = renderHome({
      decisions: mixed,
      pendingOrdered: mixed,
      selectedId: 1,
      filters: { status: 'pending', role_id: 31, type: null, q: null },
    });
    await act(async () => {
      await listDecisions.mock.results.at(-1).value;
    });
    const sidebar = sidebarOf(container);

    expect(within(sidebar).getByText('Ada AiEngineer')).toBeInTheDocument();
    expect(within(sidebar).queryByText('Glen Glue')).not.toBeInTheDocument();
    expect(within(sidebar).queryByText('Cleo Cloud')).not.toBeInTheDocument();
    // The queue count reflects the scoped set, not the stale all-roles list.
    expect(within(sidebar).getByText('1')).toBeInTheDocument();
  });

  it('shows every role’s candidates when no role filter is set', async () => {
    const mixed = [
      mkDecision(1, 'Ada AiEngineer', { role_id: 31, role_name: 'AI Engineer' }),
      mkDecision(2, 'Glen Glue', { role_id: 53, role_name: 'AWS Glue Data Engineer' }),
    ];
    const { container } = renderHome({
      decisions: mixed,
      pendingOrdered: mixed,
      selectedId: 1,
      filters: { status: 'pending', role_id: null, type: null, q: null },
    });
    await act(async () => {
      await listDecisions.mock.results.at(-1).value;
    });
    const sidebar = sidebarOf(container);

    expect(within(sidebar).getByText('Ada AiEngineer')).toBeInTheDocument();
    expect(within(sidebar).getByText('Glen Glue')).toBeInTheDocument();
  });
});
