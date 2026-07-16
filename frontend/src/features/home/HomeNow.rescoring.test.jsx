import { act, fireEvent, render, within } from '@testing-library/react';
import { vi } from 'vitest';

import { HomeNow } from './HomeNow';

// Re-evaluating an old-engine score enqueues an async re-score and the
// decision STAYS in the queue until the fresh score lands. These tests pin
// the in-flight treatment: the row + card grey out (is-processing /
// is-rescoring), a "re-scoring" indicator shows, and actions freeze — both
// when the server reports rescore_in_flight and optimistically the instant
// Re-evaluate is clicked.

const reEvaluateDecision = vi.fn();
const listDecisions = vi.fn().mockResolvedValue({ data: [] });

vi.mock('../../shared/api', () => ({
  agent: {
    approveDecision: vi.fn().mockResolvedValue({ data: {} }),
    bulkApproveDecisions: vi.fn().mockResolvedValue({ data: {} }),
    bulkOverrideDecisions: vi.fn().mockResolvedValue({ data: {} }),
    snoozeDecision: vi.fn().mockResolvedValue({ data: {} }),
    reEvaluateDecision: (...a) => reEvaluateDecision(...a),
    listDecisions: (...a) => listDecisions(...a),
  },
  organizations: {
    getWorkableStages: vi.fn().mockResolvedValue({ data: { stages: [] } }),
  },
}));

const mkDecision = (id, name, extra = {}) => ({
  id,
  decision_type: 'send_assessment',
  status: 'pending',
  candidate_name: name,
  candidate_email: `${name.split(' ')[0].toLowerCase()}@example.com`,
  application_id: id * 10,
  role_id: 53,
  role_name: 'Data Modeler',
  created_at: '2026-06-07T10:00:00Z',
  reasoning: 'Strong fit.',
  taali_score: 66,
  ...extra,
});

const renderHome = (decisions) => {
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
    />,
  );
  return { ...utils, reload };
};

describe('HomeNow — re-score in flight', () => {
  beforeEach(() => {
    reEvaluateDecision.mockReset();
    listDecisions.mockReset().mockResolvedValue({ data: [] });
  });

  it('greys the queue row + card and freezes actions when the server reports rescore_in_flight', async () => {
    const { container } = renderHome([
      mkDecision(1, 'Mohd Hashimi', { rescore_in_flight: true }),
      mkDecision(2, 'Sathish Kumar'),
    ]);
    await act(async () => {
      await listDecisions.mock.results.at(-1).value;
    });

    // Queue row: greyed + a spinning "re-scoring" chip.
    const rows = container.querySelectorAll('.rq-qrow');
    expect(rows[0].className).toContain('is-processing');
    expect(within(rows[0]).getByText('re-scoring')).toBeInTheDocument();
    expect(rows[1].className).not.toContain('is-processing');

    // Detail card: greyed, banner explains, primary action frozen.
    const detail = container.querySelector('.rq-hybrid-detail');
    expect(detail.className).toContain('is-rescoring');
    expect(within(detail).getByText(/re-scoring this candidate/i)).toBeInTheDocument();
    expect(within(detail).getByRole('button', { name: /send assessment/i })).toBeDisabled();
    expect(within(detail).getByRole('button', { name: /snooze/i })).toBeDisabled();
  });

  it('greys the card the instant Re-evaluate is clicked — before the network resolves', async () => {
    let resolveReEval;
    reEvaluateDecision.mockImplementation(() => new Promise((r) => { resolveReEval = r; }));

    const { container, reload } = renderHome([
      mkDecision(1, 'Mohd Hashimi', {
        is_stale: true,
        staleness_reasons: ['engine_outdated'],
        staleness_summary: 'Scored by an older model',
      }),
    ]);

    const detail = container.querySelector('.rq-hybrid-detail');
    expect(detail.className).not.toContain('is-rescoring');

    const reEvalBtn = within(detail).getByRole('button', { name: /re-evaluate/i });
    await act(async () => { fireEvent.click(reEvalBtn); });

    // Request fired, promise still pending — the card is already greyed.
    expect(reEvaluateDecision).toHaveBeenCalledWith(1);
    expect(container.querySelector('.rq-hybrid-detail').className).toContain('is-rescoring');
    expect(container.querySelector('.rq-qrow').className).toContain('is-processing');
    expect(reload).not.toHaveBeenCalled();

    // Settle: server confirms; the optimistic mark is dropped after reload
    // (the server's rescore_in_flight flag owns the state from here).
    await act(async () => { resolveReEval({ data: { queued: true } }); });
    expect(reload).toHaveBeenCalled();
    expect(container.querySelector('.rq-hybrid-detail').className).not.toContain('is-rescoring');
  });
});
