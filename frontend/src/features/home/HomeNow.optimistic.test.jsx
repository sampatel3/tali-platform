import { act, fireEvent, render, waitFor, within } from '@testing-library/react';
import { vi } from 'vitest';

import { HomeNow } from './HomeNow';

// Approving a decision is async server-side (the backend flips it to
// ``processing`` and runs the heavy send in a worker), so the Hub reflects the
// action OPTIMISTICALLY: the card leaves the queue the instant you click, and
// only reappears if the send actually fails. These tests pin that behaviour.

const approveDecision = vi.fn();
const bulkApproveDecisions = vi.fn();

vi.mock('../../shared/api', () => ({
  agent: {
    approveDecision: (...a) => approveDecision(...a),
    bulkApproveDecisions: (...a) => bulkApproveDecisions(...a),
    snoozeDecision: vi.fn().mockResolvedValue({ data: {} }),
    reEvaluateDecision: vi.fn().mockResolvedValue({ data: {} }),
  },
  organizations: {
    getWorkableStages: vi.fn().mockResolvedValue({ data: { stages: [] } }),
  },
}));

const mkDecision = (id, name) => ({
  id,
  decision_type: 'send_assessment',
  status: 'pending',
  candidate_name: name,
  candidate_email: `${name.split(' ')[0].toLowerCase()}@example.com`,
  application_id: id * 10,
  role_id: 53,
  role_name: 'Data Engineer',
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

    // The request fired with the focused decision...
    expect(approveDecision).toHaveBeenCalledTimes(1);
    expect(approveDecision).toHaveBeenCalledWith(1, {});
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
});
