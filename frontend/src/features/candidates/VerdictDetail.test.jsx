import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { VerdictDetail } from './VerdictDetail';

describe('VerdictDetail', () => {
  it('renders the merged fit-summary: source heading, verdict pill, factor chips, causal reason and summary', () => {
    render(
      <VerdictDetail
        decision={{
          decision_type: 'reject',
          reasoning: 'Legacy long-form reasoning.',
          candidate_summary: 'Partial fit — strong AWS depth with a material AI/ML gap.',
          decision_explanation: {
            source: 'policy',
            rule: 'must_have_blocked',
            summary: 'Reject recommended because 2 must-have requirements were marked missing.',
            context: 'The 72 role-fit score cleared the 55 threshold; the hard must-have rule took priority.',
            factors: [
              { label: 'Knowledge graph development', status: 'missing' },
              { label: 'Ontology design', status: 'missing' },
            ],
            policy_revision_id: 42,
          },
          evidence: {
            policy_basis: 'role-fit 72 vs threshold 60 → send_assessment',
            rule_path: ['point:send_assessment', 'rule:skipped:must_have_blocked', 'rule:fired:role_fit_score >= role_fit_min'],
          },
        }}
      />
    );
    // Merged block: muted FIT SUMMARY kicker, not the old two-block kickers.
    expect(screen.getByText('FIT SUMMARY')).toBeTruthy();
    expect(screen.queryByText('WHY THE POLICY RECOMMENDS THIS')).toBeNull();
    // Verdict pill split off the candidate summary head.
    expect(screen.getByText('Partial fit')).toBeTruthy();
    // Heading rule chip: source · rule chip · decision type (underscores dropped).
    expect(screen.getByText('✦ Policy · 2 must-haves missing · reject')).toBeTruthy();
    // Factor chips (prefixed with a ✕).
    expect(screen.getByText(/Knowledge graph development/)).toBeTruthy();
    // Causal reason (summary + context) renders as the quiet note.
    expect(screen.getByText(/Reject recommended because 2 must-have requirements/)).toBeTruthy();
    expect(screen.getByText(/72 role-fit score cleared the 55 threshold/)).toBeTruthy();
    // Candidate summary body (verdict head removed, un-clamped in report density).
    expect(screen.getByText('strong AWS depth with a material AI/ML gap.')).toBeTruthy();
    // Provenance line.
    expect(screen.getByText(/policy revision #42/)).toBeTruthy();
  });

  it('does not expose raw deterministic rule codes', () => {
    render(
      <VerdictDetail
        decision={{
          reasoning: 'Strong AWS, gaps in AI/ML.',
          evidence: {
            policy_basis: 'role-fit 72 vs threshold 60 → send_assessment',
            rule_path: ['rule:fired:role_fit_score >= role_fit_min'],
          },
        }}
      />
    );
    expect(screen.queryByText(/role-fit 72 vs threshold 60/)).toBeNull();
    expect(screen.queryByText(/rule:fired/)).toBeNull();
  });

  it('keeps a readable fallback for legacy decisions (no structured explanation)', () => {
    render(<VerdictDetail decision={{ reasoning: 'Strong AWS, gaps in AI/ML.' }} />);
    expect(screen.getByText('Strong AWS, gaps in AI/ML.')).toBeTruthy();
    expect(screen.getByText('WHY THE AGENT RECOMMENDS THIS')).toBeTruthy();
  });

  it('renders nothing when there is no decision', () => {
    const { container } = render(<VerdictDetail />);
    expect(container.firstChild).toBeNull();
  });
});
