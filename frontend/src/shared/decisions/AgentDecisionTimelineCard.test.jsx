import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDecisionTimelineCard } from './AgentDecisionTimelineCard';

const mocks = vi.hoisted(() => ({
  approveDecision: vi.fn(),
  listDecisions: vi.fn(),
  showToast: vi.fn(),
}));

vi.mock('../api', () => ({
  agent: {
    approveDecision: (...args) => mocks.approveDecision(...args),
    listDecisions: (...args) => mocks.listDecisions(...args),
  },
  organizations: {
    getWorkableStages: vi.fn(),
  },
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast: mocks.showToast }),
}));

vi.mock('./AgentDecisionCard', () => ({
  AgentDecisionCard: ({ decision, onApprove }) => (
    decision.status === 'processing'
      ? <div role="status">Decision processing</div>
      : <button type="button" onClick={() => onApprove(decision)}>Approve test decision</button>
  ),
}));

vi.mock('../../features/home/OverrideModal', () => ({ OverrideModal: () => null }));
vi.mock('../../features/home/TeachModal', () => ({ TeachModal: () => null }));

const renderDecision = (stalenessReasons, overrides = {}) => render(
  <AgentDecisionTimelineCard
    item={{ decision_id: 17, decision_type: 'reject' }}
    detail={{
      id: 17,
      application_id: 23,
      role_id: 31,
      status: 'pending',
      decision_type: 'reject',
      is_stale: true,
      staleness_reasons: stalenessReasons,
      ...overrides.detail,
    }}
    roleId={31}
    roleName="Platform Engineer"
    onChanged={overrides.onChanged || vi.fn()}
  />,
);

beforeEach(() => {
  mocks.approveDecision.mockReset().mockResolvedValue({ data: { decision_id: 17 } });
  mocks.listDecisions.mockReset().mockResolvedValue({ data: [] });
  mocks.showToast.mockReset();
});

describe('AgentDecisionTimelineCard approval authorization', () => {
  it('forces only the bounded old-engine approval', async () => {
    renderDecision(['engine_outdated']);

    fireEvent.click(screen.getByRole('button', { name: /Approve test decision/i }));

    await waitFor(() => {
      expect(mocks.approveDecision).toHaveBeenCalledWith(17, {}, { force: true });
    });
  });

  it('does not submit when any cited input changed', () => {
    renderDecision(['engine_outdated', 'score_generation_changed']);

    fireEvent.click(screen.getByRole('button', { name: /Approve test decision/i }));

    expect(mocks.approveDecision).not.toHaveBeenCalled();
    expect(mocks.showToast).toHaveBeenCalledWith(
      'This decision’s inputs changed — re-evaluate before approving.',
      'warning',
    );
  });

  it('keeps an outcome-unknown reverted decision read-only until a causal transition', async () => {
    const reverted = {
      id: 17,
      application_id: 23,
      role_id: 31,
      status: 'reverted_for_feedback',
      decision_type: 'reject',
      is_stale: false,
      staleness_reasons: [],
    };
    mocks.approveDecision.mockRejectedValueOnce({ code: 'ETIMEDOUT' });
    mocks.listDecisions.mockResolvedValueOnce({ data: [reverted] });

    renderDecision([], { detail: reverted });
    fireEvent.click(screen.getByRole('button', { name: /Approve test decision/i }));

    expect(await screen.findByRole('status')).toHaveTextContent('Decision processing');
    expect(screen.queryByRole('button', { name: /Approve test decision/i }))
      .not.toBeInTheDocument();
    expect(mocks.approveDecision).toHaveBeenCalledOnce();
    expect(mocks.listDecisions).toHaveBeenCalledWith(
      { application_id: 23, status: 'current', limit: 50 },
      { timeout: 10000 },
    );
    expect(mocks.showToast).toHaveBeenCalledWith(
      "We couldn't confirm this action. Refresh before taking another action.",
      'warning',
    );
  });
});
