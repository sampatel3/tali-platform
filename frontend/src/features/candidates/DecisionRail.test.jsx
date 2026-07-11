import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { DecisionRail } from './DecisionRail';

// The candidate-report rail must mirror the hub's re-score/stale guarding
// (PR 872): while a decision is re-scoring, actions freeze and a status
// banner shows; a stale decision warns before the one-click approve. Neither
// should ever hard-block — Taali advises, never refuses.
const baseDecision = {
  id: 7,
  status: 'pending',
  decision_type: 'advance_to_interview',
  confidence: 0.9,
};

const baseApplication = { cv_match_score: 72, workable_stage: null };

const renderRail = (overrides = {}) => render(
  <DecisionRail
    candidateName="Sam Patel"
    candidateInitials="SP"
    taaliScore={72}
    decision={baseDecision}
    application={baseApplication}
    canDecide
    onApprove={vi.fn()}
    onReEvaluate={vi.fn()}
    {...overrides}
  />
);

describe('DecisionRail re-score / staleness guarding', () => {
  it('freezes the approve action and shows a banner while re-scoring', () => {
    renderRail({ decision: { ...baseDecision, rescore_in_flight: true } });
    // The re-scoring status banner is visible…
    expect(screen.getByText(/Re-scoring this candidate/i)).toBeInTheDocument();
    // …and the primary approve button is disabled (frozen).
    const approve = screen.getByRole('button', { name: /Advance|Approve/i });
    expect(approve).toBeDisabled();
  });

  it('warns on a stale decision but keeps the approve button live', () => {
    renderRail({ decision: { ...baseDecision, is_stale: true } });
    expect(screen.getByText(/Inputs changed since this was decided/i)).toBeInTheDocument();
    const approve = screen.getByRole('button', { name: /Advance|Approve/i });
    // Advice, never a block — the button stays actionable.
    expect(approve).not.toBeDisabled();
  });

  it('surfaces the old-engine copy when staleness is engine-only', () => {
    renderRail({
      decision: { ...baseDecision, is_stale: true, staleness_reasons: ['engine_outdated'] },
    });
    expect(screen.getByText(/older version of Taali/i)).toBeInTheDocument();
  });

  it('leaves the approve button live when nothing is stale or re-scoring', () => {
    renderRail();
    expect(screen.queryByText(/Re-scoring this candidate/i)).not.toBeInTheDocument();
    const approve = screen.getByRole('button', { name: /Advance|Approve/i });
    expect(approve).not.toBeDisabled();
  });
});

describe('DecisionRail score ring', () => {
  it('reads "—" (not 0/100) when the Taali score is unscored', () => {
    render(
      <DecisionRail candidateName="Sam Patel" candidateInitials="SP" taaliScore={null} />,
    );
    // The ring's accessible label mirrors the "—" override, not "0 of 100".
    expect(screen.getByLabelText(/—/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/0 of 100/)).not.toBeInTheDocument();
  });
});

describe('DecisionRail reject consequence copy', () => {
  const rejectDecision = {
    id: 9,
    status: 'pending',
    decision_type: 'reject',
    confidence: 0.8,
  };

  it('shows the consequence note under the primary reject button', () => {
    render(
      <DecisionRail
        candidateName="Sam Patel"
        candidateInitials="SP"
        taaliScore={40}
        decision={rejectDecision}
        application={{ cv_match_score: 40, workable_stage: null }}
        canDecide
        onApprove={vi.fn()}
      />,
    );
    const reject = screen.getByRole('button', { name: /Reject/i });
    expect(reject).toHaveAttribute('title', expect.stringMatching(/Disqualifies them in Workable/i));
    expect(screen.getByText(/Disqualifies them in Workable and sends the rejection email\./i)).toBeInTheDocument();
  });

  it('does not show the consequence note for a non-reject decision', () => {
    render(
      <DecisionRail
        candidateName="Sam Patel"
        candidateInitials="SP"
        taaliScore={72}
        decision={{ ...rejectDecision, decision_type: 'advance_to_interview' }}
        application={{ cv_match_score: 72, workable_stage: null }}
        canDecide
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Disqualifies them in Workable/i)).not.toBeInTheDocument();
  });

  it('lets the stale warning take precedence over the reject note in the button title', () => {
    render(
      <DecisionRail
        candidateName="Sam Patel"
        candidateInitials="SP"
        taaliScore={40}
        decision={{ ...rejectDecision, is_stale: true }}
        application={{ cv_match_score: 40, workable_stage: null }}
        canDecide
        onApprove={vi.fn()}
      />,
    );
    const reject = screen.getByRole('button', { name: /Reject/i });
    expect(reject).toHaveAttribute('title', expect.stringMatching(/Inputs changed since this was decided/i));
    // The visible consequence note still renders under the button.
    expect(screen.getByText(/Disqualifies them in Workable/i)).toBeInTheDocument();
  });
});
