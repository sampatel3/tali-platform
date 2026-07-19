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
import { DecisionNarrative } from './DecisionNarrative';

const noop = () => {};

const baseDecision = {
  id: 1,
  application_id: 7,
  role_id: 3,
  candidate_name: 'Tarig Elamin',
  status: 'pending',
  decision_type: 'reject',
  role_family: {
    owner: { id: 31, name: 'Data Platform Lead' },
    related: [{ id: 47, name: 'AI Engineer' }],
  },
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

describe('AgentDecisionCard shared candidate-pool context', () => {
  it('labels the shared provider date as pool entry on a related-role card', () => {
    renderCard({
      ...baseDecision,
      applied_at: '2026-06-30T10:00:00Z',
      role_family: undefined,
      evidence: { shared_ats_application: true },
    });

    expect(screen.getByText(/In shared ATS pool since .*2026/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Applied .*2026/i)).not.toBeInTheDocument();
  });

  it('keeps Applied for an ordinary role application', () => {
    renderCard({
      ...baseDecision,
      role_id: 31,
      applied_at: '2026-06-30T10:00:00Z',
    });

    expect(screen.getByText(/^Applied .*2026/i)).toBeInTheDocument();
  });
});

describe('AgentDecisionCard reject consequence copy', () => {
  // Parity with the candidate-report rail: a one-click reject must show what
  // confirming does to the one shared ATS application and linked role family.
  // Previously the hub card showed nothing.
  it('names every linked role in the shared reject consequence', () => {
    renderCard(baseDecision);
    expect(
      screen.getByText(
        /Rejects the shared ATS application across all linked roles: Data Platform Lead #31 \(original\) and AI Engineer #47 \(related\)\./i,
      ),
    ).toBeInTheDocument();
    const recommendation = screen.getByRole('button', { name: /reject/i });
    expect(recommendation)
      .toHaveAttribute('title', expect.stringMatching(/Data Platform Lead #31.*AI Engineer #47/i));
    expect(recommendation).toHaveAttribute('data-motion-loop', 'flow');
    expect(recommendation).toHaveAttribute('data-motion-state', 'rest');
  });

  it('keeps the generic reject consequence when linked-role metadata is absent', () => {
    renderCard({ ...baseDecision, role_family: undefined });
    expect(
      screen.getByText(/Rejects this candidate's ATS application\. If this role shares a candidate pool/i),
    ).toBeInTheDocument();
  });

  it('passes role-specific shared-application copy into a reject confirmation', () => {
    const onAlternative = vi.fn();
    render(
      <AgentDecisionCard
        decision={{ ...baseDecision, decision_type: 'send_assessment' }}
        onApprove={noop}
        onAlternative={onAlternative}
        onTeach={noop}
        onSnooze={noop}
        busy={false}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /^Reject$/i }));
    expect(onAlternative).toHaveBeenCalledWith(
      expect.objectContaining({ id: baseDecision.id }),
      expect.objectContaining({
        action: 'reject',
        body: expect.stringMatching(/Data Platform Lead #31 \(original\).*AI Engineer #47 \(related\)/i),
      }),
    );
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

    // Factor chips render in the card narrative, alongside the compact
    // candidate overview (the full un-clamped summary lives on the report).
    expect(screen.getByText(/Knowledge graph development/)).toBeInTheDocument();
    expect(screen.getByText('CANDIDATE SUMMARY')).toBeInTheDocument();
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
    // The compact candidate overview renders under its own kicker, so the two
    // prose blocks stay distinguishable.
    expect(screen.getByText('Partial role fit.')).toBeInTheDocument();
    expect(screen.getByText('CANDIDATE SUMMARY')).toBeInTheDocument();
  });

  it('renders a 2-line clamp with a Show more toggle on long agent reasoning', () => {
    const longReason = 'Advance recommended — this candidate brings deep distributed-systems experience across a decade of '
      + 'high-scale platforms, with strong AWS depth and a proven verification habit, and the role-fit read clears '
      + 'the bar with room to spare on every must-have the role hinges on.';
    renderCard({
      ...baseDecision,
      decision_type: 'advance_to_interview',
      decision_explanation: { source: 'agent', summary: longReason, factors: [] },
    });

    // The agent reasoning clamps to 2 lines with a Show more/less toggle.
    expect(screen.getByText(longReason)).toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: 'Show more' });
    expect(toggle).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();
  });

  it('shows the compact candidate summary (pill + clamp) on card density', () => {
    const summary = 'Partial fit — strong AWS depth with a material knowledge-graph gap.';
    const decision = {
      ...baseDecision,
      candidate_summary: summary,
      decision_explanation: {
        source: 'policy',
        rule: 'must_have_blocked',
        summary: 'Reject recommended because 1 must-have requirement was marked missing.',
        factors: [{ label: 'Knowledge graph development', status: 'missing' }],
      },
    };

    const card = render(<DecisionNarrative decision={decision} density="card" />);
    expect(card.getByText('CANDIDATE SUMMARY')).toBeInTheDocument();
    expect(card.getByText('Partial fit')).toBeInTheDocument();
    expect(card.getByText(/material knowledge-graph gap/)).toBeInTheDocument();
    card.unmount();

    const report = render(<DecisionNarrative decision={decision} density="report" />);
    expect(report.getByText(/material knowledge-graph gap/)).toBeInTheDocument();
  });

  it('renders the candidate summary on a factorless policy card, but no reason block', () => {
    const { container, getByText, queryByText } = render(
      <DecisionNarrative
        decision={{
          ...baseDecision,
          candidate_summary: 'Clear misfit — signal-processing background with no LLM or RAG work.',
          decision_explanation: {
            source: 'policy',
            rule: 'pre_screen_auto_reject_eligible',
            summary: 'Reject recommended at pre-screen.',
            factors: [],
          },
        }}
        density="card"
      />,
    );
    expect(getByText('CANDIDATE SUMMARY')).toBeInTheDocument();
    expect(getByText(/signal-processing background/)).toBeInTheDocument();
    // The pending slab's chip + why? carry the policy cause on cards.
    expect(queryByText(/WHY THE POLICY RECOMMENDS THIS/i)).not.toBeInTheDocument();
    expect(container.firstChild).not.toBeNull();
  });

  it('renders nothing on card density with no explanation content and no summary', () => {
    const { container } = render(
      <DecisionNarrative
        decision={{
          ...baseDecision,
          reasoning: '',
          candidate_summary: null,
          decision_explanation: {
            source: 'policy',
            rule: 'pre_screen_auto_reject_eligible',
            summary: 'Reject recommended at pre-screen.',
            factors: [],
          },
        }}
        density="card"
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('keeps the policy cause visible on a resolved card, where no rec slab renders', () => {
    // Timeline / history surfaces render approved|processing cards: the
    // pending-only recommendation slab (chip + why?) is absent, so the
    // narrative itself must carry the causal sentence.
    renderCard({
      ...baseDecision,
      status: 'approved',
      decision_explanation: {
        source: 'policy',
        rule: 'role_fit_score >= role_fit_min',
        summary: 'Send an assessment recommended because the role-fit score of 72 clears the 55 threshold.',
        factors: [],
        score_context: { role_fit_score: 72, threshold: 55, threshold_passed: true },
      },
    });
    expect(screen.getByText(/WHY THE POLICY RECOMMENDS THIS/i)).toBeTruthy();
    expect(screen.getByText(/clears the 55 threshold/)).toBeTruthy();
  });

  it('does not duplicate the policy cause inline on a pending card (it lives behind why?)', () => {
    renderCard({
      ...baseDecision,
      status: 'pending',
      decision_explanation: {
        source: 'policy',
        rule: 'role_fit_score >= role_fit_min',
        summary: 'Send an assessment recommended because the role-fit score of 72 clears the 55 threshold.',
        factors: [],
        score_context: { role_fit_score: 72, threshold: 55, threshold_passed: true },
      },
    });
    expect(screen.queryByText(/WHY THE POLICY RECOMMENDS THIS/i)).toBeNull();
    expect(screen.getByRole('button', { name: /why\?/i })).toBeTruthy();
  });
});

describe('AgentDecisionCard button design-system contract', () => {
  it('renders report and pipeline navigation as canonical secondary links', () => {
    renderCard(baseDecision);

    const report = screen.getByRole('link', { name: 'Candidate report' });
    expect(report).toHaveAttribute('href', '/candidates/7?from=home&view_role_id=3');
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
