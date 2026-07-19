import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
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

  it('keeps a processing decision visible and read-only', () => {
    renderRail({ decision: { ...baseDecision, status: 'processing' } });

    expect(screen.getByRole('status')).toHaveTextContent(/Processing/i);
    expect(screen.getByText(/Accepted — actions are read-only/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Advance|Approve/i })).not.toBeInTheDocument();
  });
});

describe('DecisionRail entrance motion', () => {
  it('uses the shared horizontal Motion reveal for the rail', () => {
    const { container } = renderRail();
    const rail = container.querySelector('.dossier-rail');
    expect(rail).not.toBeNull();
    expect(rail).toHaveAttribute('data-motion-reveal', 'horizontal');
    expect(rail).not.toHaveClass('dr-reveal');
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

describe('DecisionRail recommendation attribution', () => {
  it('labels deterministic recommendations as policy with a rule chip and no model confidence', () => {
    renderRail({
      decision: {
        ...baseDecision,
        confidence: 1,
        decision_explanation: {
          source: 'policy',
          rule: 'role_fit_score >= role_fit_min',
          summary: 'Advance recommended.',
          score_context: { role_fit_score: 72, threshold: 55, threshold_passed: true },
        },
      },
    });
    expect(screen.getByText('Policy')).toBeInTheDocument();
    expect(screen.getByText('72 ≥ 55')).toBeInTheDocument();
    expect(screen.queryByText(/Confidence 100%/i)).not.toBeInTheDocument();
    // The rail carries no prose — the full explanation lives in the report body.
    expect(screen.queryByText('Advance recommended.')).not.toBeInTheDocument();
  });

  it('shows the agent confidence chip and no "recommends" prose', () => {
    renderRail({
      decision: {
        ...baseDecision,
        confidence: 0.9,
        decision_explanation: { source: 'agent', summary: 'Advance recommended.' },
      },
    });
    expect(screen.getByText('Agent')).toBeInTheDocument();
    expect(screen.getByText('Confidence 90%')).toBeInTheDocument();
    expect(screen.queryByText(/recommends/i)).not.toBeInTheDocument();
  });
});

describe('DecisionRail reject consequence copy', () => {
  const rejectDecision = {
    id: 9,
    status: 'pending',
    decision_type: 'reject',
    confidence: 0.8,
    role_family: {
      owner: { id: 31, name: 'AI Engineer' },
      related: [{ id: 135, name: 'AI Platform Engineer' }],
    },
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
    expect(reject).toHaveAttribute('title', expect.stringMatching(/AI Engineer #31 \(original\).*AI Platform Engineer #135 \(related\)/i));
    expect(screen.getByText(/AI Engineer #31 \(original\).*AI Platform Engineer #135 \(related\)/i)).toBeInTheDocument();
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
    expect(screen.queryByText(/Rejects the shared ATS application/i)).not.toBeInTheDocument();
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
    expect(screen.getByText(/AI Engineer #31 \(original\).*AI Platform Engineer #135 \(related\)/i)).toBeInTheDocument();
  });
});

describe('DecisionRail pre-screen escalation', () => {
  const preScreenApp = { cv_match_score: null, workable_stage: null };

  it('renders the pre-screen context + Run full evaluation action, not the generic hint', () => {
    render(
      <DecisionRail
        candidateName="Sam Patel"
        candidateInitials="SP"
        taaliScore={null}
        application={preScreenApp}
        canDecide
        preScreenedOut
        preScreenScore={31}
        preScreenReason="Missing the core Kubernetes requirement."
        onRunFullEvaluation={vi.fn()}
      />,
    );
    expect(screen.getByText(/Filtered out by pre-screen · 31\/100/i)).toBeInTheDocument();
    expect(screen.getByText(/Missing the core Kubernetes requirement\./i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run full evaluation/i })).toBeInTheDocument();
    // The generic "score this candidate" hint is suppressed — the pre-screen
    // block replaces it.
    expect(screen.queryByText(/No agent decision yet/i)).not.toBeInTheDocument();
  });

  it('fires onRunFullEvaluation when the action is clicked', () => {
    const onRun = vi.fn();
    render(
      <DecisionRail
        candidateName="Sam Patel"
        candidateInitials="SP"
        taaliScore={null}
        application={preScreenApp}
        canDecide
        preScreenedOut
        onRunFullEvaluation={onRun}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Run full evaluation/i }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it('shows the in-flight state and disables the action while evaluating', () => {
    render(
      <DecisionRail
        candidateName="Sam Patel"
        candidateInitials="SP"
        taaliScore={null}
        application={preScreenApp}
        canDecide
        preScreenedOut
        evaluating
        onRunFullEvaluation={vi.fn()}
      />,
    );
    const btn = screen.getByRole('button', { name: /Evaluating/i });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/Running a full CV evaluation now/i)).toBeInTheDocument();
  });

  it('does not render the pre-screen block for a client / interview view', () => {
    render(
      <DecisionRail
        candidateName="Sam Patel"
        candidateInitials="SP"
        taaliScore={null}
        application={preScreenApp}
        canDecide={false}
        preScreenedOut
        onRunFullEvaluation={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: /Run full evaluation/i })).not.toBeInTheDocument();
  });
});
