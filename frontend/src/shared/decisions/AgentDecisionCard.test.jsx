// Post-handover reject warning contract.
//
// A reject-type card for a candidate already advanced in Workable (a live
// interview / offer stage — possibly moved there before the application
// entered Taali) must warn the recruiter BEFORE the one-click approve:
// approving disqualifies them in Workable. Advice, never a block — the
// approve button stays enabled. Non-reject cards and pre-handover
// candidates get no banner.

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AgentDecisionCard } from './AgentDecisionCard';

const noop = () => {};

const baseDecision = {
  id: 1,
  application_id: 7,
  role_id: 3,
  candidate_name: 'Tarig Elamin',
  status: 'pending',
  decision_type: 'reject',
  reasoning: 'Below the role-fit bar.',
  evidence: {},
};

const renderCard = (decision) => render(
  <AgentDecisionCard
    decision={decision}
    onApprove={noop}
    onAlternative={noop}
    onTeach={noop}
    onSnooze={noop}
    onNavigate={vi.fn()}
    busy={false}
  />,
);

describe('AgentDecisionCard post-handover warning', () => {
  it('warns on a reject for a candidate already advanced in Workable', () => {
    renderCard({
      ...baseDecision,
      candidate_post_handover: true,
      candidate_workable_stage: 'Technical Interview',
    });
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('Technical Interview');
    expect(alert.textContent).toContain('disqualify');
    // Advice, not a block: the approve button is still enabled.
    expect(screen.getByRole('button', { name: /reject/i })).toBeEnabled();
  });

  it('warns on a pre-screen reject too', () => {
    renderCard({
      ...baseDecision,
      decision_type: 'skip_assessment_reject',
      candidate_post_handover: true,
      candidate_workable_stage: 'Final Interview',
    });
    expect(screen.getByRole('alert').textContent).toContain('Final Interview');
  });

  it('shows no banner for a pre-handover candidate', () => {
    renderCard({ ...baseDecision, candidate_post_handover: false });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows no banner on a non-reject card even when post-handover', () => {
    renderCard({
      ...baseDecision,
      decision_type: 'advance_to_interview',
      candidate_post_handover: true,
      candidate_workable_stage: 'Technical Interview',
    });
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('AgentDecisionCard reject consequence copy', () => {
  // Parity with the candidate-report rail: a one-click reject must show what
  // confirming does (disqualify in Workable; the ATS — not Taali — emails).
  // Previously the hub card showed nothing.
  it('shows the shared reject consequence under the recommendation', () => {
    renderCard(baseDecision);
    expect(
      screen.getByText(/Disqualifies them in Workable\./i),
    ).toBeInTheDocument();
    const recommendation = screen.getByRole('button', { name: /reject/i });
    expect(recommendation)
      .toHaveAttribute('title', expect.stringMatching(/Disqualifies them in Workable/i));
    expect(recommendation).toHaveAttribute('data-motion-loop', 'flow');
    expect(recommendation).toHaveAttribute('data-motion-state', 'rest');
  });

  it('does not show the consequence on a non-reject card', () => {
    renderCard({ ...baseDecision, decision_type: 'advance_to_interview' });
    expect(screen.queryByText(/Disqualifies them in Workable/i)).not.toBeInTheDocument();
  });
});

describe('AgentDecisionCard button design-system contract', () => {
  it('renders report and pipeline navigation as canonical secondary links', () => {
    renderCard(baseDecision);

    const report = screen.getByRole('link', { name: 'Candidate report' });
    expect(report).toHaveAttribute('href', '/candidates/7?from=home');
    expect(report).toHaveAttribute('target', '_blank');
    expect(report).toHaveClass('taali-btn', 'taali-btn-secondary', 'taali-btn-sm');

    const pipeline = screen.getByRole('link', { name: 'Job pipeline' });
    expect(pipeline).toHaveAttribute('href', '/jobs/3');
    expect(pipeline).toHaveClass('taali-btn', 'taali-btn-secondary', 'taali-btn-sm');
  });

  it('uses the agent treatment for the recommendation and canonical support actions', () => {
    renderCard(baseDecision);

    expect(screen.getByRole('button', { name: 'Reject' }))
      .toHaveClass('taali-btn-agent', 'taali-btn-md', 'rq-rec-btn');
    expect(screen.getByRole('button', { name: 'Send assessment' }))
      .toHaveClass('taali-btn-secondary', 'taali-btn-sm');
    expect(screen.getByRole('button', { name: 'Advance instead' }))
      .toHaveClass('taali-btn-secondary', 'taali-btn-sm');
    expect(screen.getByRole('button', { name: 'Send back & teach' }))
      .toHaveClass('taali-btn-secondary', 'taali-btn-sm');
    expect(screen.getByRole('button', { name: 'Snooze 1h' }))
      .toHaveClass('taali-btn-ghost', 'taali-btn-sm');
  });
});
