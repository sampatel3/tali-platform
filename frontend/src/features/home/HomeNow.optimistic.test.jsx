import { act, fireEvent, render, waitFor, within } from '@testing-library/react';
import { vi } from 'vitest';

import { HomeNow } from './HomeNow';
import { resetOptimisticDecisions } from './optimisticDecisionStore';
import { clearCache } from '../../shared/api/resourceCache';

// Approving a decision is async server-side (the backend flips it to
// ``processing`` and runs the heavy send in a worker), so the Hub reflects the
// action OPTIMISTICALLY: the card stays visible but greys out the instant you
// click, then becomes actionable again only if the send actually fails.

const approveDecision = vi.fn();
const bulkApproveDecisions = vi.fn();
const bulkOverrideDecisions = vi.fn();
const listDecisions = vi.fn();

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

vi.mock('./RecentDecisions', () => ({
  RecentDecisions: () => null,
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
  let props = {
    decisions,
    pendingOrdered: decisions,
    selectedId: 1,
    setSelectedId,
    loading: false,
    filters: { status: 'pending', role_id: null, type: null, q: null },
    setFilters: vi.fn(),
    rolesBreakdown: [],
    reload,
    decisionScopeKey: 'scope:all',
    decisionRevision: 0,
    decisionRevisionScopeKey: 'scope:all',
    onNavigate: vi.fn(),
    questionsInDock: true,
    ...overrides,
  };
  const utils = render(<HomeNow {...props} />);
  const rerenderHome = (next = {}) => {
    props = { ...props, ...next };
    utils.rerender(<HomeNow {...props} />);
  };
  return { ...utils, setSelectedId, reload: props.reload, rerenderHome };
};

const sidebarOf = (container) => container.querySelector('.rq-split-list');

describe('HomeNow — optimistic Send assessment', () => {
  beforeEach(() => {
    resetOptimisticDecisions();
    clearCache();
    approveDecision.mockReset();
    bulkApproveDecisions.mockReset();
    bulkOverrideDecisions.mockReset();
    listDecisions.mockReset().mockResolvedValue({ data: [] });
  });

  it('greys the card and advances selection the instant you click — before the network resolves', async () => {
    let resolveApprove;
    approveDecision.mockImplementation(() => new Promise((r) => { resolveApprove = r; }));

    const queue = [mkDecision(1, 'Miguel Parracho'), mkDecision(2, 'Ada Lovelace')];
    const { container, setSelectedId, reload, rerenderHome } = renderHome({
      decisions: queue,
      pendingOrdered: queue,
    });
    reload.mockResolvedValue({
      applied: false,
      reason: 'superseded',
      ticket: 7,
      scopeKey: 'scope:all',
    });
    const sidebar = sidebarOf(container);

    // Both candidates are in the pending queue to start.
    expect(within(sidebar).getByText('Miguel Parracho')).toBeInTheDocument();
    expect(within(sidebar).getByText('Ada Lovelace')).toBeInTheDocument();

    const detail = container.querySelector('.rq-hybrid-detail');
    const sendBtn = within(detail).getByRole('button', { name: /send assessment/i });
    await act(async () => { fireEvent.click(sendBtn); });

    // The request fired with the focused decision (not stale → force:false)...
    expect(approveDecision).toHaveBeenCalledTimes(1);
    expect(approveDecision).toHaveBeenCalledWith(1, {}, { force: false });
    // ...and the UI already moved on though the promise is still pending:
    // the approved card remains as a grey processing acknowledgement while
    // selection advances to the next actionable row.
    const processingRow = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(processingRow).toHaveClass('is-processing');
    expect(within(sidebar).getByText('Ada Lovelace')).toBeInTheDocument();
    expect(sidebar.querySelectorAll('.rq-qrow')[0]).toBe(processingRow);
    expect(setSelectedId).toHaveBeenCalledWith(2);
    // The heavy reload hasn't been awaited yet — the click didn't block on it.
    expect(reload).not.toHaveBeenCalled();

    // Let it settle so the test tears down cleanly.
    await act(async () => { resolveApprove({ data: { id: 1, status: 'processing' } }); });
    await waitFor(() => expect(reload).toHaveBeenCalled());

    // A reload attempt that lost to a poll is not authoritative: stale pending
    // props must not make the accepted row actionable again.
    expect(processingRow).toHaveClass('is-processing');

    // A same-scope snapshot from before the post-accept ticket is also stale.
    rerenderHome({ decisionRevision: 6, decisionRevisionScopeKey: 'scope:all' });
    expect(processingRow).toHaveClass('is-processing');

    // A different filter scope cannot settle by absence alone.
    rerenderHome({
      decisionScopeKey: 'scope:other',
      decisionRevision: 8,
      decisionRevisionScopeKey: 'scope:other',
      decisions: [queue[1]],
      pendingOrdered: [queue[1]],
    });
    expect(within(sidebar).queryByText('Miguel Parracho')).not.toBeInTheDocument();
    rerenderHome({
      decisionScopeKey: 'scope:all',
      decisionRevision: 6,
      decisionRevisionScopeKey: 'scope:all',
      decisions: queue,
      pendingOrdered: queue,
    });
    expect(within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow')).toHaveClass('is-processing');

    // The winning same-scope poll is authoritative. Because its raw row is
    // pending here, this represents a worker return and restores actionability.
    rerenderHome({ decisionRevision: 9, decisionRevisionScopeKey: 'scope:all' });
    await waitFor(() => {
      expect(within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow'))
        .not.toHaveClass('is-processing');
    });
  });

  it('lets another scope settle when its authoritative rows include the same decision', async () => {
    approveDecision.mockResolvedValue({ data: { decision_id: 1, accepted: true } });
    const reload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'superseded',
      ticket: 4,
      scopeKey: 'scope:all',
    });
    const { container, rerenderHome } = renderHome({ reload });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container.querySelector('.rq-hybrid-detail'))
      .getByRole('button', { name: /send assessment/i }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    const row = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(row).toHaveClass('is-processing');

    // The different query scope still returned this exact ID, so its raw
    // pending state is authoritative for the row and may release the lock.
    rerenderHome({
      decisionScopeKey: 'scope:search',
      decisionRevision: 5,
      decisionRevisionScopeKey: 'scope:search',
    });
    await waitFor(() => expect(row).not.toHaveClass('is-processing'));
  });

  it('returns the card to the queue when the send fails (never silently dropped)', async () => {
    approveDecision.mockRejectedValue({
      response: {
        status: 503,
        data: { detail: "We couldn't accept this action. Nothing was sent; please try again." },
      },
    });

    const { container, setSelectedId } = renderHome();
    const sidebar = sidebarOf(container);

    const detail = container.querySelector('.rq-hybrid-detail');
    const sendBtn = within(detail).getByRole('button', { name: /send assessment/i });
    await act(async () => { fireEvent.click(sendBtn); });

    // After the failure reconciles, the card is back in the queue and refocused.
    await waitFor(() => {
      const returnedRow = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
      expect(returnedRow).not.toHaveClass('is-processing');
    });
    expect(setSelectedId).toHaveBeenCalledWith(1);
  });

  it('keeps an outcome-unknown rejection locked until processing is observed', async () => {
    const timeout = Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' });
    approveDecision.mockRejectedValue(timeout);
    const reload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'error',
      ticket: 13,
      scopeKey: 'scope:all',
    });
    const { container, setSelectedId, rerenderHome } = renderHome({ reload });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container.querySelector('.rq-hybrid-detail'))
      .getByRole('button', { name: /send assessment/i }));

    await waitFor(() => expect(reload).toHaveBeenCalled());
    const row = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(row).toHaveClass('is-processing');
    expect(setSelectedId).not.toHaveBeenCalledWith(1);

    rerenderHome({ decisionRevision: 14, decisionRevisionScopeKey: 'scope:all' });
    expect(row).toHaveClass('is-processing');

    // Arbitrarily later pending snapshots are not causal proof that the timed
    // out POST failed, so they cannot unlock the row.
    rerenderHome({ decisionRevision: 99, decisionRevisionScopeKey: 'scope:all' });
    expect(row).toHaveClass('is-processing');

    // Same-scope absence is not proof either: the pending endpoint is capped,
    // and a busy agent run can push this row outside the returned page.
    rerenderHome({
      decisions: [mkDecision(2, 'Ada Lovelace')],
      pendingOrdered: [mkDecision(2, 'Ada Lovelace')],
      decisionRevision: 100,
      decisionRevisionScopeKey: 'scope:all',
    });
    expect(within(sidebar).queryByText('Miguel Parracho')).not.toBeInTheDocument();

    // A search can surface that omitted row again; the cross-scope tombstone
    // must still make it read-only until a real processing transition appears.
    const pending = [mkDecision(1, 'Miguel Parracho'), mkDecision(2, 'Ada Lovelace')];
    rerenderHome({
      decisionScopeKey: 'scope:search',
      decisions: pending,
      pendingOrdered: pending,
      decisionRevision: 101,
      decisionRevisionScopeKey: 'scope:search',
    });
    const searchedRow = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(searchedRow).toHaveClass('is-processing');

    // Once processing was actually observed, a subsequent pending snapshot is
    // proof that the worker returned the decision and may restore actionability.
    const processing = { ...mkDecision(1, 'Miguel Parracho'), status: 'processing' };
    rerenderHome({
      decisions: [processing, mkDecision(2, 'Ada Lovelace')],
      pendingOrdered: [processing, mkDecision(2, 'Ada Lovelace')],
      decisionRevision: 102,
      decisionRevisionScopeKey: 'scope:search',
    });
    await waitFor(() => expect(searchedRow).toHaveClass('is-processing'));
    rerenderHome({
      decisions: pending,
      pendingOrdered: pending,
      decisionRevision: 103,
      decisionRevisionScopeKey: 'scope:search',
    });
    await waitFor(() => expect(searchedRow).not.toHaveClass('is-processing'));
  });

  it('does not treat a raced reverted snapshot as terminal after an unknown outcome', async () => {
    const timeout = Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' });
    approveDecision.mockRejectedValue(timeout);
    const reverted = {
      ...mkDecision(1, 'Miguel Parracho'),
      status: 'reverted_for_feedback',
    };
    const pending = mkDecision(2, 'Ada Lovelace');
    const reload = vi.fn().mockResolvedValue({
      applied: true,
      ticket: 31,
      scopeKey: 'scope:all',
    });
    const { container, rerenderHome } = renderHome({
      decisions: [reverted, pending],
      pendingOrdered: [reverted, pending],
      reload,
    });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container.querySelector('.rq-hybrid-detail'))
      .getByRole('button', { name: /send assessment/i }));
    await waitFor(() => expect(reload).toHaveBeenCalled());

    const row = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(row).toHaveClass('is-processing');

    rerenderHome({ decisionRevision: 31, decisionRevisionScopeKey: 'scope:all' });
    expect(row).toHaveClass('is-processing');
    rerenderHome({ decisionRevision: 99, decisionRevisionScopeKey: 'scope:all' });
    expect(row).toHaveClass('is-processing');
    expect(approveDecision).toHaveBeenCalledOnce();
  });

  it('unlocks an outcome-unknown reverted row when a new worker retry note is observed', async () => {
    const timeout = Object.assign(new Error('timed out'), { code: 'ETIMEDOUT' });
    approveDecision.mockRejectedValue(timeout);
    const reverted = {
      ...mkDecision(1, 'Miguel Parracho'),
      status: 'reverted_for_feedback',
      resolution_note: 'Recruiter feedback saved.',
    };
    const pending = mkDecision(2, 'Ada Lovelace');
    const reload = vi.fn().mockResolvedValue({
      applied: true,
      ticket: 41,
      scopeKey: 'scope:all',
    });
    const { container, rerenderHome } = renderHome({
      decisions: [reverted, pending],
      pendingOrdered: [reverted, pending],
      reload,
    });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container.querySelector('.rq-hybrid-detail'))
      .getByRole('button', { name: /send assessment/i }));
    await waitFor(() => expect(reload).toHaveBeenCalled());

    const row = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(row).toHaveClass('is-processing');

    const returned = {
      ...reverted,
      resolution_note: 'Returned to queue after an unexpected error. Please try approving it again.',
    };
    rerenderHome({
      decisions: [returned, pending],
      pendingOrdered: [returned, pending],
      decisionRevision: 42,
      decisionRevisionScopeKey: 'scope:all',
    });

    await waitFor(() => expect(row).not.toHaveClass('is-processing'));
    expect(within(container.querySelector('.rq-hybrid-detail'))
      .getByRole('button', { name: /send assessment/i })).toBeEnabled();
    expect(approveDecision).toHaveBeenCalledOnce();
  });

  it('advances selection to the next reverted actionable row', async () => {
    let resolveApprove;
    approveDecision.mockImplementation(() => new Promise((resolve) => { resolveApprove = resolve; }));
    const pending = mkDecision(1, 'Miguel Parracho');
    const reverted = {
      ...mkDecision(2, 'Ada Lovelace'),
      status: 'reverted_for_feedback',
    };
    const { container, setSelectedId } = renderHome({
      decisions: [pending, reverted],
      pendingOrdered: [pending, reverted],
    });

    fireEvent.click(within(container.querySelector('.rq-hybrid-detail'))
      .getByRole('button', { name: /send assessment/i }));

    expect(setSelectedId).toHaveBeenCalledWith(2);
    await act(async () => { resolveApprove({ data: { decision_id: 1, accepted: true } }); });
  });

  it('includes fresh reverted rows in the visible bulk approval count', () => {
    const pending = mkDecision(1, 'Miguel Parracho');
    const reverted = {
      ...mkDecision(2, 'Ada Lovelace'),
      status: 'reverted_for_feedback',
    };
    const { container } = renderHome({
      decisions: [pending, reverted],
      pendingOrdered: [pending, reverted],
    });

    expect(within(container).getByRole('button', { name: /approve 2 visible/i }))
      .toBeEnabled();
  });

  it('keeps an accepted modal advance grey until its scoped refresh wins', async () => {
    const advance = { ...mkDecision(1, 'Miguel Parracho'), decision_type: 'advance_to_interview' };
    const pending = mkDecision(2, 'Ada Lovelace');
    approveDecision.mockResolvedValue({ data: { decision_id: 1, accepted: true } });
    const reload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'superseded',
      ticket: 9,
      scopeKey: 'scope:all',
    });
    const { container, rerenderHome } = renderHome({
      decisions: [advance, pending],
      pendingOrdered: [advance, pending],
      reload,
    });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container).getByRole('button', { name: /advance to next stage/i }));
    const dialog = within(container).getByRole('dialog', { name: /advance miguel parracho/i });
    fireEvent.click(within(dialog).getByRole('button', { name: /^advance$/i }));

    await waitFor(() => expect(approveDecision).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    const row = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(row).toHaveClass('is-processing');

    // Once the winning same-scope snapshot says the worker returned it to
    // pending, the recruiter can act again.
    rerenderHome({ decisionRevision: 10, decisionRevisionScopeKey: 'scope:all' });
    await waitFor(() => expect(row).not.toHaveClass('is-processing'));
  });

  it('hands an ambiguous modal advance to the queue lock before closing', async () => {
    const advance = { ...mkDecision(1, 'Miguel Parracho'), decision_type: 'advance_to_interview' };
    const pending = mkDecision(2, 'Ada Lovelace');
    approveDecision.mockRejectedValue({ code: 'ETIMEDOUT' });
    listDecisions.mockResolvedValue({ data: [{ ...advance, status: 'pending' }] });
    const reload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'superseded',
      ticket: 11,
      scopeKey: 'scope:all',
    });
    const { container } = renderHome({
      decisions: [advance, pending],
      pendingOrdered: [advance, pending],
      reload,
    });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container).getByRole('button', { name: /advance to next stage/i }));
    const dialog = within(container).getByRole('dialog', { name: /advance miguel parracho/i });
    fireEvent.click(within(dialog).getByRole('button', { name: /^advance$/i }));

    await waitFor(() => expect(reload).toHaveBeenCalled());
    await waitFor(() => expect(within(container).queryByRole('dialog')).not.toBeInTheDocument());
    const row = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(row).toHaveClass('is-processing');
    expect(within(container).getByText(/Decision is processing/i)).toBeInTheDocument();
    expect(within(container).queryByRole('button', { name: /advance to next stage/i }))
      .not.toBeInTheDocument();
    expect(within(container).getByRole('button', { name: /approve 1 visible/i }))
      .toBeInTheDocument();
    expect(approveDecision).toHaveBeenCalledOnce();
  });

  it('uses the current scope reload for the approve keyboard shortcut', async () => {
    approveDecision.mockResolvedValue({ data: { decision_id: 1, accepted: true } });
    const oldReload = vi.fn();
    const currentReload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'superseded',
      ticket: 5,
      scopeKey: 'scope:role-53',
    });
    const { container, rerenderHome } = renderHome({ reload: oldReload });

    rerenderHome({
      filters: { status: 'pending', role_id: 53, type: null, q: null },
      decisionScopeKey: 'scope:role-53',
      reload: currentReload,
    });
    fireEvent.keyDown(document, { key: 'a' });

    await waitFor(() => expect(currentReload).toHaveBeenCalledOnce());
    expect(oldReload).not.toHaveBeenCalled();
    expect(within(sidebarOf(container)).getByText('Miguel Parracho').closest('.rq-qrow'))
      .toHaveClass('is-processing');
  });

  it('keeps an in-flight decision locked across a Home remount', async () => {
    let resolveApprove;
    approveDecision.mockImplementation(() => new Promise((resolve) => { resolveApprove = resolve; }));
    const reload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'superseded',
      ticket: 21,
      scopeKey: 'scope:all',
    });
    const first = renderHome({ reload });
    fireEvent.click(within(first.container.querySelector('.rq-hybrid-detail'))
      .getByRole('button', { name: /send assessment/i }));
    expect(within(sidebarOf(first.container)).getByText('Miguel Parracho').closest('.rq-qrow'))
      .toHaveClass('is-processing');
    first.unmount();

    const second = renderHome({ reload });
    const sidebar = sidebarOf(second.container);
    const remountedRow = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(remountedRow).toHaveClass('is-processing');
    expect(within(second.container).getByText(/Decision is processing/i)).toBeInTheDocument();
    expect(within(second.container).queryByRole('button', { name: /send assessment/i }))
      .not.toBeInTheDocument();
    expect(within(second.container).getByRole('button', { name: /approve 1 visible/i }))
      .toBeInTheDocument();

    await act(async () => { resolveApprove({ data: { decision_id: 1 } }); });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it('keeps backend processing rows visible, greyed, and read-only', () => {
    const processing = { ...mkDecision(1, 'Miguel Parracho'), status: 'processing' };
    const pending = mkDecision(2, 'Ada Lovelace');
    const { container } = renderHome({
      decisions: [processing, pending],
      pendingOrdered: [processing, pending],
      selectedId: 1,
    });
    const sidebar = sidebarOf(container);

    const processingRow = within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow');
    expect(processingRow).toHaveClass('is-processing');
    expect(within(sidebar).getByText('Ada Lovelace')).toBeInTheDocument();
    expect(sidebar.querySelectorAll('.rq-qrow')[0]).toBe(processingRow);
    expect(within(container).getByText(/Decision is processing/i)).toBeInTheDocument();
    expect(within(container).getByRole('heading', { name: /Miguel Parracho/i })).toBeInTheDocument();
    expect(within(container).queryByRole('button', { name: /send assessment/i })).not.toBeInTheDocument();
    expect(within(container).getByRole('button', { name: /approve 1 visible/i })).toBeInTheDocument();
  });

  it('retains the lock when the server confirms processing, protecting stale scope rows', async () => {
    approveDecision.mockResolvedValue({ data: { decision_id: 1, accepted: true } });
    const reload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'superseded',
      ticket: 6,
      scopeKey: 'scope:all',
    });
    const { container, rerenderHome } = renderHome({ reload });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container.querySelector('.rq-hybrid-detail'))
      .getByRole('button', { name: /send assessment/i }));
    await waitFor(() => expect(reload).toHaveBeenCalled());

    const processing = { ...mkDecision(1, 'Miguel Parracho'), status: 'processing' };
    rerenderHome({
      decisions: [processing, mkDecision(2, 'Ada Lovelace')],
      pendingOrdered: [processing, mkDecision(2, 'Ada Lovelace')],
      decisionRevision: 7,
      decisionRevisionScopeKey: 'scope:all',
    });
    expect(within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow'))
      .toHaveClass('is-processing');

    // A cached row from another scope may still say pending, but the retained
    // processing tombstone keeps it read-only until that scope revalidates.
    rerenderHome({
      decisionScopeKey: 'scope:other',
      decisionRevision: 0,
      decisionRevisionScopeKey: 'scope:other',
      decisions: [mkDecision(1, 'Miguel Parracho')],
      pendingOrdered: [mkDecision(1, 'Miguel Parracho')],
    });
    expect(within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow'))
      .toHaveClass('is-processing');
  });

  it('greys every bulk "Skip & advance" row immediately and restores explicit failures', async () => {
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
    expect(bulkOverrideDecisions).toHaveBeenCalledWith([1, 2], 'skip_assessment_advance');
    // Optimistic: both rows remain visible but grey while the request is in
    // flight (promise still pending, reload not yet awaited).
    expect(within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow')).toHaveClass('is-processing');
    expect(within(sidebar).getByText('Ada Lovelace').closest('.rq-qrow')).toHaveClass('is-processing');
    expect(reload).not.toHaveBeenCalled();

    await act(async () => {
      resolveBulk({
        data: {
          requested: 2,
          accepted: 1,
          failures: [{ decision_id: 2, error: 'not found' }],
        },
      });
    });
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow')).toHaveClass('is-processing');
    expect(within(sidebar).getByText('Ada Lovelace').closest('.rq-qrow')).not.toHaveClass('is-processing');
  });

  it('keeps accepted bulk approvals protected while restoring only failed IDs', async () => {
    let resolveBulk;
    bulkApproveDecisions.mockImplementation(() => new Promise((resolve) => { resolveBulk = resolve; }));

    const reload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'superseded',
      ticket: 12,
      scopeKey: 'scope:all',
    });
    const { container } = renderHome({ reload });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container).getByRole('button', { name: /approve 2 visible/i }));
    fireEvent.click(within(container).getByRole('button', { name: /^approve 2$/i }));

    expect(bulkApproveDecisions).toHaveBeenCalledWith([1, 2], null, null);
    expect(within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow')).toHaveClass('is-processing');
    expect(within(sidebar).getByText('Ada Lovelace').closest('.rq-qrow')).toHaveClass('is-processing');

    await act(async () => {
      resolveBulk({
        data: {
          requested: 2,
          accepted: 1,
          failures: [{ decision_id: 2, error: 'already processing' }],
        },
      });
    });

    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(within(sidebar).getByText('Miguel Parracho').closest('.rq-qrow')).toHaveClass('is-processing');
    expect(within(sidebar).getByText('Ada Lovelace').closest('.rq-qrow')).not.toHaveClass('is-processing');
  });

  it('keeps an outcome-unknown bulk rejection protected across later pending snapshots', async () => {
    bulkApproveDecisions.mockRejectedValue(new Error('connection reset'));
    const reload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'error',
      ticket: 15,
      scopeKey: 'scope:all',
    });
    const { container, rerenderHome } = renderHome({ reload });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container).getByRole('button', { name: /approve 2 visible/i }));
    fireEvent.click(within(container).getByRole('button', { name: /^approve 2$/i }));

    await waitFor(() => expect(reload).toHaveBeenCalled());
    const rows = [...sidebar.querySelectorAll('.rq-qrow')];
    rows.forEach((row) => expect(row).toHaveClass('is-processing'));

    rerenderHome({ decisionRevision: 16, decisionRevisionScopeKey: 'scope:all' });
    rows.forEach((row) => expect(row).toHaveClass('is-processing'));
    rerenderHome({ decisionRevision: 99, decisionRevisionScopeKey: 'scope:all' });
    rows.forEach((row) => expect(row).toHaveClass('is-processing'));
  });

  it('keeps an outcome-unknown bulk skip protected across later pending snapshots', async () => {
    bulkOverrideDecisions.mockRejectedValue(new Error('connection reset'));
    const reload = vi.fn().mockResolvedValue({
      applied: false,
      reason: 'error',
      ticket: 17,
      scopeKey: 'scope:all',
    });
    const { container, rerenderHome } = renderHome({
      filters: { status: 'pending', role_id: null, type: 'assessment', q: null },
      reload,
    });
    const sidebar = sidebarOf(container);

    fireEvent.click(within(container).getByRole('button', { name: /skip & advance 2 visible/i }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    const rows = [...sidebar.querySelectorAll('.rq-qrow')];
    rows.forEach((row) => expect(row).toHaveClass('is-processing'));
    expect(within(container).queryByRole('button', { name: /skip & advance \d+ visible/i }))
      .not.toBeInTheDocument();

    rerenderHome({ decisionRevision: 18, decisionRevisionScopeKey: 'scope:all' });
    rows.forEach((row) => expect(row).toHaveClass('is-processing'));
    rerenderHome({ decisionRevision: 99, decisionRevisionScopeKey: 'scope:all' });
    rows.forEach((row) => expect(row).toHaveClass('is-processing'));
  });

  it('clamps rows to the Send filter during revalidation, so the bulk count matches the screen', () => {
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
    const sidebar = sidebarOf(container);
    expect(within(sidebar).getByText('Sandy Sender')).toBeInTheDocument();
    expect(within(sidebar).queryByText('Andy Advancer')).not.toBeInTheDocument();
    expect(within(container).getByRole('button', { name: /skip & advance 1 visible/i })).toBeInTheDocument();
  });

  it('hides bulk "Skip & advance" outside the Send view — a mixed queue can\'t show which cards it targets', () => {
    // Same assessment decisions, but the type filter is All (null): the bulk
    // approve stays, the bulk skip & advance is gone. (The per-card
    // "Skip & advance" override in the detail pane is unaffected, so match
    // the bulk button's "N visible" wording.)
    const { container } = renderHome();
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
  it('shows only the selected role’s candidates even when the parent still holds another role’s rows', () => {
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
    const sidebar = sidebarOf(container);

    expect(within(sidebar).getByText('Ada AiEngineer')).toBeInTheDocument();
    expect(within(sidebar).queryByText('Glen Glue')).not.toBeInTheDocument();
    expect(within(sidebar).queryByText('Cleo Cloud')).not.toBeInTheDocument();
    // The queue count reflects the scoped set, not the stale all-roles list.
    expect(within(sidebar).getByText('1')).toBeInTheDocument();
  });

  it('shows every role’s candidates when no role filter is set', () => {
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
    const sidebar = sidebarOf(container);

    expect(within(sidebar).getByText('Ada AiEngineer')).toBeInTheDocument();
    expect(within(sidebar).getByText('Glen Glue')).toBeInTheDocument();
  });
});
