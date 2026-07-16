// Post-handover reject warning contract.
//
// A reject-type card for a candidate already advanced in Workable (a live
// interview / offer stage — possibly moved there before the application
// entered Taali) must warn the recruiter BEFORE the one-click approve:
// approving disqualifies them in Workable. Advice, never a block — the
// approve button stays enabled. Non-reject cards and pre-handover
// candidates get no banner.

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
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

describe('AgentDecisionCard decision narrative', () => {
  it('labels a policy recommendation with a rule chip, hides confidence, and gates the causal sentence behind why?', () => {
    renderCard({
      ...baseDecision,
      confidence: 1,
      candidate_summary: '18 years in Lakehouse and dimensional modelling. The material gap is unproven knowledge-graph delivery.',
      decision_explanation: {
        source: 'policy',
        rule: 'must_have_blocked',
        summary: 'Reject recommended because 2 must-have requirements were marked missing.',
        context: 'The 72 role-fit score cleared the 55 threshold; the hard must-have rule took priority.',
        factors: [
          { label: 'Knowledge graph development', status: 'missing' },
          { label: 'Ontology and taxonomy design', status: 'missing' },
        ],
        policy_revision_id: 7,
      },
    });

    // Kicker: "Policy" + the rule chip. No "recommends", no confidence text.
    expect(screen.getByText('Policy')).toBeInTheDocument();
    expect(screen.getByText('2 must-haves missing')).toBeInTheDocument();
    expect(screen.queryByText(/Confidence 100%/i)).not.toBeInTheDocument();

    // The causal sentence is collapsed until "why?" is clicked.
    expect(screen.queryByText(/2 must-have requirements were marked missing/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'why?' }));
    expect(screen.getByText(/2 must-have requirements were marked missing/i)).toBeInTheDocument();
    expect(screen.getByText(/policy revision #7/)).toBeInTheDocument();

    // Factor chips + the full candidate summary render in the card narrative.
    expect(screen.getByText(/Knowledge graph development/)).toBeInTheDocument();
    expect(screen.getByText(
      '18 years in Lakehouse and dimensional modelling. The material gap is unproven knowledge-graph delivery.',
    )).toBeInTheDocument();
  });

  it('surfaces confidence as the chip for genuine agent judgment', () => {
    renderCard({
      ...baseDecision,
      confidence: 0.84,
      decision_explanation: {
        source: 'agent',
        summary: 'Reject recommended after reviewing the conflicting evidence.',
        factors: [],
      },
      candidate_summary: 'Partial role fit.',
    });

    expect(screen.getByText('Agent')).toBeInTheDocument();
    expect(screen.getByText('Confidence 84%')).toBeInTheDocument();
    // Agent reasoning prints inline, so no redundant "why?" disclosure.
    expect(screen.queryByRole('button', { name: 'why?' })).not.toBeInTheDocument();
    expect(screen.getByText('Reject recommended after reviewing the conflicting evidence.')).toBeInTheDocument();
  });

  it('keeps an unverified must-have distinct from a confirmed miss', () => {
    renderCard({
      ...baseDecision,
      decision_explanation: {
        source: 'policy',
        rule: 'must_have_blocked',
        summary: 'Reject recommended because a must-have remains unverified.',
        factors: [{ label: 'Security clearance', status: 'unknown' }],
      },
    });

    expect(screen.getByLabelText('Security clearance: Unverified'))
      .toHaveTextContent('? Security clearance · unverified');
  });

  it.each(['approved', 'overridden'])('keeps policy rationale on a %s read-only card', (status) => {
    renderCard({
      ...baseDecision,
      status,
      decision_explanation: {
        source: 'policy',
        rule: 'role_fit_score <= role_fit_max',
        summary: 'Reject recommended because the role-fit score is at the maximum threshold.',
        context: 'The configured policy is authoritative for this decision.',
      },
    });

    expect(screen.queryByRole('button', { name: 'why?' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Why this decision' })).toHaveTextContent(
      'Reject recommended because the role-fit score is at the maximum threshold. '
      + 'The configured policy is authoritative for this decision.',
    );
  });

  it('keeps policy rationale when a pending card replaces the recommendation slab', () => {
    render(
      <AgentDecisionCard
        decision={{
          ...baseDecision,
          decision_explanation: {
            source: 'policy',
            rule: 'must_have_blocked',
            summary: 'Reject recommended because a must-have is missing.',
          },
        }}
        onApprove={noop}
        onAlternative={noop}
        onTeach={noop}
        onSnooze={noop}
        busy={false}
        middleSlot={<div>Assessment stage tracker</div>}
      />,
    );

    expect(screen.getByText('Assessment stage tracker')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'why?' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Why this decision' }))
      .toHaveTextContent('Reject recommended because a must-have is missing.');
  });

  it('renders a 2-line clamp with a Show more toggle on a long card summary', () => {
    const longSummary = 'Partial fit — this candidate brings deep distributed-systems experience across a decade of '
      + 'high-scale platforms, with strong AWS depth and a proven verification habit, but the knowledge-graph '
      + 'delivery the role hinges on is unproven and stays the material open question.';
    renderCard({
      ...baseDecision,
      decision_explanation: { source: 'policy', rule: 'must_have_blocked', factors: [] },
      candidate_summary: longSummary,
    });

    // Verdict pill split off the head; the body carries a Show more/less toggle.
    expect(screen.getByText('Partial fit')).toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: 'Show more' });
    expect(toggle).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();
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
