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
