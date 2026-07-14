import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { VerdictDetail } from './VerdictDetail';

describe('VerdictDetail', () => {
  it('separates the causal policy reason from the candidate summary', () => {
    render(
      <VerdictDetail
        decision={{
          reasoning: 'Legacy long-form reasoning.',
          candidate_summary: 'Strong AWS depth with a material AI/ML gap.',
          decision_explanation: {
            source: 'policy',
            summary: 'Reject recommended because 2 must-have requirements were marked missing.',
            context: 'The 72 role-fit score cleared the 55 threshold; the hard must-have rule took priority.',
            factors: [
              { label: 'Knowledge graph development', status: 'missing' },
              { label: 'Ontology design', status: 'missing' },
            ],
          },
          evidence: {
            policy_basis: 'role-fit 72 vs threshold 60 → send_assessment',
            rule_path: ['point:send_assessment', 'rule:skipped:must_have_blocked', 'rule:fired:role_fit_score >= role_fit_min'],
          },
        }}
      />
    );
    expect(screen.getByText(/Reject recommended because 2 must-have requirements/)).toBeTruthy();
    expect(screen.getByText('Knowledge graph development')).toBeTruthy();
    expect(screen.getByText(/72 role-fit score cleared the 55 threshold/)).toBeTruthy();
    expect(screen.getByText('Strong AWS depth with a material AI/ML gap.')).toBeTruthy();
    expect(screen.getByText('WHY THE POLICY RECOMMENDS THIS')).toBeTruthy();
    expect(screen.getByText('CANDIDATE SUMMARY')).toBeTruthy();
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

  it('keeps a readable fallback for legacy decisions', () => {
    render(<VerdictDetail decision={{ reasoning: 'Strong AWS, gaps in AI/ML.' }} />);
    expect(screen.getByText('Strong AWS, gaps in AI/ML.')).toBeTruthy();
    expect(screen.getByText('WHY THE AGENT RECOMMENDS THIS')).toBeTruthy();
  });

  it('renders nothing when there is no decision', () => {
    const { container } = render(<VerdictDetail />);
    expect(container.firstChild).toBeNull();
  });
});
